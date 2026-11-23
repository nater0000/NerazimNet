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
        # Pin the admin port — allocation skips ports in live use, which
        # made this test fail whenever a real frpc was running locally.
        # Stub the probes so a real listener can't force reallocation.
        manager._admin_ports['srv1'] = 7400
        manager._admin_port_free = lambda port: True
        manager._admin_port_is_ours = lambda sid, port: True
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'serverAddr = "10.0.0.1"' in content
        assert 'token = "tok"' in content
        assert 'protocol = "quic"' in content
        assert 'port = 7400' in content          # admin api port
        assert 'name = "tun1"' in content
        assert 'localPort = 3000' in content
        assert 'remotePort = 8443' in content

    def test_admin_api_auth_rendered_and_stable(self, manager):
        c = manager.controller
        c.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')
        content = manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert 'user = "nerazim"' in content
        import re
        m = re.search(r'password = "([^"]+)"', content)
        assert m and len(m.group(1)) >= 20
        # same password across re-renders; different per server
        assert m.group(1) in manager._build_frpc_config('srv1', '10.0.0.1', 'tok')
        assert m.group(1) not in manager._build_frpc_config('srv2', '10.0.0.2', 'tok')

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
            'serverAddr = "1.2.3.4"\n[webServer]\naddr = "127.0.0.1"\n'
            'port = 7411\nuser = "nerazim"\npassword = "pw123secret"\n')
        (tmp_path / 'desired.json').write_text(json.dumps({'tunnels': ['tunA', 'tunB']}))
        tm = TunnelManager(FakeController(), frp_config_dir=str(tmp_path),
                           ensure_daemon=lambda: True,
                           frpc_command=[sys.executable, FAKE_FRPC],
                           start_monitor=False)
        assert tm.frp_daemons['srv9']['admin_port'] == 7411
        assert tm._admin_creds['srv9'] == 'pw123secret'
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


class TestTransportHealth:
    """Transport-fallback chain: latch, reset, and api-down convergence."""

    def _desired(self, manager):
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')

    def test_dead_final_survives_api_down_poll(self, manager, monkeypatch):
        """The latch must not be erased by api-down polls — that was the
        frozen-(0s)/no-error bug."""
        self._desired(manager)
        port = free_port()  # closed -> conn refused -> api down
        manager.frp_daemons['srv1'] = {'admin_port': port, 'api_up': False}
        manager._transport_dead_final.add('srv1')
        manager._unreachable_reason['srv1'] = 'boom'
        manager._api_down_since['srv1'] = time.time() - 10
        probes = []
        monkeypatch.setattr(manager, '_frps_port_reachable',
                            lambda s: probes.append(s) or False)
        manager._poll_proxy_statuses()
        assert 'srv1' in manager._transport_dead_final
        # already latched — the convergence probe must not re-run
        assert probes == []

    def test_api_down_probe_waits_for_fail_window(self, manager, monkeypatch):
        """A respawn window is ~5-15s of api downtime — probing at 5s
        re-latches dead_final over a fresh retry. Must wait FAIL_S."""
        self._desired(manager)
        port = free_port()
        manager.frp_daemons['srv1'] = {'admin_port': port, 'api_up': False}
        manager._api_down_since['srv1'] = time.time() - 10  # < FAIL_S
        monkeypatch.setattr(manager, '_frps_port_reachable', lambda s: False)
        manager._poll_proxy_statuses()
        assert 'srv1' not in manager._transport_dead_final

    def test_api_down_probe_skips_pending_restart(self, manager, monkeypatch):
        self._desired(manager)
        port = free_port()
        manager.frp_daemons['srv1'] = {'admin_port': port, 'api_up': False}
        manager._api_down_since['srv1'] = time.time() - 60
        manager._transport_restart.add('srv1')
        probes = []
        monkeypatch.setattr(manager, '_frps_port_reachable',
                            lambda s: probes.append(s) or False)
        manager._poll_proxy_statuses()
        assert 'srv1' not in manager._transport_dead_final
        assert probes == []

    def test_api_down_probe_latches_terminal(self, manager, monkeypatch):
        """api down past FAIL_S + dead :7000 -> converge to terminal."""
        self._desired(manager)
        port = free_port()
        manager.frp_daemons['srv1'] = {'admin_port': port, 'api_up': False}
        manager._api_down_since['srv1'] = \
            time.time() - (manager.API_DOWN_FAIL_S + 1)
        monkeypatch.setattr(manager, '_frps_port_reachable', lambda s: False)
        monkeypatch.setattr(manager, '_diagnose_unreachable',
                            staticmethod(lambda s: 'port dead'))
        manager._poll_proxy_statuses()
        assert 'srv1' in manager._transport_dead_final
        assert manager._unreachable_reason['srv1'] == 'port dead'


class TestRetryReset:
    def _desired(self, manager):
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')

    def test_retry_clears_latched_failure(self, manager, monkeypatch):
        """Start on an errored tunnel must reset the whole chain to step 0."""
        self._desired(manager)
        manager._transport_dead_final.add('srv1')
        manager._unreachable_reason['srv1'] = 'dead'
        manager._transport_step['srv1'] = 2
        manager._transport_dead_since['srv1'] = time.time()
        manager._port_dead['srv1'] = True
        manager.frp_daemons['srv1'] = {'api_up': True, 'admin_port': 7400}
        stops = []
        monkeypatch.setattr(manager, '_stop_frpc',
                            lambda sid, path: stops.append(sid) or True)
        monkeypatch.setattr(manager, '_reload_daemon', lambda sid, path: True)
        ok, _ = manager.start_tunnel('tun1')
        assert ok
        assert 'srv1' not in manager._transport_dead_final
        assert 'srv1' not in manager._transport_step
        assert 'srv1' not in manager._port_dead
        assert stops == ['srv1']
        # the direct stop already ran — reconcile must not stop again
        assert 'srv1' not in manager._transport_restart

    def test_fresh_start_on_dead_server_resets(self, manager, monkeypatch):
        """A stopped->started tunnel joins a latched server — it must
        reset, or the row renders Unreachable instantly."""
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager._transport_dead_final.add('srv1')
        manager._transport_step['srv1'] = 2
        monkeypatch.setattr(manager, '_stop_frpc', lambda sid, path: True)
        monkeypatch.setattr(manager, '_reload_daemon', lambda sid, path: True)
        ok, _ = manager.start_tunnel('tun1')
        assert ok
        assert 'srv1' not in manager._transport_dead_final
        assert 'srv1' not in manager._transport_step

    def test_fresh_start_on_healthy_server_no_reset(self, manager, monkeypatch):
        """Not latched -> hot reload only; no frpc bounce."""
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        stops = []
        monkeypatch.setattr(manager, '_stop_frpc',
                            lambda sid, path: stops.append(sid) or True)
        monkeypatch.setattr(manager, '_reload_daemon', lambda sid, path: True)
        ok, _ = manager.start_tunnel('tun1')
        assert ok
        assert 'srv1' not in manager._transport_restart
        assert stops == []

    def test_remove_config_clears_all_server_state(self, manager):
        """A re-added server must not inherit stale failure state."""
        manager._transport_dead_final.add('srv1')
        manager._unreachable_reason['srv1'] = 'dead'
        manager._transport_step['srv1'] = 2
        manager._transport_dead_since['srv1'] = time.time()
        manager._transport_restart.add('srv1')
        manager._api_down_since['srv1'] = time.time()
        manager._respawning['srv1'] = time.time()
        manager._port_dead['srv1'] = True
        manager._admin_creds['srv1'] = 'pw'
        manager._remove_frpc_config('srv1')
        assert 'srv1' not in manager._transport_dead_final
        assert 'srv1' not in manager._unreachable_reason
        assert 'srv1' not in manager._transport_step
        assert 'srv1' not in manager._transport_dead_since
        assert 'srv1' not in manager._transport_restart
        assert 'srv1' not in manager._api_down_since
        assert 'srv1' not in manager._respawning
        assert 'srv1' not in manager._port_dead
        assert 'srv1' not in manager._admin_creds


class TestStatusRendering:
    def _desired(self, manager):
        manager.controller.objects = {'srv1': make_server(), 'tun1': make_tunnel()}
        manager.desired_tunnels.add('tun1')
        manager.frp_daemons['srv1'] = {'api_up': True, 'admin_port': 7400}

    def test_restart_pending_renders_restarting(self, manager):
        """Flag set but restart not landed -> 'frpc restarting', not the
        next transport's name."""
        self._desired(manager)
        manager._transport_restart.add('srv1')
        st = manager.get_tunnel_statuses()
        assert st['tun1']['short'] == 'frpc restarting…'

    def test_dead_final_renders_unreachable(self, manager):
        self._desired(manager)
        manager._transport_dead_final.add('srv1')
        manager._unreachable_reason['srv1'] = 'port dead'
        st = manager.get_tunnel_statuses()
        assert st['tun1']['status'] == 'error'
        assert st['tun1']['short'] == 'Unreachable :7000'
        assert st['tun1']['message'] == 'port dead'

    def test_countdown_uses_fast_window_when_port_dead(self, manager):
        """The render must count down from the window the health check
        will actually apply — 5s fast-dead, not the 30s fallback."""
        self._desired(manager)
        manager._transport_dead_since['srv1'] = time.time() - 1
 
