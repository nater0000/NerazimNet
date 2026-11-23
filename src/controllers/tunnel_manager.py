import subprocess
import logging
import os
import socket
import sys
import threading
import time
import re
import json
import glob
import hashlib
import http.client
import secrets
from collections import deque, defaultdict

import requests

from utils.staging import stage_bundled_exe
from utils.paths import get_app_data_dir, EXE_EXT
from utils.services import ensure_daemon_running

# localIP values land inside TOML quoted strings — restrict charset so a
# synced (or hand-edited) config can't break frpc parsing or inject keys.
_LOCAL_HOST_RE = re.compile(r'[a-zA-Z0-9.\-_:\[\]]+')


class TunnelManager:
    """
    Manages FRP tunnels by writing frpc_<server>.toml configs and letting the
    OS-level NerazimNet daemon (Scheduled Task / systemd user / LaunchAgent)
    own the frpc processes. Tunnels therefore survive app exit and reboots.

    Responsibilities:
      - desired tunnel set -> frpc.toml files (+ desired.json)
      - ensure the OS daemon is registered and running while configs exist
      - hot-reload via `frpc reload` when a config changes
      - status via each frpc's local admin API (127.0.0.1:<port>/api/status)
    """

    FRP_SERVER_PORT = 7000
    ADMIN_PORT_BASE = 7400
    DAEMON_RESTART_BACKOFF_S = 10
    LOCAL_HEALTH_INTERVAL_S = 15
    # 'auto' transport tries each protocol in order when proxies never get
    # past 'new' — QUIC dies on networks that block UDP, TCP on odd DPI.
    TRANSPORT_CHAIN = ('quic', 'tcp', 'websocket')
    # frpc's own dial/login timeout is ~10-20s — 30s is safely past it
    # while keeping the worst-case amber window (3 transports) ~90s.
    TRANSPORT_FALLBACK_AFTER_S = 30
    # But if the frps port itself doesn't accept TCP, nothing frpc
    # dials can work — after a short grace, probe it and fail fast.
    FAST_DEAD_AFTER_S = 5

    def __init__(self, controller, frp_config_dir: str = None,
                 ensure_daemon=None, frpc_command: list = None,
                 start_monitor: bool = True):
        self.controller = controller
        self.desired_tunnels = set()          # { tunnel_id } user wants running
        self.frp_daemons = {}                 # { server_id: {'admin_port': int, 'api_up': bool} }
        self.config_hashes = {}               # { server_id: hash of last written frpc.toml }
        self.daemon_logs = {}                 # { server_id: deque() }
        self.tunnel_logs = {}                 # { tunnel_id: deque() } - tunnel-specific events
        self.tunnel_error_messages = {}       # { tunnel_id: "error message" }
        self.proxy_statuses = {}              # { tunnel_id: proxy status entry from frpc API }
        self.local_route_health = {}          # { tunnel_id: {'ok': bool, 'message': str} }
        self._prev_statuses = {}              # { tunnel_id: last status } for drop/reconnect notifications
        self._starting_since = {}             # { tunnel_id: timestamp } transitional-state watchdog
        self._admin_ports = {}                # { server_id: port }
        self._admin_port_used = set()
        self._admin_creds = {}                # { server_id: frpc admin API password }
        self._transport_step = {}             # { server_id: index into TRANSPORT_CHAIN } auto mode
        self._transport_dead_since = {}       # { server_id: timestamp all proxies went 'new' }
        self._transport_restart = set()       # server_ids needing frpc restart (not reload)
        self._transport_dead_final = set()    # chain exhausted — server unreachable on all transports
        self._unreachable_reason = {}         # { server_id: diagnostic } for dead_final servers
        self._respawning = {}                 # { server_id: ts } frpc stop->respawn window
        self._api_down_since = {}             # { server_id: ts } first observed api-down
        self._last_service_attempt = {}       # { '_daemon': timestamp } service-start backoff
        self._lock = threading.Lock()
        self._reconcile_lock = threading.Lock() # Serializes daemon/config reconciliation
        self._monitor_stop = threading.Event() # Lets the monitor exit promptly on shutdown

        self.frpc_command = frpc_command or [self._resolve_frpc_path()]
        self.frp_config_dir = frp_config_dir or os.path.join(get_app_data_dir(), 'frp')
        os.makedirs(self.frp_config_dir, exist_ok=True)
        self._ensure_daemon = ensure_daemon or ensure_daemon_running

        self._recover_state()

        self._is_monitoring = True
        self._last_health_check = 0
        self.monitor_thread = None
        if start_monitor:
            self.monitor_thread = threading.Thread(target=self._monitor_tunnels, daemon=True)
            self.monitor_thread.start()

    # ------------------------------------------------------------------
    # Paths / config generation
    # ------------------------------------------------------------------

    def _resolve_frpc_path(self) -> str:
        """Locates the bundled frpc binary (PyInstaller bundle or source tree)."""
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            # Stage to a stable path — _MEIPASS changes every launch, and
            # firewall rules / AV heuristics key on the exe path.
            bundled = os.path.join(sys._MEIPASS, "resources", "frp", f"frpc{EXE_EXT}")
            return stage_bundled_exe(bundled, f"frpc{EXE_EXT}")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_path = os.path.dirname(os.path.dirname(script_dir)) # controllers -> src -> root
        return os.path.join(base_path, "resources", "frp", f"frpc{EXE_EXT}")

    def _toml_path_for(self, server_id: str) -> str:
        return os.path.join(self.frp_config_dir, f"frpc_{server_id}.toml")

    def _desired_state_path(self) -> str:
        return os.path.join(self.frp_config_dir, "desired.json")

    def _alloc_admin_port(self, server_id: str) -> int:
        port = self._admin_ports.get(server_id)
        if port and (self._admin_port_free(port)
                     or self._admin_port_is_ours(server_id, port)):
            return port
        # Stored port is held by a foreign frpc (stale orphan with old
        # creds) — asking it to stop is rejected, and a new frpc spawned on
        # the same port can never bind its admin API. Move to a free port;
        # the GUI polls whatever the current toml records, so the orphan
        # simply stops mattering.
        if port:
            logging.warning(
                f"frpc admin port {port} for {server_id} is held by a "
                f"foreign process; allocating a fresh port.")
        port = self.ADMIN_PORT_BASE
        while port in self._admin_port_used or not self._admin_port_free(port):
            port += 1
        self._admin_ports[server_id] = port
        self._admin_port_used.add(port)
        return port

    @staticmethod
    def _admin_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return True
            except OSError:
                return False

    def _admin_port_is_ours(self, server_id: str, port: int) -> bool:
        """The listener on `port` answers /api/status with this server's
        admin password — i.e. it's our frpc, not a stale orphan."""
        password = self._admin_creds.get(server_id)
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            headers = {}
            if password:
                import base64
                headers['Authorization'] = 'Basic ' + base64.b64encode(
                    f'nerazim:{password}'.encode()).decode()
            conn.request('GET', '/api/status', headers=headers)
            ours = conn.getresponse().status == 200
            conn.close()
            return ours
        except OSError:
            return False

    def _webserver_config_from_toml(self, toml_path: str) -> tuple[int | None, str | None]:
        """Reads [webServer] port + password back out of an existing frpc.toml
        so admin settings stay stable across app restarts (needed for adoption)."""
        port = password = None
        try:
            in_webserver = False
            with open(toml_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('['):
                        in_webserver = (line == '[webServer]')
                        continue
                    if not in_webserver:
                        continue
                    key, _, value = line.partition('=')
                    key, value = key.strip(), value.strip().strip('"')
                    if key == 'port':
                        port = int(value)
                    elif key == 'password':
                        password = value
        except (OSError, ValueError):
            pass
        return port, password

    def _recover_state(self):
        """Rebuilds admin-port map and desired-tunnel set from files on disk,
        so a relaunched GUI reflects tunnels the daemon is already running."""
        for toml_path in glob.glob(os.path.join(self.frp_config_dir, 'frpc_*.toml')):
            server_id = os.path.basename(toml_path)[len('frpc_'):-len('.toml')]
            port, password = self._webserver_config_from_toml(toml_path)
            if server_id and port:
                self._admin_ports[server_id] = port
                self._admin_port_used.add(port)
                self.frp_daemons[server_id] = {'admin_port': port, 'api_up': False}
            if server_id and password:
                self._admin_creds[server_id] = password
        try:
            with open(self._desired_state_path(), 'r', encoding='utf-8') as f:
                for tid in json.load(f).get('tunnels', []):
                    self.desired_tunnels.add(tid)
            if self.desired_tunnels:
                logging.info(f"Recovered {len(self.desired_tunnels)} desired tunnels from disk.")
        except (OSError, json.JSONDecodeError):
            pass

    def _write_desired_state(self):
        try:
            with open(self._desired_state_path(), 'w', encoding='utf-8') as f:
                json.dump({'tunnels': sorted(self.desired_tunnels)}, f)
        except OSError as e:
            logging.warning(f"Could not persist desired tunnel state: {e}")

    def _parse_local_dest(self, local_dest: str) -> tuple[str, int] | None:
        """Parses 'host:port' into (localIP, localPort). Maps localhost -> 127.0.0.1."""
        if not local_dest or ':' not in local_dest:
            return None
        host, _, port_str = local_dest.rpartition(':')
        host = host.strip() or '127.0.0.1'
        if host.lower() == 'localhost':
            host = '127.0.0.1'
        if not _LOCAL_HOST_RE.fullmatch(host):
            return None
        try:
            port = int(port_str.strip())
            if not (0 < port <= 65535):
                return None
            return host, port
        except ValueError:
            return None

    def _admin_password(self, server_id: str) -> str:
        """Per-server frpc admin password, stable across re-renders."""
        cred = self._admin_creds.get(server_id)
        if not cred:
            cred = secrets.token_urlsafe(18)
            self._admin_creds[server_id] = cred
        return cred

    def _resolve_transport(self, server_id: str) -> str:
        """frpc transport.protocol for a server: the configured value, or the
        current step of the auto fallback chain."""
        server = self.controller.get_object_by_id(server_id) or {}
        mode = str(server.get('transport') or 'auto').strip().lower()
        if mode in self.TRANSPORT_CHAIN:
            return mode
        step = self._transport_step.get(server_id, 0)
        return self.TRANSPORT_CHAIN[step]

    def _build_frpc_config(self, server_id: str, server_ip: str, frp_token: str) -> str:
        """Renders the frpc.toml content for every *desired* tunnel on a server."""
        admin_port = self._alloc_admin_port(server_id)
        my_device_id = self.controller.get_my_device_id()
        lines = [
            f'serverAddr = "{server_ip}"',
            f'serverPort = {self.FRP_SERVER_PORT}',
            # Keep retrying login in-process instead of crash-looping —
            # the admin API must stay up so the transport-fallback
            # watchdog can observe dead transports and advance the chain.
            'loginFailExit = false',
            '',
            '[auth]',
            f'token = "{frp_token}"',
            '',
            '[transport]',
            f'protocol = "{self._resolve_transport(server_id)}"',
            'tls.enable = true',
            '',
            '[webServer]',
            'addr = "127.0.0.1"',
            f'port = {admin_port}',
            # Basic auth on the local admin API — loopback-only still leaves
            # the API reachable to any process running as the same user.
            'user = "nerazim"',
            f'password = "{self._admin_password(server_id)}"',
            '',
        ]

        for tunnel_id in sorted(self.desired_tunnels):
            tunnel = self.controller.get_object_by_id(tunnel_id)
            if not tunnel or tunnel.get('server_id') != server_id:
                continue
            # 'local' routes point at a service running on the VPS itself —
            # Nginx proxies to it directly, so no frpc proxy is needed.
            if tunnel.get('route_type', 'tunnel') == 'local':
                continue
            # Only render proxies for tunnels assigned to this device
            if my_device_id and tunnel.get('client_device_id') not in (None, my_device_id):
                continue

            try:
                remote_port = int(tunnel['remote_port'])
            except (KeyError, TypeError, ValueError):
                logging.warning(f"Skipping tunnel {tunnel_id}: invalid remote_port.")
                continue
            parsed = self._parse_local_dest(tunnel.get('local_destination', ''))
            if not parsed:
                logging.warning(f"Skipping tunnel {tunnel_id}: invalid local_destination.")
                continue
            local_ip, local_port = parsed

            lines += [
                '[[proxies]]',
                f'name = "{tunnel_id}"',
                'type = "tcp"',
                f'localIP = "{local_ip}"',
                f'localPort = {local_port}',
                f'remotePort = {remote_port}',
                '',
            ]

            # Extra service ports (e.g. '7880:localhost:7880, raw:7881:localhost:7881',
            # 'udp:50000-50020:localhost:50000-50020' for LiveKit/WebRTC ranges)
            for spec in (tunnel.get('extra_ports') or '').split(','):
                spec = spec.strip()
                if not spec:
                    continue
                match = re.fullmatch(r'(?:(raw|tcp|udp|http|wss):)?(\d+(?:-\d+)?):(.+)',
                                     spec, re.IGNORECASE)
                if not match:
                    logging.warning(f"Skipping invalid extra port spec '{spec}' for tunnel {tunnel_id}.")
                    continue
                scheme = (match.group(1) or 'http').lower()
                remote_ports = match.group(2)
                local = match.group(3).strip()
                host, _, local_ports = local.rpartition(':')
                host = (host.strip() or '127.0.0.1')
                if host.lower() == 'localhost':
                    host = '127.0.0.1'
                if not _LOCAL_HOST_RE.fullmatch(host):
                    logging.warning(f"Skipping extra port spec '{spec}': invalid local host '{host}'.")
                    continue
                if not re.fullmatch(r'\d+(?:-\d+)?', local_ports.strip()):
                    logging.warning(f"Skipping extra port spec '{spec}': invalid local destination.")
                    continue
                local_ports = local_ports.strip()

                remote_lo, _, remote_hi = remote_ports.partition('-')
                local_lo, _, local_hi = local_ports.partition('-')
                if remote_hi or local_hi:
                    # Ranges only make sense for L4 proxies; HTTP ports need
                    # per-port Nginx server blocks and can't span a range.
                    if scheme not in ('raw', 'tcp', 'udp'):
                        logging.warning(
                            f"Skipping extra port spec '{spec}': port ranges require "
                            "a raw/tcp/udp scheme.")
                        continue
                    if bool(remote_hi) != bool(local_hi) or \
                            int(remote_hi or 0) - int(remote_lo) != int(local_hi or 0) - int(local_lo):
                        logging.warning(
                            f"Skipping extra port spec '{spec}': remote and local "
                            "ranges must have equal length.")
                        continue
                    # FRP expands a 'range:' proxy into one proxy per port
                    lines += [
                        '[[proxies]]',
                        f'name = "range:{tunnel_id}-x{remote_lo}"',
                        f'type = "{"udp" if scheme == "udp" else "tcp"}"',
                        f'localIP = "{host}"',
                        f'localPort = "{local_ports}"',
                        f'remotePort = "{remote_ports}"',
                        '',
                    ]
                    continue

                lines += [
                    '[[proxies]]',
                    f'name = "{tunnel_id}-x{remote_ports}"',
                    f'type = "{"udp" if scheme == "udp" else "tcp"}"',
                    f'localIP = "{host}"',
                    f'localPort = {local_lo}',
                    f'remotePort = {remote_lo}',
                    '',
                ]

        return "\n".join(lines)

    def _write_frpc_config(self, server_id: str, server_ip: str, frp_token: str) -> tuple[bool, str | None]:
        """Writes frpc.toml for the server. Returns (changed, toml_path)."""
        content = self._build_frpc_config(server_id, server_ip, frp_token)
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        toml_path = self._toml_path_for(server_id)
        try:
            if self.config_hashes.get(server_id) != digest or not os.path.exists(toml_path):
                with open(toml_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(content)
                # Contains the FRP token + admin API password — keep it
                # readable only by this user (no-op effect on Windows ACLs).
                try: os.chmod(toml_path, 0o600)
                except OSError: pass
                self.config_hashes[server_id] = digest
                return True, toml_path
            return False, toml_path
        except OSError as e:
            logging.error(f"Failed to write {toml_path}: {e}")
            return False, None

    def _remove_frpc_config(self, server_id: str):
        """Deletes a server's toml — the daemon notices and stops its frpc."""
        toml_path = self._toml_path_for(server_id)
        try:
            if os.path.exists(toml_path):
                os.remove(toml_path)
                logging.info(f"Removed {toml_path}; daemon will stop frpc for {server_id}.")
        except OSError as e:
            logging.error(f"Failed to remove {toml_path}: {e}")
        with self._lock:
            self.config_hashes.pop(server_id, None)
            port = self._admin_ports.pop(server_id, None)
            if port:
                self._admin_port_used.discard(port)
            self.frp_daemons.pop(server_id, None)
            self._admin_creds.pop(server_id, None)
            self._transport_step.pop(server_id, None)
            self._transport_dead_since.pop(server_id, None)
            self._transport_restart.discard(server_id)
            self.daemon_logs.pop(server_id, None)

    # ------------------------------------------------------------------
    # Daemon service + reload
    # ------------------------------------------------------------------

    def _ensure_daemon_started(self) -> bool:
        """Registers + starts the OS daemon service (with backoff)."""
        if time.time() - self._last_service_attempt.get('_daemon', 0) < self.DAEMON_RESTART_BACKOFF_S:
            return False
        self._last_service_attempt['_daemon'] = time.time()
        ok = self._ensure_daemon()
        if ok:
            # Runs every reconcile loop — log a few times then stay quiet.
            self._daemon_ok_logs = getattr(self, '_daemon_ok_logs', 0) + 1
            if self._daemon_ok_logs <= 3:
                logging.debug("NerazimNet daemon service ensured running.")
        else:
            logging.error("Failed to ensure NerazimNet daemon service.")
        return ok

    def _reload_daemon(self, server_id: str, toml_path: str) -> bool:
        """Hot-reloads the daemon's proxy table via `frpc reload`."""
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            result = subprocess.run(
                self.frpc_command + ['reload', '-c', toml_path],
                capture_output=True, text=True, timeout=15,
                creationflags=creationflags
            )
            if result.returncode == 0:
                logging.info(f"frpc reload succeeded for server {server_id}.")
                return True
            logging.error(f"frpc reload failed for server {server_id}: {result.stderr.strip() or result.stdout.strip()}")
            return False
        except Exception as e:
            logging.error(f"frpc reload error for server {server_id}: {e}")
            return False

    def _stop_frpc(self, server_id: str, toml_path: str) -> bool:
        """Asks frpc to exit; the daemon respawns it with the new config."""
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            result = subprocess.run(
                self.frpc_command + ['stop', '-c', toml_path],
                capture_output=True, text=True, timeout=15,
                creationflags=creationflags
            )
            if result.returncode == 0:
                logging.info(f"frpc stopped for server {server_id}; daemon will respawn it.")
                self._respawning[server_id] = time.time()
                return True
            logging.error(f"frpc stop failed for server {server_id}: {result.stderr.strip() or result.stdout.strip()}")
            return False
        except Exception as e:
            logging.error(f"frpc stop error for server {server_id}: {e}")
            return False

    @staticmethod
    def _diagnose_unreachable(server: dict) -> str:
        """All transports timed out on :7000 — distinguish 'VPS down'
        from 'server up but frps not listening/blocked' by probing the
        web ports provisioning set up."""
        ip = server.get('ip_address')
        if not ip:
            return 'Server unreachable — check VPS firewall / frps'
        for port in (443, 80):
            try:
                with socket.create_connection((ip, port), timeout=3):
                    return ('frps not reachable on :7000 — service down, '
                            'port blocked, or server not provisioned. '
                            'Right-click the server and choose '
                            'Provision / Re-run Setup.')
            except OSError:
                continue
        return 'Server unreachable — VPS down or network blocked'

    @staticmethod
    def _frps_port_reachable(server: dict) -> bool:
        """Liveness probe: does the frps port accept a TCP connection?
        If not, nothing frpc dials can work (a provisioned server binds
        TCP+UDP on the same port) — evidence now, not after a 30s wait."""
        ip = server.get('ip_address')
        if not ip:
            return True  # can't probe — don't fast-fail on missing data
        try:
            with socket.create_connection(
                    (ip, TunnelManager.FRP_SERVER_PORT), timeout=3):
                return True
        except OSError:
            return False

    def _check_transport_health(self, server_id: str, entries: list):
        """Auto transport fallback: when the admin API is up but every proxy
        stays in 'new'/'wait start' for a sustained window, the configured
        protocol can't reach frps (e.g. UDP blocked) — advance the chain."""
        server = self.controller.get_object_by_id(server_id) or {}
        # 'Pinned' means a value inside the chain — anything else (unset,
        # 'auto', or a synced value we don't recognize) behaves as auto,
        # matching _resolve_transport's fallthrough.
        mode = str(server.get('transport') or 'auto').strip().lower()
        if mode in self.TRANSPORT_CHAIN:
            self._transport_dead_since.pop(server_id, None)
            return
        if not entries:
            # frpc only registers proxies after a successful control
            # login, so with loginFailExit=false an up-but-empty API while
            # tunnels are desired IS dead-transport evidence — the login
            # itself never completed. Only when nothing is expected on
            # this server does empty status prove nothing.
            if not self._desired_servers().get(server_id):
                self._transport_dead_since.pop(server_id, None)
                self._transport_dead_final.discard(server_id)
                self._unreachable_reason.pop(server_id, None)
                return
        else:
            # A status past 'new'/'wait start' means the control
            # connection reached frps — even 'start error' proves the
            # transport works.
            live = any(e.get('status') not in ('new', 'wait start', '', None)
                       for e in entries)
            if live:
                self._transport_dead_since.pop(server_id, None)
                self._transport_dead_final.discard(server_id)
                self._unreachable_reason.pop(server_id, None)
                return
        advanced = None
        with self._lock:
            since = self._transport_dead_since.setdefault(server_id, time.time())
            step = self._transport_step.get(server_id, 0)
            elapsed = time.time() - since
            dead = elapsed >= self.TRANSPORT_FALLBACK_AFTER_S
        if not dead and elapsed >= self.FAST_DEAD_AFTER_S:
            # Port probe outside the lock — a filtered port costs the
            # full 3s socket timeout.
            dead = not self._frps_port_reachable(server)
        with self._lock:
            if dead and step < len(self.TRANSPORT_CHAIN) - 1:
                advanced = (self.TRANSPORT_CHAIN[step],
                            self.TRANSPORT_CHAIN[step + 1], int(elapsed))
                self._transport_step[server_id] = step + 1
                self._transport_dead_since[server_id] = time.time()
                self._transport_restart.add(server_id)
            elif dead and server_id not in self._transport_dead_final:
                # Last transport also timed out — every protocol is
                # blocked or frps is down. Surface it as unreachable
                # with a diagnosis (VPS down vs frps not provisioned).
                # Mark once — this branch fires every poll otherwise.
                self._transport_dead_final.add(server_id)
                reason = self._diagnose_unreachable(server)
                self._unreachable_reason[server_id] = reason
                logging.error(
                    f"All FRP transports failed for {server.get('name', server_id)}: "
                    f"{reason}")
                try:
                    name = server.get('name', server_id)
                    self.controller.after(0, lambda r=reason, n=name:
                        self.controller.show_notification(
                            "All transports failed", f"{n}: {r}"))
                except Exception:
                    pass
        if advanced:
            old, new, elapsed_s = advanced
            msg = (f"FRP transport '{old}' unreachable on server "
                   f"{server.get('name', server_id)} ({elapsed_s}s); "
                   f"falling back to '{new}'.")
            logging.warning(msg)
            try:
                self.controller.after(0, lambda: self.controller.show_notification(
                    "Transport fallback", msg))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _desired_servers(self) -> dict:
        """Groups desired (non-local) tunnels by server_id; prunes deleted tunnels."""
        by_server = defaultdict(set)
        stale_ids = []
        with self._lock:
            desired = list(self.desired_tunnels)
        for tunnel_id in desired:
            tunnel = self.controller.get_object_by_id(tunnel_id)
            if not tunnel:
                stale_ids.append(tunnel_id)
                continue
            if tunnel.get('route_type', 'tunnel') == 'local':
                continue
            server_id = tunnel.get('server_id')
            if server_id:
                by_server[server_id].add(tunnel_id)
        if stale_ids:
            with self._lock:
                for tid in stale_ids:
                    self.desired_tunnels.discard(tid)
        return by_server

    def _reconcile(self):
        """Ensures frpc configs + daemon service match the desired tunnel set."""
        if not self._reconcile_lock.acquire(blocking=False):
            return # Another reconcile is already in flight
        try:
            self._reconcile_inner()
        finally:
            self._reconcile_lock.release()

    def _reconcile_inner(self):
        creds = self.controller.get_automation_credentials()
        frp_token = (creds or {}).get('frp_token')
        desired_by_server = self._desired_servers()

        # Remove configs for servers that no longer have desired tunnels —
        # the daemon stops those frpc processes (and exits when none remain).
        configured = {os.path.basename(p)[len('frpc_'):-len('.toml')]
                      for p in glob.glob(os.path.join(self.frp_config_dir, 'frpc_*.toml'))}
        configured |= set(self.frp_daemons.keys()) | set(self.config_hashes.keys())
        for server_id in configured:
            if server_id not in desired_by_server:
                self._remove_frpc_config(server_id)

        if not desired_by_server:
            self._write_desired_state()
            return

        if not frp_token:
            logging.error("FRP token not configured; cannot start tunnels.")
            return

        wrote_any = False
        for server_id in desired_by_server:
            server = self.controller.get_object_by_id(server_id)
            if not server:
                continue
            server_ip = server.get('ip_address')
            if not server_ip:
                continue

            changed, toml_path = self._write_frpc_config(server_id, server_ip, frp_token)
            if not toml_path:
                continue
            wrote_any = True

            # Track the daemon entry (admin port known from config render).
            # Always re-sync admin_port — _alloc_admin_port may have moved
            # off a port held by a stale orphan frpc.
            with self._lock:
                entry = self.frp_daemons.setdefault(
                    server_id, {'api_up': False})
                entry['admin_port'] = self._admin_ports.get(
                    server_id, self.ADMIN_PORT_BASE)
                if server_id not in self.daemon_logs:
                    self.daemon_logs[server_id] = deque(maxlen=1000)

            daemon = self.frp_daemons.get(server_id, {})
            if not daemon.get('api_up'):
                # Daemon service may need a nudge; frpc itself may still be
                # starting up — ensure_service is cheap and idempotent.
                self._transport_restart.discard(server_id)
                self._ensure_daemon_started()
            elif server_id in self._transport_restart:
                # Transport changes live outside `frpc reload`'s proxy
                # scope — stop frpc and let the daemon respawn it on the
                # new protocol. Checked independently of `changed`: the toml
                # may already have been rewritten on an earlier pass.
                self._transport_restart.discard(server_id)
                self._stop_frpc(server_id, toml_path)
            elif changed:
                # Daemon alive and config changed — hot reload
                self._reload_daemon(server_id, toml_path)

        if wrote_any:
            self._write_desired_state()

    def _monitor_tunnels(self):
        """Background loop: reconcile configs + poll proxy/local health."""
        while self._is_monitoring:
            try:
                self._reconcile()
                self._poll_proxy_statuses()

                now = time.time()
                if now - self._last_health_check >= self.LOCAL_HEALTH_INTERVAL_S:
                    self._last_health_check = now
                    self._check_local_routes()

                self._monitor_stop.wait(5)
            except Exception as e:
                logging.error(f"Error in tunnel monitor thread: {e}", exc_info=True)
                self._monitor_stop.wait(10)

    def _poll_proxy_statuses(self):
        """Queries each server's frpc admin API and caches per-tunnel status."""
        with self._lock:
            daemons = dict(self.frp_daemons)
        statuses = {}
        for server_id, daemon in daemons.items():
            api_up = False
            try:
                # http.client on purpose: requests/urllib honor system and
                # env proxy settings, which must never apply to loopback.
                conn = http.client.HTTPConnection(
                    '127.0.0.1', daemon['admin_port'], timeout=2)
                headers = {}
                password = self._admin_creds.get(server_id)
                if password:
                    import base64
                    token = base64.b64encode(f'nerazim:{password}'.encode()).decode()
                    headers['Authorization'] = f'Basic {token}'
                conn.request('GET', '/api/status', headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                if resp.status == 200:
                    api_up = True
                    payload = json.loads(body)
                    server_entries = []
                    for proxy_list in payload.values():
                        if not isinstance(proxy_list, list):
                            continue
                        for entry in proxy_list:
                            server_entries.append(entry)
                            name = entry.get('name', '')
                            tid = name.split('-x')[0] if '-x' in name else name
                            # Main proxy wins; record extra-proxy errors too
                            if tid not in statuses or name == tid:
                                statuses[tid] = entry
                            elif entry.get('status') != 'running' and entry.get('err'):
                                statuses.setdefault(f"{tid}::extra_error", entry)
                    self._check_transport_health(server_id, server_entries)
                else:
                    logging.debug(f"frpc admin API for {server_id} returned {resp.status}")
            except (OSError, ValueError):
                # Admin API not up yet (daemon still connecting)
                pass
            with self._lock:
                if server_id in self.frp_daemons:
                    self.frp_daemons[server_id]['api_up'] = api_up
                if api_up:
                    self._respawning.pop(server_id, None)
                    self._api_down_since.pop(server_id, None)
                if not api_up:
                    self._api_down_since.setdefault(server_id, time.time())
                    # API downtime isn't evidence about the transport — but
                    # a dead frps port IS: when the control port won't take
                    # TCP, every remaining transport fails identically, so
                    # converge to the terminal state instead of resetting
                    # it forever. Only probe when the API has been down a
                    # little while — a planned transport-switch respawn
                    # shouldn't hit the network.
                    api_down_elapsed = (
                        time.time() - self._api_down_since[server_id])
                    if (api_down_elapsed >= self.FAST_DEAD_AFTER_S
                            and server_id not in self._transport_dead_final):
                        server_obj = (self.controller
                                      .get_object_by_id(server_id)) or {}
                        if (self._desired_servers().get(server_id)
                                and not self._frps_port_reachable(server_obj)):
                            self._transport_dead_final.add(server_id)
                            reason = self._diagnose_unreachable(server_obj)
                            self._unreachable_reason[server_id] = reason
                            logging.error(
                                f"All FRP transports failed for "
                                f"{server_obj.get('name', server_id)}: {reason}")
                    # API downtime isn't evidence either way — don't let it
                    # count toward the fallback window. dead_final stays
                    # latched: once unreachable is proven it must render,
                    # not get erased by the next api-down poll.
                    self._transport_dead_since.pop(server_id, None)
        with self._lock:
            self.proxy_statuses = statuses

    def _check_local_routes(self):
        """Health-checks desired 'local' routes via their public HTTPS endpoint."""
        with self._lock:
            desired = list(self.desired_tunnels)
        for tunnel_id in desired:
            tunnel = self.controller.get_object_by_id(tunnel_id)
            if not tunnel or tunnel.get('route_type', 'tunnel') != 'local':
                with self._lock:
                    self.local_route_health.pop(tunnel_id, None)
                continue
            hostname = tunnel.get('hostname')
            if not hostname:
                continue
            try:
                resp = requests.head(f"https://{hostname}", timeout=5, allow_redirects=True)
                entry = {'ok': True, 'message': f"Connected (HTTP {resp.status_code})"}
            except requests.RequestException as e:
                entry = {'ok': False, 'message': f"Route unreachable: {e.__class__.__name__}"}
            with self._lock:
                self.local_route_health[tunnel_id] = entry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def shutdown(self):
        """App exit: stop the monitor only — frpc daemons are owned by the
        OS-level service and keep running so tunnels persist."""
        logging.info("Stopping tunnel monitor (daemons keep running).")
        self._is_monitoring = False
        self._monitor_stop.set()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=3)
            if self.monitor_thread.is_alive():
                logging.warning("Tunnel monitor thread did not exit cleanly.")

    def stop_all_tunnels(self):
        """Stops all tunnels by clearing desired state and removing configs —
        the daemon stops each frpc and exits when none remain."""
        logging.info("Stopping all active tunnels...")
        with self._lock:
            self.desired_tunnels.clear()
        try:
            self._reconcile()
        except Exception as e:
            logging.error(f"Reconcile failed during stop_all_tunnels: {e}")
        logging.info("All tunnels stopped.")
        self.controller.refresh_dashboard()

    def start_tunnel(self, tunnel_id: str) -> tuple[bool, str]:
        with self._lock:
            already_desired = tunnel_id in self.desired_tunnels
            self.tunnel_error_messages.pop(tunnel_id, None)

        if already_desired:
            # Retry path — the tunnel is desired but hasn't connected
            # (stuck at 'Starting frpc daemon…' / 'Connecting…'). Reset
            # backoff + stall tracking and bounce frpc so the daemon
            # respawns it with a fresh config load.
            tunnel_config = self.controller.get_object_by_id(tunnel_id)
            server_id = (tunnel_config or {}).get('server_id')
            with self._lock:
                self._starting_since.pop(tunnel_id, None)
                self._last_service_attempt.pop('_daemon', None)
            daemon = self.frp_daemons.get(server_id or '', {})
            if server_id and daemon.get('api_up'):
                self._stop_frpc(server_id, self._toml_path_for(server_id))
            try:
                self._reconcile()
            except Exception as e:
                logging.error(f"Reconcile failed retrying tunnel {tunnel_id}: {e}")
            if hasattr(self.controller, 'after'):
                self.controller.after(0, self.controller.refresh_dashboard)
            logging.info(f"Retrying already-desired tunnel {tunnel_id}.")
            return True, "Tunnel start re-requested."

        tunnel_config = self.controller.get_object_by_id(tunnel_id)
        if not tunnel_config:
            return False, "Tunnel configuration not found."

        my_device_id = self.controller.get_my_device_id()
        assigned_device_id = tunnel_config.get('client_device_id')
        if not assigned_device_id or assigned_device_id != my_device_id:
            name = self.controller.get_client_name(assigned_device_id) or "another device"
            msg = f"Cannot start tunnel. It is assigned to '{name}'."
            logging.info(msg)
            return False, msg

        server_config = self.controller.get_object_by_id(tunnel_config.get('server_id'))
        if not server_config:
            return False, "Server config not found."

        automation_creds = self.controller.get_automation_credentials()
        if not automation_creds or not automation_creds.get('frp_token'):
            msg = "Automation credentials (FRP token) not found."
            logging.error(msg)
            return False, f"{msg}\nPlease configure in Settings."

        is_local_route = tunnel_config.get('route_type', 'tunnel') == 'local'
        if not is_local_route and not self._parse_local_dest(tunnel_config.get('local_destination', '')):
            return False, "Invalid local destination (expected 'host:port')."

        with self._lock:
            self.desired_tunnels.add(tunnel_id)
            self.tunnel_logs[tunnel_id] = deque(maxlen=500)
            route_label = "Local route" if is_local_route else "Tunnel"
            self.tunnel_logs[tunnel_id].append(
                f"--- {route_label} '{tunnel_config.get('hostname')}' requested ---\n")

        logging.info(f"Starting {tunnel_config.get('route_type', 'tunnel')} "
                     f"'{tunnel_config.get('hostname')}' (ID: {tunnel_id})")

        # Reconcile immediately rather than waiting for the monitor tick
        try:
            self._reconcile()
        except Exception as e:
            logging.error(f"Reconcile failed after starting tunnel {tunnel_id}: {e}")

        if hasattr(self.controller, 'after'):
            self.controller.after(0, self.controller.refresh_dashboard)
        return True, "Tunnel start requested."

    def stop_tunnel(self, tunnel_id: str, refresh_ui: bool = True):
        """Stops a tunnel by removing it from desired state and reloading frpc."""
        logging.debug(f"Attempting to stop tunnel {tunnel_id}. Refresh UI: {refresh_ui}")
        with self._lock:
            was_desired = tunnel_id in self.desired_tunnels
            self.desired_tunnels.discard(tunnel_id)
            self.tunnel_error_messages.pop(tunnel_id, None)
            self.local_route_health.pop(tunnel_id, None)
            if tunnel_id in self.tunnel_logs:
                self.tunnel_logs[tunnel_id].append("--- Tunnel stop requested ---\n")

        if not was_desired:
            logging.debug(f"[{tunnel_id}] Tunnel was not running.")
        else:
            try:
                self._reconcile()
            except Exception as e:
                logging.error(f"Reconcile failed after stopping tunnel {tunnel_id}: {e}")

        if refresh_ui:
            try:
                if hasattr(self.controller, 'after'):
                    self.controller.after(0, self.controller.refresh_dashboard)
            except Exception as e:
                logging.error(f"[{tunnel_id}] Error scheduling UI refresh: {e}")

    def start_all_tunnels(self):
        """Starts tunnels marked for auto-start on this device."""
        logging.info("Starting auto-start tunnels assigned to this device...")
        my_device_id = self.controller.get_my_device_id()
        all_tunnels = self.controller.get_tunnels()

        tunnels_to_start = [
            t for t in all_tunnels
            if my_device_id in t.get('auto_start_on_device_ids', [])
        ]

        logging.info(f"Found {len(tunnels_to_start)} tunnels configured to auto-start on this device.")
        started_count = 0
        failed_count = 0
        for tunnel in tunnels_to_start:
            success, _ = self.start_tunnel(tunnel['id'])
            if success:
                started_count += 1
            else:
                failed_count += 1

        logging.info(f"Attempted to start {started_count} auto-start tunnels ({failed_count} failures).")
        self.controller.after(50, self.controller.refresh_dashboard)

    def get_tunnel_statuses(self) -> dict:
        """Maps cached frpc proxy statuses onto tunnel IDs for the UI."""
        statuses = {}
        with self._lock:
            desired = set(self.desired_tunnels)
            proxy_statuses = dict(self.proxy_statuses)
            local_health = dict(self.local_route_health)
            errors = dict(self.tunnel_error_messages)
            daemons = dict(self.frp_daemons)

        all_tunnel_configs = self.controller.get_tunnels()
        my_device_id = self.controller.get_my_device_id()

        for tunnel in all_tunnel_configs:
            tid = tunnel['id']
            assigned_id = tunnel.get('client_device_id')
            is_local = tunnel.get('route_type', 'tunnel') == 'local'

            if assigned_id != my_device_id:
                client_name = self.controller.get_client_name(assigned_id) or "another device"
                statuses[tid] = {'status': 'disabled',
                                 'message': f'Managed by {client_name}' if assigned_id else 'Stopped (Unassigned)'}
                continue

            if tid not in desired:
                statuses[tid] = {'status': 'stopped', 'message': 'Stopped'}
                continue

            # Desired on this device
            if is_local:
                health = local_health.get(tid)
                if health is None:
                    statuses[tid] = {'status': 'running', 'message': 'Route synced (checking...)'}
                elif health['ok']:
                    statuses[tid] = {'status': 'running', 'message': health['message']}
                else:
                    statuses[tid] = {'status': 'error', 'message': health['message']}
                continue

            if tid in errors:
                statuses[tid] = {'status': 'error', 'message': errors[tid]}
                continue

            server_id = tunnel.get('server_id')
            daemon = daemons.get(server_id)
            if server_id in self._transport_dead_final:
                msg = (self._unreachable_reason.get(server_id)
                       or 'Server unreachable — check VPS firewall / frps (port 7000)')
                statuses[tid] = {'status': 'error', 'message': msg,
                                 'short': 'Unreachable :7000'}
                continue
            if not daemon or not daemon.get('api_up'):
                # A planned transport restart takes ~5-10s of api downtime;
                # label it as such instead of the generic startup state.
                if time.time() - self._respawning.get(server_id, 0) < 30:
                    statuses[tid] = {'status': 'stopped',
                                     'message': 'Restarting frpc (transport switch)...',
                                     'short': 'frpc restarting…'}
                    continue
                # Escalate by how long the API has been down — a respawn
                # should land in seconds; anything longer means frpc is
                # crash-looping or never spawned, which is an error, not
                # "starting". setdefault seeds the timer even when the
                # poll loop never ran for this server (missing daemon
                # entry) — otherwise every render defaults to now and the
                # row shows "(0s)" forever.
                if server_id:
                    down = time.time() - self._api_down_since.setdefault(
                        server_id, time.time())
                else:
                    down = 0
                if down >= self.API_DOWN_FAIL_S:
                    statuses[tid] = {'status': 'error',
                                     'message': 'frpc daemon not responding — check Debug view',
                                     'short': 'frpc not responding'}
                elif down >= self.API_DOWN_WARN_S:
                    statuses[tid] = {'status': 'stopped',
                                     'message': f'frpc slow to start — still retrying... ({int(down)}s)',
                                     'short': f'frpc slow start ({int(down)}s)'}
                else:
                    statuses[tid] = {'status': 'stopped',
                                     'message': f'Starting frpc daemon... ({int(down)}s)',
                                     'short': f'frpc starting ({int(down)}s)'}
                continue

            server_obj = self.controller.get_object_by_id(server_id) or {}
            mode = str(server_obj.get('transport') or 'auto').strip().lower()
            transport = self._resolve_transport(server_id)
            if mode in self.TRANSPORT_CHAIN:  # pinned — no chain to show
                trying = f"Connecting via {transport}..."
                trying_short = f'{transport}…'
            else:
                step = self._transport_step.get(server_id, 0)
                # Show the fallback countdown — otherwise the row sits on
                # identical text for the whole probe window and looks dead.
                since = self._transport_dead_since.get(server_id)
                rem = (max(0, int(self.TRANSPORT_FALLBACK_AFTER_S
                                  - (time.time() - since)))
                       if since else self.TRANSPORT_FALLBACK_AFTER_S)
                trying = (f"Connecting via {transport} "
                          f"({step + 1} of {len(self.TRANSPORT_CHAIN)}, ~{rem}s)...")
                trying_short = (f'{transport} ({step + 1}/{len(self.TRANSPORT_CHAIN)},'
                                f' ~{rem}s)')
            proxy = proxy_statuses.get(tid)
            if proxy is None:
                statuses[tid] = {'status': 'stopped', 'message': trying,
                                 'short': trying_short}
                continue

            proxy_status = (proxy.get('status') or '').lower()
            extra = proxy_statuses.get(f"{tid}::extra_error")
            if proxy_status in ('new', 'wait start'):
                # frpc is up but the control connection hasn't landed —
                # transitional, not an error.
                statuses[tid] = {'status': 'stopped', 'message': trying,
                                 'short': trying_short}
            elif proxy_status == 'running':
                conns = proxy.get('cur_conns')
                message = f"Connected ({conns} conn)" if conns is not None else 'Connected'
                if extra and extra.get('err'):
                    message = f"Connected (extra port {extra.get('remote_port', '?')} failed)"
                statuses[tid] = {'status': 'running', 'message': message}
            else:
                err = proxy.get('err') or (extra or {}).get('err') or proxy.get('status', 'Unknown')
                statuses[tid] = {'status': 'error', 'message': err}

        self._apply_startup_stall(statuses, all_tunnel_configs)
        self._notify_status_transitions(all_tunnel_configs, statuses)
        return statuses

    STARTUP_STALL_S = 45  # transitional "Starting…/Connecting…" may persist this long
    # frpc's admin API should answer within seconds of a (re)spawn —
    # 20s of silence means slow/crash-looping, 40s means it's dead.
    API_DOWN_WARN_S = 20
    API_DOWN_FAIL_S = 40

    def _apply_startup_stall(self, statuses: dict, tunnel_configs: list = None):
        """Escalate tunnels stuck in a transitional startup state to
        'error' so they don't sit at 'Starting frpc daemon...' forever.
        Tunnels on an auto-transport server are exempt while the chain
        is still probing — the real failure state ('server unreachable')
        is set separately once every transport has timed out."""
        servers_by_tid = {}
        if tunnel_configs:
            for t in tunnel_configs:
                if t.get('type') == 'tunnel':
                    servers_by_tid[t.get('id')] = t.get('server_id')

        def still_probing(tid):
            server_id = servers_by_tid.get(tid)
            if not server_id or server_id in self._transport_dead_final:
                return False
            # Probing only progresses while the admin API answers — an
            # api-down server can't accumulate dead-transport evidence,
            # so it must not be exempt from the stall watchdog.
            daemon = self.frp_daemons.get(server_id)
            if not (daemon and daemon.get('api_up')):
                return False
            server = self.controller.get_object_by_id(server_id) or {}
            mode = str(server.get('transport') or 'auto').strip().lower()
            return mode not in self.TRANSPORT_CHAIN  # auto chain may still advance

        now = time.time()
        for tid, info in statuses.items():
            transitional = (info.get('status') == 'stopped'
                            and info.get('message') not in (None, 'Stopped'))
            if transitional:
                if still_probing(tid):
                    continue  # transport chain still cycling — not a stall
                since = self._starting_since.setdefault(tid, now)
                if now - since > self.STARTUP_STALL_S:
                    statuses[tid] = {'status': 'error',
                                     'message': 'Startup stalled — view logs'}
            else:
                self._starting_since.pop(tid, None)
        # Drop trackers for tunnels no longer reported.
        for tid in set(self._starting_since) - set(statuses):
            self._starting_since.pop(tid, None)

    def _notify_status_transitions(self, tunnel_configs, statuses: dict):
        """Fires a desktop notification when a tunnel drops or reconnects.

        Only running->error (drop) and error->running (reconnect) transitions
        notify; 'stopped'/'disabled' are user actions and the first poll after
        launch has no baseline, so neither alerts.
        """
        hostnames = {t['id']: (t.get('hostname') or t['id']) for t in tunnel_configs}
        for tid, info in statuses.items():
            old = self._prev_statuses.get(tid)
            new = info.get('status')
            name = hostnames.get(tid, tid)
            if old == 'running' and new == 'error':
                self._notify('Tunnel down', f"{name}: {info.get('message', 'error')}")
            elif old == 'error' and new == 'running':
                self._notify('Tunnel reconnected', name)
        self._prev_statuses = {tid: s.get('status') for tid, s in statuses.items()}

    def _notify(self, title: str, message: str):
        notify_fn = getattr(self.controller, 'show_notification', None)
        if notify_fn:
            try:
                notify_fn(title, message)
            except Exception as e:
                logging.debug(f"Notification callback failed: {e}")

    def drop_tunnel_state(self, tunnel_id: str):
        """Forget all per-tunnel bookkeeping for a deleted tunnel —
        without this, log deques and status entries accumulate forever."""
        with self._lock:
            self.desired_tunnels.discard(tunnel_id)
            self.tunnel_logs.pop(tunnel_id, None)
            self._prev_statuses.pop(tunnel_id, None)
            self.tunnel_error_messages.pop(tunnel_id, None)
            self.local_route_health.pop(tunnel_id, None)

    def get_tunnel_log(self, tunnel_id: str) -> str:
        """Returns tunnel-specific events plus the owning server's frpc log."""
        tunnel = self.controller.get_object_by_id(tunnel_id)
        server_id = tunnel.get('server_id') if tunnel else None
        with self._lock:
            parts = []
            if tunnel_id in self.tunnel_logs:
                parts.append("".join(list(self.tunnel_logs[tunnel_id])))
            error_msg = self.tunnel_error_messages.get(tunnel_id)
            if error_msg:
                parts.append(f"--- Last error ---\n{error_msg}\n")
        # frpc writes to its own log file now (owned by the daemon, not us)
        if server_id:
            frpc_log = os.path.join(get_app_data_dir(), 'logs', f'frpc_{server_id}.log')
            try:
                if os.path.exists(frpc_log):
                    with open(frpc_log, 'r', encoding='utf-8', errors='replace') as f:
                        tail = deque(f, maxlen=200)
                    if tail:
                        parts.append("--- frpc daemon log ---\n" + "".join(tail))
            except OSError:
                pass
        return "".join(parts) if parts else "No logs available for this tunnel yet."
