"""Stages bundled binaries to a stable AppData path.

PyInstaller --onefile extracts bundled resources to a fresh %TEMP%\\_MEI*
directory on every launch. Windows Firewall rules key on the executable's
full path, so a binary run from _MEIPASS re-triggers the "Allow access"
prompt every launch. Copying to %APPDATA%\\NerazimNet\\bin gives the exe a
stable identity so the rule persists.
"""
import os
import shutil
import hashlib
import logging


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
    stable_dir = os.path.join(os.getenv('APPDATA'), 'NerazimNet', 'bin')
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
