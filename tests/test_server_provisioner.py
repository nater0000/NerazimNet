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
