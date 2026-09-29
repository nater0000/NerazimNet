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

    def test_extra_port_range_renders_range_proxy(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(extra_ports='udp:50000-50020:localhost:50000-50020')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'name = "range:tun1-x50000"' in content
        assert 'localPort = "50000-50020"' in content
        assert 'remotePort = "50000-50020"' in content
        assert 'type = "udp"' in content

    def test_extra_port_range_allows_offset_local_range(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(extra_ports='tcp:6000-6002:localhost:7000-7002')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'name = "range:tun1-x6000"' in content
        assert 'localPort = "7000-7002"' in content
        assert 'remotePort = "6000-6002"' in content

    def test_extra_port_range_rejects_mismatched_lengths(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(extra_ports='udp:50000-50020:localhost:50000-50005')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'range:' not in content

    def test_extra_port_range_rejects_http_scheme(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(extra_ports='8000-8010:localhost:8000-8010')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'range:' not in content
        assert '-x8000' not in content

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

    def test_notifies_once_on_drop_then_reconnect(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        notes = []
        c.show_notification = lambda title, msg: notes.append((title, msg))
        manager.start_tunnel('tun1')
        manager.frp_daemons['srv1']['api_up'] = True

        # Baseline: running — no notification for the first observation
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'running'}}
        manager.get_tunnel_statuses()
        assert notes == []

        # Drop: running -> error notifies once, not on repeated polls
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'error', 'err': 'dial fail'}}
        manager.get_tunnel_statuses()
        manager.get_tunnel_statuses()
        assert [t for t, _ in notes] == ['Tunnel down']

        # Reconnect: error -> running notifies
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'running'}}
        manager.get_tunnel_statuses()
        assert [t for t, _ in notes] == ['Tunnel down', 'Tunnel reconnected']


class TestNativeHealthCheck:
    """frpc's built-in healthCheck replaces the local socket probe: the
    generated config enables it, and its 'check failed' phase maps to the
    dashboard warning."""

    def test_main_proxy_has_healthcheck(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert '[proxies.healthCheck]' in content
        assert 'type = "tcp"' in content

    def test_udp_and_range_proxies_skip_healthcheck(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(),
                     'tun1': make_tunnel(
                         extra_ports='udp:7881:localhost:7881, raw:7882:localhost:7882')}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        import tomllib
        proxies = tomllib.loads(content)['proxies']
        by_name = {p['name']: p for p in proxies}
        assert 'healthCheck' in by_name['tun1']
        assert 'healthCheck' in by_name['tun1-x7882']  # raw/tcp gets checked
        assert 'healthCheck' not in by_name['tun1-x7881']  # udp can't

    def test_check_failed_phase_shows_warning(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel(local='localhost:3000')}
        manager.start_tunnel('tun1')
        manager.frp_daemons['srv1']['api_up'] = True
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'check failed'}}
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'warning'
        assert '3000' in statuses['tun1']['message']
        assert 'not listening' in statuses['tun1']['message']

    def test_proxy_error_still_error(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.start_tunnel('tun1')
        manager.frp_daemons['srv1']['api_up'] = True
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'error', 'err': 'dial fail'}}
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'error'
        assert statuses['tun1']['message'] == 'dial fail'


class TestRouteHealthAndStats:
    """End-to-end HTTPS probe (latency/status) + drop counters."""

    def _setup_running_tunnel(self, manager, **kw):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel(**kw)}
        manager.start_tunnel('tun1')
        manager.frp_daemons['srv1']['api_up'] = True
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'running'}}

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    def test_e2e_latency_appears_in_message(self, manager, monkeypatch):
        import controllers.tunnel_manager as tm_mod
        monkeypatch.setattr(tm_mod.requests, 'head', lambda *a, **k: self._Resp(200))
        self._setup_running_tunnel(manager)
        manager._check_route_health()
        assert manager.route_health['tun1']['ok'] is True
        assert manager.route_health['tun1']['code'] == 200
        assert manager.route_health['tun1']['latency_ms'] is not None
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'running'
        assert 'ms' in statuses['tun1']['message']

    def test_e2e_502_while_running_shows_warning(self, manager, monkeypatch):
        import controllers.tunnel_manager as tm_mod
        monkeypatch.setattr(tm_mod.requests, 'head', lambda *a, **k: self._Resp(502))
        self._setup_running_tunnel(manager)
        manager._check_route_health()
        assert manager.route_health['tun1']['ok'] is False
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'warning'
        assert '502' in statuses['tun1']['message']

    def test_e2e_unreachable_shows_warning(self, manager, monkeypatch):
        import controllers.tunnel_manager as tm_mod
        import requests as real_requests
        def boom(*a, **k):
            raise real_requests.ConnectionError('nope')
        monkeypatch.setattr(tm_mod.requests, 'head', boom)
        self._setup_running_tunnel(manager)
        manager._check_route_health()
        statuses = manager.get_tunnel_statuses()
        assert statuses['tun1']['status'] == 'warning'
        assert 'unreachable' in statuses['tun1']['message']

    def test_401_counts_as_healthy(self, manager, monkeypatch):
        # A basic-auth gate answers 401 — the route itself works.
        import controllers.tunnel_manager as tm_mod
        monkeypatch.setattr(tm_mod.requests, 'head', lambda *a, **k: self._Resp(401))
        self._setup_running_tunnel(manager)
        manager._check_route_health()
        assert manager.route_health['tun1']['ok'] is True

    def test_wildcard_route_skipped(self, manager, monkeypatch):
        import controllers.tunnel_manager as tm_mod
        calls = []
        monkeypatch.setattr(tm_mod.requests, 'head',
                            lambda *a, **k: calls.append(a) or self._Resp(200))
        self._setup_running_tunnel(manager, hostname='*.lab.example.com')
        manager._check_route_health()
        assert calls == []
        assert 'tun1' not in manager.route_health

    def test_drops_counted_on_running_to_error(self, manager):
        self._setup_running_tunnel(manager)
        manager.get_tunnel_statuses()  # baseline: running
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'error', 'err': 'x'}}
        manager.get_tunnel_statuses()
        manager.get_tunnel_statuses()  # repeated error — still one drop
        assert manager.tunnel_stats['tun1']['drops'] == 1
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'running'}}
        manager.get_tunnel_statuses()
        manager.proxy_statuses = {'tun1': {'name': 'tun1', 'status': 'error', 'err': 'x'}}
        manager.get_tunnel_statuses()
        assert manager.tunnel_stats['tun1']['drops'] == 2
