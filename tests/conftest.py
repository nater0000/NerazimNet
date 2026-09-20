import os
import sys
import socket

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

FAKE_FRPC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fake_frpc.py')


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeController:
    """Minimal stand-in for the App controller surface TunnelManager uses."""

    def __init__(self):
        self.objects = {}
        self.credentials = {'frp_token': 'test-token-abc123'}
        self.device_id = 'device-self'
        self.refreshed = False

    def get_object_by_id(self, oid):
        return self.objects.get(oid)

    def get_automation_credentials(self):
        return self.credentials

    def get_my_device_id(self):
        return self.device_id

    def get_tunnels(self):
        return [o for o in self.objects.values() if o.get('_type') == 'tunnel']

    def get_client_name(self, device_id):
        return f"device-{device_id}"

    def refresh_dashboard(self):
        self.refreshed = True

    def after(self, _ms, fn):
        fn()


def make_server(oid='srv1', ip='10.0.0.1'):
    return {'id': oid, '_type': 'server', 'ip_address': ip, 'name': 'VPS One'}


def make_tunnel(tid='tun1', server_id='srv1', route_type='tunnel',
                remote_port='8443', local='localhost:3000', extra_ports='',
                device='device-self', hostname='app.example.com'):
    return {'id': tid, '_type': 'tunnel', 'server_id': server_id,
            'route_type': route_type, 'remote_port': remote_port,
            'local_destination': local, 'extra_ports': extra_ports,
            'client_device_id': device, 'hostname': hostname,
            'auto_start_on_device_ids': [device]}


@pytest.fixture
def manager(tmp_path):
    """TunnelManager with a temp config dir, stubbed service-ensure, no monitor."""
    from controllers.tunnel_manager import TunnelManager
    calls = {'ensure': []}
    tm = TunnelManager(
        FakeController(),
        frp_config_dir=str(tmp_path),
        ensure_daemon=lambda: calls['ensure'].append(True) or True,
        frpc_command=[sys.executable, FAKE_FRPC],
        start_monitor=False,
    )
    tm._test_calls = calls
    return tm
