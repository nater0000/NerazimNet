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

from utils.paths import get_app_data_dir, EXE_EXT
from utils.staging import stage_bundled_exe

POLL_INTERVAL_S = 3.0
SPAWN_BACKOFF_S = 10.0


class DaemonRunner:
    def __init__(self, frp_dir: str = None, frpc_command: list = None,
                 log_dir: str = None, lock_path: str = None,
                 poll_interval: float = POLL_INTERVAL_S,
                 spawn_backoff: float = SPAWN_BACKOFF_S):
        self.frp_dir = frp_dir or os.path.join(get_app_data_dir(), 'frp')
        self.log_dir = log_dir or os.path.join(get_app_data_dir(), 'logs')
        self.lock_path = (lock_path
                          or os.path.join(get_app_data_dir(), 'daemon.lock'))
        self.frpc_command = frpc_command or [self._resolve_frpc()]
        self.poll_interval = poll_interval
        self.spawn_backoff = spawn_backoff
        self.procs = {}        # server_id -> subprocess.Popen
        self._log_files = {}   # server_id -> open log file handle
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
        bundled = os.path.join(base, "resources", "frp", f"frpc{EXE_EXT}")
        if os.path.exists(bundled):
            return bundled
        # resources/frp is populated at build time — a dev checkout may
        # lack it while an older staged copy still exists in app data.
        staged = os.path.join(get_app_data_dir(), 'bin', f"frpc{EXE_EXT}")
        if os.path.exists(staged):
            logging.warning(f"resources/frp missing; using staged frpc: {staged}")
            return staged
        return bundled  # Popen error will name the expected path

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
        self._kill_stray_frpc(server_id, toml_path)

    def _kill_stray_frpc(self, server_id: str, toml_path: str):
        """`frpc stop -c` only reaches the listener on the toml's current
        webServer port — a stale frpc from an older generation (different
        admin port/creds) ignores it and keeps running forever. Kill any
        frpc whose cmdline references this config but isn't a child of
        this daemon."""
        base = os.path.basename(toml_path)
        mine = {p.pid for p in self.procs.values() if p.poll() is None}
        try:
            if os.name == 'nt':
                out = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     "Get-CimInstance Win32_Process -Filter \"name='frpc.exe'\" "
                     f"| Where-Object {{ $_.CommandLine -like '*{base}*' }} "
                     "| Select-Object -ExpandProperty ProcessId"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW).stdout
                pids = [int(w) for w in out.split() if w.isdigit()]
                kill_cmd = lambda pid: ['taskkill', '/F', '/PID', str(pid)]
            else:
                out = subprocess.run(
                    ['pgrep', '-f', f'frpc.*{base}'],
                    capture_output=True, text=True, timeout=10).stdout
                pids = [int(w) for w in out.split() if w.isdigit()]
                kill_cmd = lambda pid: ['kill', '-9', str(pid)]
            for pid in pids:
                if pid not in mine and pid != os.getpid():
                    subprocess.run(kill_cmd(pid), capture_output=True,
                                   timeout=5,
                                   creationflags=(subprocess.CREATE_NO_WINDOW
                                                  if os.name == 'nt' else 0))
                    logging.info(f"[daemon] killed stray frpc pid {pid} for {server_id}")
        except Exception as e:
            logging.debug(f"[daemon] stray frpc scan skipped: {e}")

    def _close_log_file(self, server_id: str):
        """Close a previously-opened log handle for a server (respawn-safe)."""
        fh = self._log_files.pop(server_id, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass

    def _spawn(self, server_id: str, toml_path: str) -> bool:
        try:
            self._stop_orphan(server_id, toml_path)
            self._close_log_file(server_id)  # drop stale handle before reopening
            log_path = os.path.join(self.log_dir, f'frpc_{server_id}.log')
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_file = open(log_path, 'a', encoding='utf-8')
            log_file.write(f"\n--- daemon spawn {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log_file.flush()
            self._log_files[server_id] = log_file

            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                self.frpc_command + ['-c', toml_path],
                stdout=log_file, stderr=subprocess.STDOUT,
                startupinfo=startupinfo, creationflags=creationflags,
                # start_new_session (setsid via posix_spawn) — preexec_fn
                # takes the fork path and can deadlock on macOS when the
                # parent process has threads holding locks at fork time
                start_new_session=(os.name != 'nt'))
            self.procs[server_id] = proc
            self._last_spawn[server_id] = time.time()
            logging.info(f"[daemon] frpc started for {server_id} (PID {proc.pid})")
            return True
        except Exception as e:
            logging.error(f"[daemon] failed to spawn frpc for {server_id}: {e}")
            # Honor the backoff on failure too — otherwise a missing
            # binary respawns every poll tick and floods the log.
            self._last_spawn[server_id] = time.time()
            return False

    def _kill(self, server_id: str):
        proc = self.procs.pop(server_id, None)
        try:
            if proc and proc.poll() is None:
                self._kill_proc(proc)
            logging.info(f"[daemon] frpc for {server_id} stopped")
        except Exception as e:
            logging.warning(f"[daemon] error stopping frpc for {server_id}: {e}")
        finally:
            self._close_log_file(server_id)

    def _kill_proc(self, proc):
        """Terminate a frpc process (and its tree on Windows)."""
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

    def _acquire_singleton(self):
        """Exclusive lockfile for the whole daemon lifetime. Two spawners
        (scheduled task + detached fallback) can both start us in the same
        second — the pidfile check races, so the loser must exit before it
        can spawn/kill frpc and flap the admin API."""
        fh = open(self.lock_path, 'a+')
        try:
            fh.seek(0)  # 'a+' positions at EOF — the lock byte must be stable
            if not fh.read(1):
                fh.write('\0')  # locking an empty file fails on some CRTs
                fh.flush()
            fh.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Windows byte-range locks block even read() on the locked
            # byte — the read above raises PermissionError for the loser,
            # not just the locking() call, so the whole block is inside
            # the try.
            logging.info("[daemon] another instance holds daemon.lock; exiting.")
            fh.close()
            return None
        return fh  # held for process lifetime; released on exit

    def run(self):
        lock_fh = self._acquire_singleton()
        if lock_fh is None:
            return
        logging.info("[daemon] NerazimNet daemon starting.")
        pid_path = os.path.join(get_app_data_dir(), 'daemon.pid')
        try:
            with open(pid_path, 'w') as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
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
            for server_id in list(self._log_files):
                self._close_log_file(server_id)
            try:
                os.remove(pid_path)
            except OSError:
                pass
        logging.info("[daemon] Daemon stopped.")
