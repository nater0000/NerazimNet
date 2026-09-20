import subprocess
import logging
import os
import sys
import threading
import time
import re
import json
import glob
import hashlib
from collections import deque, defaultdict

import requests

from utils.staging import stage_bundled_exe
from utils.paths import get_app_data_dir, EXE_EXT
from utils.services import ensure_daemon_running


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
        self._admin_ports = {}                # { server_id: port }
        self._admin_port_used = set()
        self._last_service_attempt = {}       # { '_daemon': timestamp } service-start backoff
        self._lock = threading.Lock()
        self._reconcile_lock = threading.Lock() # Serializes daemon/config reconciliation

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
        if port:
            return port
        port = self.ADMIN_PORT_BASE
        while port in self._admin_port_used:
            port += 1
        self._admin_ports[server_id] = port
        self._admin_port_used.add(port)
        return port

    def _admin_port_from_toml(self, toml_path: str) -> int | None:
        """Reads the [webServer] port back out of an existing frpc.toml so
        admin ports stay stable across app restarts (needed for adoption)."""
        try:
            in_webserver = False
            with open(toml_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('['):
                        in_webserver = (line == '[webServer]')
                        continue
                    if in_webserver and line.startswith('port'):
                        return int(line.split('=', 1)[1].strip())
        except (OSError, ValueError):
            pass
        return None

    def _recover_state(self):
        """Rebuilds admin-port map and desired-tunnel set from files on disk,
        so a relaunched GUI reflects tunnels the daemon is already running."""
        for toml_path in glob.glob(os.path.join(self.frp_config_dir, 'frpc_*.toml')):
            server_id = os.path.basename(toml_path)[len('frpc_'):-len('.toml')]
            port = self._admin_port_from_toml(toml_path)
            if server_id and port:
                self._admin_ports[server_id] = port
                self._admin_port_used.add(port)
                self.frp_daemons[server_id] = {'admin_port': port, 'api_up': False}
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
        try:
            return host, int(port_str.strip())
        except ValueError:
            return None

    def _build_frpc_config(self, server_id: str, server_ip: str, frp_token: str) -> str:
        """Renders the frpc.toml content for every *desired* tunnel on a server."""
        admin_port = self._alloc_admin_port(server_id)
        my_device_id = self.controller.get_my_device_id()
        lines = [
            f'serverAddr = "{server_ip}"',
            f'serverPort = {self.FRP_SERVER_PORT}',
            '',
            '[auth]',
            f'token = "{frp_token}"',
            '',
            '[transport]',
            'protocol = "quic"',
            'tls.enable = true',
            '',
            '[webServer]',
            'addr = "127.0.0.1"',
            f'port = {admin_port}',
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

            # Extra service ports (e.g. '7880:localhost:7880, raw:7881:localhost:7881')
            for spec in (tunnel.get('extra_ports') or '').split(','):
                spec = spec.strip()
                if not spec:
                    continue
                match = re.fullmatch(r'(?:(raw|tcp|udp|http|wss):)?(\d+):(.+)', spec, re.IGNORECASE)
                if not match:
                    logging.warning(f"Skipping invalid extra port spec '{spec}' for tunnel {tunnel_id}.")
                    continue
                scheme = (match.group(1) or 'http').lower()
                extra_remote = int(match.group(2))
                parsed_extra = self._parse_local_dest(match.group(3).strip())
                if not parsed_extra:
                    logging.warning(f"Skipping extra port spec '{spec}': invalid local destination.")
                    continue
                extra_ip, extra_local_port = parsed_extra
                lines += [
                    '[[proxies]]',
                    f'name = "{tunnel_id}-x{extra_remote}"',
                    f'type = "{"udp" if scheme == "udp" else "tcp"}"',
                    f'localIP = "{extra_ip}"',
                    f'localPort = {extra_local_port}',
                    f'remotePort = {extra_remote}',
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
            logging.info("NerazimNet daemon service ensured running.")
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

            # Track the daemon entry (admin port known from config render)
            with self._lock:
                if server_id not in self.frp_daemons:
                    self.frp_daemons[server_id] = {
                        'admin_port': self._admin_ports.get(server_id, self.ADMIN_PORT_BASE),
                        'api_up': False,
                    }
                if server_id not in self.daemon_logs:
                    self.daemon_logs[server_id] = deque(maxlen=1000)

            daemon = self.frp_daemons.get(server_id, {})
            if not daemon.get('api_up'):
                # Daemon service may need a nudge; frpc itself may still be
                # starting up — ensure_service is cheap and idempotent.
                self._ensure_daemon_started()
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

                time.sleep(5)
            except Exception as e:
                logging.error(f"Error in tunnel monitor thread: {e}", exc_info=True)
                time.sleep(10)

    def _poll_proxy_statuses(self):
        """Queries each server's frpc admin API and caches per-tunnel status."""
        with self._lock:
            daemons = dict(self.frp_daemons)
        statuses = {}
        for server_id, daemon in daemons.items():
            api_up = False
            try:
                resp = requests.get(
                    f"http://127.0.0.1:{daemon['admin_port']}/api/status", timeout=2)
                if resp.ok:
                    api_up = True
                    payload = resp.json()
                    for proxy_list in payload.values():
                        if not isinstance(proxy_list, list):
                            continue
                        for entry in proxy_list:
                            name = entry.get('name', '')
                            tid = name.split('-x')[0] if '-x' in name else name
                            # Main proxy wins; record extra-proxy errors too
                            if tid not in statuses or name == tid:
                                statuses[tid] = entry
                            elif entry.get('status') != 'running' and entry.get('err'):
                                statuses.setdefault(f"{tid}::extra_error", entry)
                else:
                    logging.debug(f"frpc admin API for {server_id} returned {resp.status_code}")
            except requests.RequestException:
                # Admin API not up yet (daemon still connecting)
                pass
            with self._lock:
                if server_id in self.frp_daemons:
                    self.frp_daemons[server_id]['api_up'] = api_up
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
            msg = f"Tunnel {tunnel_id} is already running."
            logging.warning(msg)
            return True, msg

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
            if not daemon or not daemon.get('api_up'):
                statuses[tid] = {'status': 'stopped', 'message': 'Starting frpc daemon...'}
                continue

            proxy = proxy_statuses.get(tid)
            if proxy is None:
                statuses[tid] = {'status': 'stopped', 'message': 'Connecting...'}
                continue

            proxy_status = (proxy.get('status') or '').lower()
            extra = proxy_statuses.get(f"{tid}::extra_error")
            if proxy_status == 'running':
                message = 'Connected'
                if extra and extra.get('err'):
                    message = f"Connected (extra port {extra.get('remote_port', '?')} failed)"
                statuses[tid] = {'status': 'running', 'message': message}
            else:
                err = proxy.get('err') or (extra or {}).get('err') or proxy.get('status', 'Unknown')
                statuses[tid] = {'status': 'error', 'message': err}

        return statuses

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
