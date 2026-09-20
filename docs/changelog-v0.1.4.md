# NerazimNet v0.1.4

## Bug Fixes

- **Fixed the repeating Windows Firewall prompt.** Bundled executables were launched from PyInstaller's temporary `_MEI*` folder, which changes every launch — so the firewall rule never matched and Windows asked again each time. `syncthing.exe` and `frpc.exe` now run from a stable `%APPDATA%\NerazimNet\bin` path. You'll see the prompt one last time, then never again.
- **Added a "Fix Firewall Access" button** (Settings → Devices) for cases where access was accidentally denied. It recreates the allow rules via a one-time administrator prompt — no more hunting through Windows Firewall settings.
