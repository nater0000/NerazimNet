"""Headless frpc supervisor — runs via `NerazimNet --daemon`.

This is the process the OS service (Scheduled Task / systemd --user /
LaunchAgent) keeps alive. It owns frpc lifecycle so tunnels survive the
GUI closing and reboots:

  - scans <app_data>/frp/frpc_*.toml; spawns one hidden frpc per config
  - restarts frpc on crash (with backoff)
  - stops a server's frpc when its toml is deleted
  - exits cleanly (code 0) when no tomls remain, so the OS doesn't restart it
  - on startup, asks any orphaned frpc to exit via `frpc stop -c` before
    spawning, preventing duplicate daemons / admin-port collisions
"""
import os
import sys
import glob
import time
import signal
import logging
import subprocess

from utils.paths import get_app_data_dir, get_bin_dir, EXE_EXT
from utils.staging import stage_bundled_exe

POLL_INTERVAL_S = 3.0
SPAWN_BACKOFF_S = 10.0


class DaemonRunner:
    def __init__(self, frp_dir: str = None, frpc_command: list = None,
                 log_dir: str = None,
                 poll_interval: float = POLL_INTERVAL_S,
                 spawn_backoff: float = SPAWN_BACKOFF_S):
        self.frp_dir = frp_dir or os.path.join(get_app_data_dir(), 'frp')
        self.log_dir = log_dir or os.path.join(get_app_data_dir(), 'logs')
        self.frpc_command = frpc_command or [self._resolve_frpc()]
        self.poll_interval = poll_interval
        self.spawn_backoff = spawn_backoff
        self.procs = {}        # server_id -> subprocess.Popen
        self._last_spawn = {}  # server_id -> timestamp
        self._running = True

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_frpc() -> str:
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            bundled = os.path.join(sys._MEIPASS, "resources", "frp", f"frpc{EXE_EXT}")
            return stage_bundled_exe(bundled, f"frpc{EXE_EXT}")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base = os.path.dirname(os.path.dirname(script_dir))  # controllers -> src -> root
        return os.path.join(base, "resources", "frp", f"frpc{EXE_EXT}")

    def _scan_tomls(self) -> dict:
        """{server_id: toml_path} for every frpc_<server_id>.toml in frp dir."""
        result = {}
        for path in glob.glob(os.path.join(self.frp_dir, 'frpc_*.toml')):
            server_id = os.path.basename(path)[len('frpc_'):-len('.toml')]
            if server_id:
                result[server_id] = path
        return result

    # ------------------------------------------------------------------
    # frpc lifecycle
    # ------------------------------------------------------------------

    def _stop_orphan(self, server_id: str, toml_path: str):
        """Asks any previously-running frpc for this config to exit, so a
        crashed daemon can't leave a duplicate holding the admin port."""
        try:
            subprocess.run(
                self.frpc_command + ['stop', '-c', toml_path],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except Exception as e:
            logging.debug(f"[daemon] frpc stop for {server_id} skipped: {e}")

    def _spawn(self, server_id: str, toml_path: str) -> bool:
        try:
            self._stop_orphan(server_id, toml_path)
            log_path = os.path.join(self.log_dir, f'frpc_{server_id}.log')
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_file = open(log_path, 'a', encoding='utf-8')
            log_file.write(f"\n--- daemon spawn {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log_file.flush()

            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            preexec_fn = os.setsid if os.name != 'nt' else None

            proc = subprocess.Popen(
                self.frpc_command + ['-c', toml_path],
                stdout=log_file, stderr=subprocess.STDOUT,
                startupinfo=startupinfo, creationflags=creationflags,
                preexec_fn=preexec_fn)
            self.procs[server_id] = proc
            self._last_spawn[server_id] = time.time()
            logging.info(f"[daemon] frpc started for {server_id} (PID {proc.pid})")
            return True
        except Exception as e:
            logging.error(f"[daemon] failed to spawn frpc for {server_id}: {e}")
            return False

    def _kill(self, server_id: str):
        proc = self.procs.pop(server_id, None)
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/PID', str(proc.pid), '/T'],
                               capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            else:
                import os as _os
                _os.killpg(_os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            logging.info(f"[daemon] frpc for {server_id} stopped")
        except Exception as e:
            logging.warning(f"[daemon] error stopping frpc for {server_id}: {e}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """One supervision pass. Returns False when the daemon should exit."""
        tomls = self._scan_tomls()
        # Stop frpc for deleted configs first — including when the last
        # toml disappears (daemon exits but must not orphan frpc).
        for server_id in list(self.procs):
            if server_id not in tomls:
                self._kill(server_id)
        if not tomls:
            return False
        # (Re)spawn frpc for configs without a live process
        for server_id, toml_path in tomls.items():
            proc = self.procs.get(server_id)
            if proc and proc.poll() is None:
                continue
            if time.time() - self._last_spawn.get(server_id, 0) < self.spawn_backoff:
                continue
            self._spawn(server_id, toml_path)
        return True

    def run(self):
        logging.info("[daemon] NerazimNet daemon starting.")
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, '_running', False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, '_running', False))
        try:
            while self._running:
                try:
                    if not self.tick():
                        logging.info("[daemon] No frpc configs remain; exiting.")
                        break
                except Exception as e:
                    logging.error(f"[daemon] tick error: {e}", exc_info=True)
                time.sleep(self.poll_interval)
        finally:
            for server_id in list(self.procs):
                self._kill(server_id)
        logging.info("[daemon] Daemon stopped.")
