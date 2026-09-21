"""Cross-platform desktop notifications.

Uses the pystray balloon on Windows, osascript on macOS (no tray icon
exists there), and notify-send on Linux. All failures are non-fatal —
a notification is best-effort UX, never worth crashing a poll loop.
"""
import logging
import subprocess
import sys


def _osa_escape(text: str) -> str:
    return text.replace('\\', '\\\\').replace('"', '\\"')


def send_notification(title: str, message: str, tray_icon=None):
    """Fires an OS desktop notification. Never raises."""
    try:
        if sys.platform == 'darwin':
            subprocess.Popen([
                'osascript', '-e',
                f'display notification "{_osa_escape(message)}" '
                f'with title "{_osa_escape(title)}"'
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith('linux'):
            try:
                subprocess.Popen(['notify-send', title, message],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                if tray_icon is not None:
                    tray_icon.notify(message, title)
        elif tray_icon is not None:
            tray_icon.notify(message, title)
    except Exception as e:
        logging.debug(f"Desktop notification failed: {e}")
