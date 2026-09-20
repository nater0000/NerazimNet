"""Stages bundled binaries to a stable AppData path.

PyInstaller --onefile extracts bundled resources to a fresh %TEMP%\\_MEI*
directory on every launch. Windows Firewall rules key on the executable's
full path, so a binary run from _MEIPASS re-triggers the "Allow access"
prompt every launch. Copying to %APPDATA%\\NerazimNet\\bin gives the exe a
stable identity so the rule persists.
"""
import os
import sys
import shutil
import hashlib
import logging
import tempfile
from utils.paths import get_bin_dir


def _files_equal(path_a: str, path_b: str) -> bool:
    """Size + SHA-256 compare to detect a new bundled binary."""
    try:
        if os.path.getsize(path_a) != os.path.getsize(path_b):
            return False
        def digest(p):
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            return h.hexdigest()
        return digest(path_a) == digest(path_b)
    except OSError:
        return False


def stage_bundled_exe(bundled_path: str, exe_name: str) -> str:
    """Copies a bundled exe to %APPDATA%\\NerazimNet\\bin and returns the
    stable path. Falls back to the bundled path on any failure."""
    stable_dir = get_bin_dir()
    stable_path = os.path.join(stable_dir, exe_name)
    try:
        os.makedirs(stable_dir, exist_ok=True)
        if not os.path.exists(stable_path) or not _files_equal(bundled_path, stable_path):
            shutil.copy2(bundled_path, stable_path)
            logging.info(f"Staged {exe_name} to stable path: {stable_path}")
        return stable_path
    except Exception as e:
        logging.warning(f"Could not stage {exe_name} ({e}); falling back to bundled path.")
        return bundled_path


def reset_firewall_rules(exe_paths: list) -> tuple:
    """Removes any existing Windows Firewall rules (including accidental
    Deny rules) for the given executables and adds fresh inbound allow
    rules for TCP and UDP. Requires admin — runs a self-deleting batch
    file via a UAC elevation prompt.

    Returns (success, message)."""
    if sys.platform != 'win32':
        return False, "Firewall rules are only managed on Windows."

    exe_paths = [p for p in exe_paths if p and os.path.exists(p)]
    if not exe_paths:
        return False, "No executables found to create rules for."

    lines = ["@echo off"]
    for path in exe_paths:
        label = os.path.splitext(os.path.basename(path))[0]
        lines.append(f'netsh advfirewall firewall delete rule name=all program="{path}" >nul 2>&1')
        for proto in ("TCP", "UDP"):
            lines.append(
                f'netsh advfirewall firewall add rule name="NerazimNet {label} ({proto})" '
                f'dir=in action=allow program="{path}" enable=yes protocol={proto} profile=any'
            )
    lines.append('del "%~f0" >nul 2>&1')  # self-delete after running

    bat_path = os.path.join(tempfile.gettempdir(), "nerazimnet_firewall.bat")
    try:
        with open(bat_path, 'w') as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        return False, f"Could not write firewall script: {e}"

    import ctypes
    # "runas" triggers the UAC prompt; rc <= 32 means launch failed
    # (e.g. SE_ERR_ACCESSDENIED=5 when the user cancels UAC).
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", bat_path, None, None, 0)
    if rc <= 32:
        try: os.remove(bat_path)
        except OSError: pass
        return False, "Firewall update was not approved or failed to launch."
    return True, ("Firewall rules updated. If Windows asks again on next "
                  "launch, choose Allow — this was a one-time fix.")
