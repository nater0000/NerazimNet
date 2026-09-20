# NerazimNet v0.2.0

## Highlights

- **macOS and Linux support.** NerazimNet now runs on all three desktop platforms — download `NerazimNet_macOS.zip` or `NerazimNet_Linux.tar.gz` from this release. Note the macOS build is unsigned: right-click → Open, or run `xattr -dr com.apple.quarantine` on the app, on first launch.
- **Tunnels now survive app exit and reboot.** frpc is owned by a headless OS-managed daemon (Scheduled Task on Windows, `systemd --user` on Linux, LaunchAgent on macOS) instead of the GUI. Closing NerazimNet no longer stops your tunnels — they restart automatically at login, and crashed frpc processes respawn on their own. The GUI is only needed to make changes.

## Improvements

- New Nerazim insignia icon (proper multi-size `.ico`/`.icns`) and a Dark-Templar teal theme sampled from the artwork.
- Dark-mode icon variants for all UI glyphs — the loader picks `dark/` versions automatically.
- README refreshed for the FRP workflow, with a screenshot audit tracking which images need recapture.

## Bug Fixes

- Tunnel status polling and test HTTP calls no longer honor system/env proxy settings for loopback requests — a configured proxy could previously make running tunnels appear dead forever.
- Fixed a macOS deadlock when spawning frpc (`preexec_fn` fork-safety issue; now uses `start_new_session`).
- Fixed transparent-button text color so sidebar, dialog, and settings labels are readable in both light and dark mode.

## Development

- pytest suite with a fake `frpc` stub (`tests/fake_frpc.py`) exercising the real TunnelManager/DaemonRunner — no VPS needed. CI now runs tests on Windows, Ubuntu, and macOS, and releases gate on them.
