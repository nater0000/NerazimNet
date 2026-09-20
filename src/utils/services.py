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


def unregister_daemon() -> bool:
    """Stops and removes the OS-level daemon registration."""
    try:
        if sys.platform == 'win32':
            r = subprocess.run(['schtasks', '/End', '/TN', TASK_NAME],
                               capture_output=True, creationflags=_no_window())
            r = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'],
                               capture_output=True, creationflags=_no_window())
            return r.returncode == 0
        if sys.platform == 'darwin':
            plist = _mac_plist_path()
            subprocess.run(['launchctl', 'unload', '-w', plist], capture_output=True)
            if os.path.exists(plist):
                os.remove(plist)
            return True
        unit_dir = _linux_unit_dir()
        subprocess.run(['systemctl', '--user', 'disable', '--now', UNIT_NAME],
                       capture_output=True)
        unit = os.path.join(unit_dir, UNIT_NAME)
        if os.path.exists(unit):
            os.remove(unit)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        return True
    except Exception as e:
        logging.error(f"Failed to unregister daemon: {e}")
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


def _win_ensure_running() -> bool:
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
                return False
        finally:
            try: os.remove(xml_path)
            except OSError: pass
    run = subprocess.run(['schtasks', '/Run', '/TN', TASK_NAME],
                         capture_output=True, creationflags=_no_window())
    return run.returncode == 0


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
    if not os.path.exists(plist_path) or \
            plistlib.load(open(plist_path, 'rb')) != plist:
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
    if not os.path.exists(unit_path) or open(unit_path).read() != unit:
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



