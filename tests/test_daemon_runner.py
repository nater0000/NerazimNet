"""Integration tests for DaemonRunner using the fake frpc stub."""
import os
import sys
import time
import json
import socket
import http.client

import pytest

from conftest import FAKE_FRPC, free_port


def _write_toml(frp_dir, server_id, admin_port, extra=''):
    path = os.path.join(frp_dir, f'frpc_{server_id}.toml')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(
            'serverAddr = "127.0.0.1"\nserverPort = 7000\n\n'
            '[auth]\ntoken = "t"\n\n[transport]\nprotocol = "quic"\n\n'
            f'[webServer]\naddr = "127.0.0.1"\nport = {admin_port}\n\n{extra}')
    return path


def _api_status(port):
    """GET /api/status via http.client — stdlib, no proxy-env handling, so
    loopback probes can't be hijacked by CI runner proxy settings."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    conn.request('GET', '/api/status')
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, json.loads(body)


def _dump_log(runner, server_id):
    log_path = os.path.join(runner.log_dir, f'frpc_{server_id}.log')
    if os.path.exists(log_path):
        return open(log_path, errors='replace').read()
    return '<no log file>'


def _api_up(runner, server_id, port, timeout_s=30):
    """Waits for the fake frpc admin API; fails fast with its log if the
    process died instead of listening, and dumps diagnostics on timeout."""
    deadline = time.time() + timeout_s
    proc = runner.procs.get(server_id)
    while time.time() < deadline:
        if proc and proc.poll() is not None:
            raise AssertionError(
                f"fake frpc exited rc={proc.returncode} before serving API.\n"
                f"{_dump_log(runner, server_id)}")
        try:
            status, _ = _api_status(port)
            if status == 200:
                return True
        except OSError:
            time.sleep(0.25)
    # Timeout — report whether the port is at least accepting TCP
    try:
        socket.create_connection(('127.0.0.1', port), timeout=2).close()
        tcp = 'port accepts TCP but HTTP probe failed'
    except OSError as e:
        tcp = f'port not accepting TCP: {e}'
    raise AssertionError(
        f"fake frpc API never came up on :{port} ({tcp})\n"
        f"{_dump_log(runner, server_id)}")


@pytest.fixture
def runner(tmp_path):
    from controllers.daemon_runner import DaemonRunner
    return DaemonRunner(frp_dir=str(tmp_path), log_dir=str(tmp_path / 'logs'),
                        frpc_command=[sys.executable, FAKE_FRPC],
                        poll_interval=0.2, spawn_backoff=0.1)


class TestDaemonRunner:
    def test_spawns_frpc_and_serves_api(self, runner, tmp_path):
        port = free_port()
        _write_toml(str(tmp_path), 'srv1', port)
        assert runner.tick() is True
        assert 'srv1' in runner.procs
        assert _api_up(runner, 'srv1', port)
        runner._kill('srv1')

    def test_exits_when_no_tomls(self, runner):
        assert runner.tick() is False

    def test_kills_frpc_when_toml_deleted(self, runner, tmp_path):
        port = free_port()
        path = _write_toml(str(tmp_path), 'srv1', port)
        runner.tick()
        assert 'srv1' in runner.procs
        os.remove(path)
        assert runner.tick() is False  # last toml gone -> daemon exits
        assert 'srv1' not in runner.procs

    def test_respawns_after_crash(self, runner, tmp_path):
        port = free_port()
        _write_toml(str(tmp_path), 'srv1', port)
        runner.tick()
        proc = runner.procs['srv1']
        proc.kill()
        proc.wait()
        time.sleep(0.15)  # let backoff elapse (0.1s)
        runner.tick()
        assert runner.procs['srv1'].poll() is None
        assert runner.procs['srv1'] is not proc
        runner._kill('srv1')

    def test_reload_picks_up_new_proxies(self, runner, tmp_path):
        port = free_port()
        _write_toml(str(tmp_path), 'srv1', port)
        runner.tick()
        assert _api_up(runner, 'srv1', port)
        # simulate a config change + `frpc reload` like TunnelManager does
        _write_toml(str(tmp_path), 'srv1', port,
                    extra='[[proxies]]\nname = "tun1"\ntype = "tcp"\n'
                          'localIP = "127.0.0.1"\nlocalPort = 3000\nremotePort = 8443\n')
        status, body = _api_status(port)
        assert status == 200
        names = [p['name'] for p in body['tcp']]
        assert 'tun1' in names
        runner._kill('srv1')
