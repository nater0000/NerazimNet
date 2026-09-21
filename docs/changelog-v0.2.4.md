# NerazimNet v0.2.4

## Improvements

- **Remote Port auto-fills in Add Tunnel.** Prefills with the next free port — `max(used)+1` across every tunnel's `remote_port` and `extra_ports` remote sides (range upper bounds included), or `10000` for your first tunnel. Always editable; Local VPS Service routes are skipped since that field is the app's own port.
- **Documentation screenshots refreshed.** 19 images recaptured on the new theme — installer wizard, first-run dialogs, empty views, all Settings tabs, collapsed sidebar, tray icon + menu, and the Add Server/Tunnel dialogs. A few slots (populated dashboard, edit dialogs, provisioning dialog) still use older captures.

## Bug Fixes

- **Ctrl+C no longer orphans Syncthing.** Interrupting the app in a console now runs the normal shutdown path — previously `syncthing.exe` kept running with its executable and ports locked.
