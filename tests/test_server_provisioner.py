"""Unit tests for ServerProvisioner — arch detection and extra_ports parsing."""
import pytest

from controllers.server_provisioner import ServerProvisioner


class FakeResult:
    def __init__(self, ok=True, stdout='', stderr=''):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr


class FakeConnection:
    """Records run/sudo commands; answers 'uname -m' with a canned arch."""

    def __init__(self, machine='x86_64', certbot_certificates=''):
        self.machine = machine
        self.certbot_certificates = certbot_certificates
        self.commands = []
        self.uploads = {}  # remote_path -> content

    def run(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd == 'uname -m':
            return FakeResult(stdout=f'{self.machine}\n')
        if '--version' in cmd:
            return FakeResult(ok=False)  # force install path
        return FakeResult()

    def sudo(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd.startswith('certbot certificates'):
            # Route sync's cert check pipes through grep; a wildcard lookup
            # doesn't. Answer each shape separately.
            if 'grep' in cmd:
                return FakeResult(ok=True)  # "certificate already exists"
            return FakeResult(stdout=self.certbot_certificates)
        return FakeResult()

    def put(self, file_obj, remote=None):
        self.uploads[remote] = file_obj.read().decode('utf-8')
        return FakeResult()


@pytest.fixture
def provisioner():
    return ServerProvisioner(host='10.0.0.1', admin_user='admin', frp_version='v0.71.0')


class TestInstallFrpsArch:
    def test_x86_64_downloads_amd64(self, provisioner):
        conn = FakeConnection('x86_64')
        assert provisioner._install_frps(conn)
        curl = next(cmd for cmd in conn.commands if 'curl' in cmd)
        assert 'frp_0.71.0_linux_amd64.tar.gz' in curl

    @pytest.mark.parametrize('machine', ['aarch64', 'arm64'])
    def test_arm_downloads_arm64(self, provisioner, machine):
        conn = FakeConnection(machine)
        assert provisioner._install_frps(conn)
        curl = next(cmd for cmd in conn.commands if 'curl' in cmd)
        assert 'frp_0.71.0_linux_arm64.tar.gz' in curl

    def test_unsupported_arch_fails_before_download(self, provisioner):
        conn = FakeConnection('riscv64')
        assert not provisioner._install_frps(conn)
        assert not any('curl' in cmd for cmd in conn.commands)
        assert any('riscv64' in msg for msg in provisioner.log_output)


class TestParseExtraPorts:
    def test_single_ports(self, provisioner):
        http, stream = provisioner._parse_extra_ports(
            '7880:localhost:7880, udp:7881:localhost:7881, raw:7882:localhost:7882')
        assert http == [7880]
        assert {'port': 7881, 'udp': True, 'range': False} in stream
        assert {'port': 7882, 'udp': False, 'range': False} in stream

    def test_udp_range(self, provisioner):
        http, stream = provisioner._parse_extra_ports(
            'udp:50000-50020:localhost:50000-50020')
        assert http == []
        assert stream == [{'port': '50000-50020', 'udp': True, 'range': True}]

    def test_http_range_rejected(self, provisioner):
        http, stream = provisioner._parse_extra_ports('8000-8010:localhost:8000-8010')
        assert http == [] and stream == []
        assert any('ranges' in msg for msg in provisioner.log_output)

    def test_invalid_spec_skipped(self, provisioner):
        http, stream = provisioner._parse_extra_ports('not-a-port')
        assert http == [] and stream == []


class TestNginxStreamTemplate:
    def test_range_uses_server_port_variable(self, provisioner):
        template = provisioner.jinja_env.get_template('nginx_stream.conf.j2')
        content = template.render(
            stream_ports=[{'port': '50000-50020', 'udp': True, 'range': True}],
            server_ip='')
        assert 'listen 50000-50020 udp;' in content
        assert 'proxy_pass 127.0.0.1:$server_port;' in content

    def test_single_port_unchanged(self, provisioner):
        template = provisioner.jinja_env.get_template('nginx_stream.conf.j2')
        content = template.render(
            stream_ports=[{'port': 7881, 'udp': True, 'range': False}],
            server_ip='')
        assert 'listen 7881 udp;' in content
        assert 'proxy_pass 127.0.0.1:7881;' in content


def _render_site(provisioner, **overrides):
    params = dict(hostname='app.example.com', remote_port=8443,
                  http_extra_ports=[], server_ip='1.2.3.4',
                  ssl_enabled=True, cert_name='app.example.com',
                  max_body_size='', proxy_timeout='',
                  allowed_ips=None, auth_file=None)
    params.update(overrides)
    return provisioner.jinja_env.get_template('nginx_site.conf.j2').render(**params)


class TestNginxSiteTemplate:
    def test_defaults_match_previous_behavior(self, provisioner):
        content = _render_site(provisioner)
        assert 'client_max_body_size' not in content
        assert 'auth_basic' not in content
        assert 'deny all' not in content
        assert 'listen 443 ssl;' in content
        assert 'return 301 https://' in content
        assert '/etc/letsencrypt/live/app.example.com/' in content

    def test_max_body_size_and_timeout(self, provisioner):
        content = _render_site(provisioner, max_body_size='100m', proxy_timeout='300')
        assert 'client_max_body_size 100m;' in content
        assert 'proxy_read_timeout 300s;' in content

    def test_access_controls_render(self, provisioner):
        content = _render_site(provisioner,
                               allowed_ips=['1.2.3.4', '10.0.0.0/8'],
                               auth_file='/etc/nginx/htpasswd-app.example.com')
        assert 'allow 1.2.3.4;' in content
        assert 'allow 10.0.0.0/8;' in content
        assert 'deny all;' in content
        assert 'auth_basic "Restricted";' in content
        assert 'auth_basic_user_file /etc/nginx/htpasswd-app.example.com;' in content

    def test_http_only_when_no_cert(self, provisioner):
        content = _render_site(provisioner, hostname='*.lab.example.com',
                               ssl_enabled=False)
        assert 'listen 443 ssl' not in content
        assert 'return 301' not in content
        assert 'ssl_certificate' not in content
        assert 'proxy_pass http://127.0.0.1:8443;' in content

    def test_wildcard_cert_name_override(self, provisioner):
        content = _render_site(provisioner, hostname='*.lab.example.com',
                               cert_name='lab.example.com')
        assert 'server_name *.lab.example.com;' in content
        assert '/etc/letsencrypt/live/lab.example.com/' in content


class TestParseAllowedIps:
    def test_valid_entries_normalized(self, provisioner):
        ips = provisioner._parse_allowed_ips('1.2.3.4, 10.0.0.0/8, ::1')
        assert ips == ['1.2.3.4', '10.0.0.0/8', '::1']

    def test_invalid_entries_skipped(self, provisioner):
        ips = provisioner._parse_allowed_ips('1.2.3.4, not-an-ip, 999.1.1.1')
        assert ips == ['1.2.3.4']
        assert any('not-an-ip' in m for m in provisioner.log_output)

    def test_empty_returns_none(self, provisioner):
        assert provisioner._parse_allowed_ips('') is None
        assert provisioner._parse_allowed_ips(' , ') is None


class TestWildcardCertDiscovery:
    CERTS = """  Certificate Name: lab.example.com
    Domains: *.lab.example.com
  Certificate Name: app.example.com
    Domains: app.example.com
"""

    def test_finds_covering_cert(self, provisioner):
        conn = FakeConnection(certbot_certificates=self.CERTS)
        assert provisioner._find_wildcard_cert(conn, '*.lab.example.com') == 'lab.example.com'

    def test_no_match_returns_none(self, provisioner):
        conn = FakeConnection(certbot_certificates=self.CERTS)
        assert provisioner._find_wildcard_cert(conn, '*.other.com') is None


class TestSyncSingleRoute:
    def _tunnel(self, **kw):
        t = {'hostname': 'app.example.com', 'remote_port': '8443',
             'extra_ports': ''}
        t.update(kw)
        return t

    def test_wildcard_without_cert_serves_http_only(self, provisioner):
        conn = FakeConnection()  # certbot certificates -> empty
        assert provisioner._sync_single_route(conn, self._tunnel(hostname='*.lab.example.com'), '1.2.3.4')
        site = next(v for k, v in conn.uploads.items() if 'wildcard.lab.example.com' in k)
        assert 'listen 443' not in site
        assert 'proxy_pass http://127.0.0.1:8443;' in site
        assert any('wildcard' in m.lower() for m in provisioner.log_output)

    def test_basic_auth_uploads_htpasswd(self, provisioner):
        conn = FakeConnection()
        tunnel = self._tunnel(auth_user='admin', auth_password='s3cret')
        assert provisioner._sync_single_route(conn, tunnel, '1.2.3.4')
        ht = next(v for k, v in conn.uploads.items() if 'htpasswd' in k)
        assert ht.startswith('admin:$apr1$')
        site = next(v for v in conn.uploads.values() if 'server_name app.example.com' in v)
        assert 'auth_basic_user_file /etc/nginx/htpasswd-app.example.com;' in site

    def test_conf_name_sanitizes_wildcard(self, provisioner):
        assert provisioner._conf_name('*.lab.example.com') == 'wildcard.lab.example.com'
        assert provisioner._conf_name('app.example.com') == 'app.example.com'
