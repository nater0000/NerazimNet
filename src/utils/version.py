"""Runtime version helpers for NerazimNet."""
import logging
import os
import sys

try:
    import toml
except ImportError:
    toml = None


def _version_file_candidates():
    """Return possible paths to pyproject.toml (source and PyInstaller bundles)."""
    candidates = []
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller onefile/_MEIPASS layout
        candidates.append(os.path.join(sys._MEIPASS, 'pyproject.toml'))
        # Some PyInstaller modes place data files next to the executable
        candidates.append(os.path.join(os.path.dirname(sys.executable), 'pyproject.toml'))

    # Source layout: src/utils/version.py -> project root
    module_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(module_dir))
    candidates.append(os.path.join(project_root, 'pyproject.toml'))

    return candidates


def get_version() -> str:
    """Return the application version from pyproject.toml, or 'unknown'."""
    for path in _version_file_candidates():
        if not os.path.exists(path):
            continue
        try:
            if toml is None:
                # Minimal TOML parse fallback for [project] version
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('version') and '=' in line:
                            return line.split('=', 1)[1].strip().strip('"').strip("'")
                continue
            with open(path, 'r', encoding='utf-8') as f:
                data = toml.load(f)
            return data['project']['version']
        except Exception as e:
            logging.warning(f"Failed to read version from {path}: {e}")
            continue
    return "unknown"


def get_frp_version() -> str:
    """Return the bundled/provisioned FRP version from [tool.nerazimnet], or a default."""
    default = "v0.71.0"
    for path in _version_file_candidates():
        if not os.path.exists(path) or toml is None:
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = toml.load(f)
            value = data.get('tool', {}).get('nerazimnet', {}).get('frp_version')
            if value:
                return value
        except Exception as e:
            logging.warning(f"Failed to read frp_version from {path}: {e}")
            continue
    return default
