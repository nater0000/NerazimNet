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

    def __init__(self, machine='x86_64'):
        self.machine = machine
        self.commands = []

    def run(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd == 'uname -m':
            return FakeResult(stdout=f'{self.machine}\n')
        if '--version' in cmd:
            return FakeResult(ok=False)  # force install path
        return FakeResult()

    def sudo(self, cmd, **kwargs):
        self.commands.append(cmd)
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
    def test_range_expands_to_per_port_blocks(self, provisioner):
        """Nginx has no `listen` range syntax — ranges render one block per port."""
        template = provisioner.jinja_env.get_template('nginx_stream.conf.j2')
        content = template.render(
            stream_ports=[{'port': 50000, 'udp': True},
                          {'port': 50001, 'udp': True}],
            server_ip='')
        assert 'listen 50000 udp;' in content
        assert 'listen 50001 udp;' in content
        assert 'proxy_pass 127.0.0.1:50000;' in content
        assert 'proxy_pass 127.0.0.1:50001;' in content

    def test_single_port_unchanged(self, provisioner):
        template = provisioner.jinja_env.get_template('nginx_stream.conf.j2')
        content = template.render(
            stream_ports=[{'port': 7881, 'udp': True}],
            server_ip='')
        assert 'listen 7881 udp;' in content
        assert 'proxy_pass 127.0.0.1:7881;' in content

    def test_tcp_and_server_ip_binding(self, provisioner):
        template = provisioner.jinja_env.get_template('nginx_stream.conf.j2')
        content = template.render(
            stream_ports=[{'port': 7881, 'udp': False}],
            server_ip='203.0.113.5')
        assert 'listen 203.0.113.5:7881;' in content
        assert ' udp;' not in content


class TestValidatePrincipal:
    def test_valid_usernames(self, provisioner):
        for name in ('root', 'ubuntu', 'deploy-user', 'a_b', 'admin$'):
            provisioner.admin_user = name
            assert provisioner._validate_principal(), name

    def test_injection_rejected(self, provisioner):
        for bad in ('root; rm -rf /', 'root\nALL ALL=(ALL) NOPASSWD: ALL',
                    'root ALL', '-x', '', None):
            provisioner.admin_user = bad
            assert not provisioner._validate_principal(), bad


class CapturingConnection(FakeConnection):
    """FakeConnection that records c.put() payloads and models certbot state:
    has-cert fails until an 'obtain' command has been issued."""

    def __init__(self):
        super().__init__()
        self.puts = []  # [(remote_path, content)]
        self._cert_obtained = False

    def run(self, cmd, **kwargs):
        self.commands.append(cmd)
        return FakeResult(ok=False)  # nothing is already configured

    def put(self, file_obj, remote):
        self.puts.append((remote, file_obj.read().decode('utf-8')))

    def sudo(self, cmd, **kwargs):
        self.commands.append(cmd)
        if 'obtain' in cmd:
            self._cert_obtained = True
        if 'has-cert' in cmd or 'certificates' in cmd:
            return FakeResult(ok=self._cert_obtained)
        return FakeResult()


class TestEnsureCertificate:
    def test_invalid_email_rejected_before_certbot(self, provisioner):
        conn = CapturingConnection()
        provisioner.certbot_email = 'a@b.c; rm -rf /'
        assert not provisioner._ensure_certificate(conn, 'app.example.com')
        assert not any('obtain' in cmd for cmd in conn.commands)
        assert not any('-m ' in cmd for cmd in conn.commands)

    def test_email_injection_into_wrapper_only(self, provisioner):
        """With a valid email the call goes through the wrapper."""
        conn = CapturingConnection()
        provisioner.certbot_email = 'user@example.com'
        assert provisioner._ensure_certificate(conn, 'app.example.com')
        assert any('nerazimnet-certbot obtain app.example.com user@example.com'
                   in cmd for cmd in conn.commands)


class TestSudoersScoping:
    def test_sudoers_has_no_unbounded_grants(self, provisioner):
        conn = CapturingConnection()
        provisioner.admin_user = 'admin'
        assert provisioner._grant_sudo_permissions(conn)
        sudoers = next(content for remote, content in conn.puts
                       if 'NOPASSWD' in content)
        assert 'admin ALL=(ALL) NOPASSWD:' in sudoers
        assert 'nerazimnet-certbot' in sudoers
        assert '/usr/bin/certbot' not in sudoers
        assert 'kill' not in sudoers
        assert 'lsof' not in sudoers
        assert '/etc/nginx/*' not in sudoers  # cp/chmod confined to managed paths


class TestUploadConfigStaging:
    def test_stage_path_is_randomized(self, provisioner):
        conn = CapturingConnection()
        provisioner._upload_config(conn, 'data', '/etc/nginx/x.conf')
        cp_cmds = [c for c in conn.commands if c.startswith('cp /tmp/nerazimnet-')]
        assert cp_cmds
        stage = cp_cmds[0].split()[1]
        assert stage != '/tmp/nerazimnet-x.conf'
        assert stage.startswith('/tmp/nerazimnet-x.conf.')
