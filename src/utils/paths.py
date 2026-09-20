"""Platform-aware filesystem locations and binary naming."""
import os
import sys

APP_NAME = "NerazimNet"
EXE_EXT = ".exe" if sys.platform == "win32" else ""


def get_app_data_dir() -> str:
    """Per-user data directory: %APPDATA%\\NerazimNet on Windows,
    ~/Library/Application Support/NerazimNet on macOS,
    $XDG_CONFIG_HOME/nerazimnet (or ~/.config/nerazimnet) on Linux."""
    if sys.platform == "win32":
        return os.path.join(os.getenv("APPDATA"), APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    xdg = os.getenv("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg, "nerazimnet")


def get_bin_dir() -> str:
    """Stable location for staged executables (firewall rules key on path)."""
    return os.path.join(get_app_data_dir(), "bin")
