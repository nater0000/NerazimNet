"""Per-OS registration of the headless NerazimNet daemon (`--daemon` mode).

The daemon owns the frpc processes so tunnels survive app exit and reboot:
  - Windows: Scheduled Task (own-user logon trigger + RestartOnFailure,
    no admin required because the task runs as the interactive user)
  - Linux:   systemd --user unit (Restart=on-failure), with a detached
    spawn fallback on non-systemd distros
  - macOS:   LaunchAgent (RunAtLoad + KeepAlive on unsuccessful exit)
"""
import os
import sys
import logging
import plistlib
import subprocess
import tempfile
from shutil import which as shutil_which
from utils.paths import get_app_data_dir

SERVICE_LABEL = "NerazimNet Daemon"
TASK_NAME = "NerazimNet Daemon"          # Windows Scheduled Task name
UNIT_NAME = "nerazimnet-daemon.service"  # systemd --user unit
PLIST_LABEL = "com.nerazimnet.daemon"    # launchd label


def get_daemon_command() -> list:
    """Command line that runs the app in headless daemon mode."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--daemon']
    script_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(os.path.dirname(script_dir), 'main.py')
    if sys.platform == 'win32':
        # pythonw.exe avoids a console window when the task fires at logon
        pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        if os.path.exists(pythonw):
            return [pythonw, main_py, '--daemon']
    return [sys.executable, main_py, '--daemon']


def ensure_daemon_running() -> bool:
    """Registers the daemon service if needed and starts it. Idempotent."""
    try:
        if sys.platform == 'win32':
            return _win_ensure_running()
        if sys.platform == 'darwin':
            return _mac_ensure_running()
        return _linux_ensure_running()
    except Exception as e:
        logging.error(f"Could not ensure daemon service: {e}", exc_info=True)
        return False


# ------------------------------------------------------------------
# Windows — Scheduled Task
# ------------------------------------------------------------------

def _no_window():
    return subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


def _win_task_xml(command: list) -> str:
    cmd = command[0]
    args = ' '.join(f'"{a}"' for a in command[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>NerazimNet FRP tunnel daemon</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>{cmd}</Command><Arguments>{args}</Arguments></Exec></Actions>
</Task>
"""


_win_task_state = "unknown"     # "ok" | "denied" — cached per session
_win_run_key_tried = False      # attempt the HKCU fallback once, not per poll


def _win_ensure_running() -> bool:
    global _win_task_state, _win_run_key_tried
    if _win_task_state == "denied":
        # schtasks was refused this session — retrying every reconcile just
        # spams errors. Keep the Run-key + detached-daemon fallback only.
        if not _win_run_key_tried:
            _win_run_key_tried = True
            _win_install_run_key()
        return _win_spawn_fallback()
    query = subprocess.run(['schtasks', '/Query', '/TN', TASK_NAME],
                           capture_output=True, creationflags=_no_window())
    if query.returncode != 0:
        xml = _win_task_xml(get_daemon_command())
        fd, xml_path = tempfile.mkstemp(suffix='.xml', prefix='nerazimnet_task_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-16') as f:  # schtasks requires UTF-16 XML
                f.write(xml)
            r = subprocess.run(['schtasks', '/Create', '/TN', TASK_NAME,
                                '/XML', xml_path, '/F'],
                               capture_output=True, creationflags=_no_window())
            if r.returncode != 0:
                logging.error(f"schtasks create failed: {r.stderr.decode(errors='replace')}")
                _win_task_state = "denied"
                _win_run_key_tried = True
                _win_install_run_key()
                return _win_spawn_fallback()
        finally:
            try: os.remove(xml_path)
            except OSError: pass
    _win_task_state = "ok"
    _win_remove_run_key()  # the task owns logon autostart; drop stale fallback
    if _daemon_pid_alive():
        return True
    run = subprocess.run(['schtasks', '/Run', '/TN', TASK_NAME],
                         capture_output=True, creationflags=_no_window())
    if run.returncode == 0:
        return True
    # The task may not run, but a detached fallback daemon could be alive.
    return _win_spawn_fallback()


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "NerazimNetDaemon"


def _win_install_run_key() -> bool:
    """schtasks denied (policy/AV) — HKCU Run key still gives logon
    autostart without elevation. Idempotent."""
    try:
        import winreg
        cmdline = subprocess.list2cmdline(get_daemon_command())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            try:
                existing, _ = winreg.QueryValueEx(key, _RUN_VALUE)
            except FileNotFoundError:
                existing = None
            if existing != cmdline:
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, cmdline)
                logging.info("Registered HKCU Run key for daemon logon autostart.")
        return True
    except Exception as e:
        logging.debug(f"Run-key fallback failed: {e}")
        return _win_install_startup_shortcut()


def _win_install_startup_shortcut() -> bool:
    """Last resort: a .bat in the Startup folder — a plain file write,
    allowed on some machines that lock down registry/tasks. pythonw runs
    without a console so the boot-time flash is minimal."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as key:
            startup, _ = winreg.QueryValueEx(key, "Startup")
    except Exception:
        startup = os.path.join(os.getenv('APPDATA', ''),
                               r"Microsoft\Windows\Start Menu\Programs\Startup")
    bat_path = os.path.join(startup, 'nerazimnet-daemon.bat')
    cmdline = subprocess.list2cmdline(get_daemon_command())
    try:
        if os.path.exists(bat_path):
            with open(bat_path, encoding='utf-8') as f:
                if cmdline in f.read():
                    return True  # already installed
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(f'@start "" {cmdline}\r\n')
        logging.info("Registered Startup-folder launcher for daemon autostart.")
        return True
    except Exception as e:
        logging.debug(f"Startup-folder fallback failed: {e}")
        return False


def _win_remove_run_key():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_VALUE)
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.debug(f"Could not remove Run key: {e}")


def _daemon_pid_path() -> str:
    return os.path.join(get_app_data_dir(), 'daemon.pid')


def _daemon_pid_alive() -> bool:
    """True if the daemon pidfile exists and that PID is still running."""
    try:
        with open(_daemon_pid_path()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    try:
        import ctypes
        # PROCESS_QUERY_LIMITED_INFORMATION
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _win_spawn_fallback() -> bool:
    """schtasks denied/unavailable — spawn the daemon detached so tunnels
    work for this session (no boot persistence)."""
    if _daemon_pid_alive():
        logging.debug("NerazimNet daemon already running (pidfile alive).")
        return True
    logging.warning(
        "Scheduled task registration denied; spawning daemon detached — "
        "tunnels will run now but won't auto-start at logon.")
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            get_daemon_command(),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True)
        return True
    except Exception as e:
        logging.error(f"Detached daemon spawn failed: {e}")
        return False


# ------------------------------------------------------------------
# macOS — LaunchAgent
# ------------------------------------------------------------------

def _mac_plist_path() -> str:
    return os.path.join(os.path.expanduser('~'), 'Library', 'LaunchAgents',
                        f'{PLIST_LABEL}.plist')


def _mac_plist_dict() -> dict:
    return {
        'Label': PLIST_LABEL,
        'ProgramArguments': get_daemon_command(),
        'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False},  # restart on crash, not clean exit
        'ProcessType': 'Background',
    }


def _mac_ensure_running() -> bool:
    plist_path = _mac_plist_path()
    plist = _mac_plist_dict()
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    existing = None
    if os.path.exists(plist_path):
        with open(plist_path, 'rb') as f:
            existing = plistlib.load(f)
    if existing != plist:
        with open(plist_path, 'wb') as f:
            plistlib.dump(plist, f)
    # `load -w` is idempotent: loads if absent, no-op if already running
    r = subprocess.run(['launchctl', 'load', '-w', plist_path], capture_output=True)
    if r.returncode != 0:
        # Fall back to modern bootstrap syntax (macOS 10.10+)
        uid = os.getuid()
        subprocess.run(['launchctl', 'bootstrap', f'gui/{uid}', plist_path],
                       capture_output=True)
        r = subprocess.run(['launchctl', 'kickstart', f'gui/{uid}/{PLIST_LABEL}'],
                           capture_output=True)
    return r.returncode == 0


# ------------------------------------------------------------------
# Linux — systemd --user (detached-spawn fallback)
# ------------------------------------------------------------------

def _linux_unit_dir() -> str:
    xdg = os.getenv('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(xdg, 'systemd', 'user')


def _linux_unit_text() -> str:
    command = ' '.join(f'"{c}"' for c in get_daemon_command())
    return f"""[Unit]
Description=NerazimNet FRP tunnel daemon
After=network-online.target

[Service]
ExecStart={command}
Restart=on-failure
RestartSec=5

# Hardening: the daemon only spawns frpc, reads/writes its own app-data
# dir, and talks to the VPS + loopback admin API.
NoNewPrivileges=true
# 'full' not 'strict' — strict mounts $HOME read-only, which would break
# the daemon's logs and pid file under the app-data dir.
ProtectSystem=full
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native

[Install]
WantedBy=default.target
"""


def _linux_ensure_running() -> bool:
    if shutil_which('systemctl') is None:
        return _linux_spawn_fallback()
    unit_dir = _linux_unit_dir()
    os.makedirs(unit_dir, exist_ok=True)
    unit_path = os.path.join(unit_dir, UNIT_NAME)
    unit = _linux_unit_text()
    existing = None
    if os.path.exists(unit_path):
        with open(unit_path, encoding='utf-8') as f:
            existing = f.read()
    if existing != unit:
        with open(unit_path, 'w') as f:
            f.write(unit)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
    r = subprocess.run(['systemctl', '--user', 'enable', '--now', UNIT_NAME],
                       capture_output=True)
    return r.returncode == 0


def _linux_spawn_fallback() -> bool:
    """No systemd — spawn the daemon detached so it survives app exit."""
    logging.warning("systemctl unavailable; spawning daemon detached (no auto-restart).")
    try:
        subprocess.Popen(get_daemon_command(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.error(f"Detached daemon spawn failed: {e}")
        return False



