"""Unit tests for TunnelManager — config generation, reconcile, state recovery."""
import json
import os
import sys
import time

import pytest

from conftest import FakeController, make_server, make_tunnel, FAKE_FRPC, free_port


def _toml_text(manager, server_id='srv1'):
    path = os.path.join(manager.frp_config_dir, f'frpc_{server_id}.toml')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class TestConfigGeneration:
    def test_builds_proxies_and_webserver(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'serverAddr = "10.0.0.1"' in content
        assert 'token = "tok"' in content
        assert 'protocol = "quic"' in content
        assert 'port = 7400' in content          # admin api port
        assert 'name = "tun1"' in content
        assert 'localPort = 3000' in content
        assert 'remotePort = 8443' in content

    def test_extra_ports_render_as_sibling_proxies(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(extra_ports='7880:localhost:7880, udp:7881:localhost:7881')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'name = "tun1-x7880"' in content
        assert 'name = "tun1-x7881"' in content
        assert 'type = "udp"' in content

    def test_local_route_needs_no_proxy(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel(route_type='local')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert '[[proxies]]' not in content

    def test_tunnel_for_other_device_is_skipped(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel(device='device-other')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert '[[proxies]]' not in content


class TestReconcile:
    def test_start_writes_toml_and_ensures_daemon(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        ok, _ = manager.start_tunnel('tun1')
        assert ok
        path = os.path.join(manager.frp_config_dir, 'frpc_srv1.toml')
        assert os.path.exists(path)
        assert manager._test_calls['ensure'], "daemon service was never ensured"
        # desired state persisted for reboot/daemon restore
        desired = json.load(open(manager._desired_state_path()))
        assert 'tun1' in desired['tunnels']

    def test_stop_removes_toml(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.start_tunnel('tun1')
        path = os.path.join(manager.frp_config_dir, 'frpc_srv1.toml')
        assert os.path.exists(path)
        manager.stop_tunnel('tun1')
        assert not os.path.exists(path)

    def test_no_token_writes_nothing(self, manager):
        manager.controller.credentials = {}
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        ok, msg = manager.start_tunnel('tun1')
        assert not ok
        assert not os.path.exists(os.path.join(manager.frp_config_dir, 'frpc_srv1.toml'))

    def test_changed_config_triggers_reload_when_api_up(self, manager, monkeypatch):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.start_tunnel('tun1')
        # Pretend the daemon's admin API answered a probe
        manager.frp_daemons['srv1']['api_up'] = True
        reloads = []
        monkeypatch.setattr(manager, '_reload_daemon',
                            lambda sid, path: reloads.append((sid, path)) or True)
        c.objects['tun1']['remote_port'] = '9999'
        manager._reconcile()
        assert reloads and reloads[0][0] == 'srv1'


class TestStateRecovery:
    def test_recovers_admin_port_and_desired_set(self, tmp_path):
        from controllers.tunnel_manager import TunnelManager
        toml = tmp_path / 'frpc_srv9.toml'
        toml.write_text(
            'serverAddr = "1.2.3.4"\n[webServer]\naddr = "127.0.0.1"\nport = 7411\n')
        (tmp_path / 'desired.json').write_text(json.dumps({'tunnels': ['tunA', 'tunB']}))
        tm = TunnelManager(FakeController(), frp_config_dir=str(tmp_path),
                           ensure_daemon=lambda: True,
                           frpc_command=[sys.executable, FAKE_FRPC],
                           start_monitor=False)
        assert tm.frp_daemons['srv9']['admin_port'] == 7411
        assert tm.desired_tunnels == {'tunA', 'tunB'}
        # next allocation doesn't collide with the recovered port
        assert tm._alloc_admin_port('srv_other') != 7411


class TestStatusMapping:
    def test_statuses_from_api(self, manager, monkeypatch):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.start_tunnel('tun1')
        manager.frp_daemons['srv1']['api_up'] = True
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'running'}}
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'running'

    def test_not_desired_shows_stopped(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'stopped'

    def test_other_device_shows_disabled(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel(device='device-other')}
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'disabled'
