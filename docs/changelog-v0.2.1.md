# NerazimNet v0.2.1

## Highlights

- **ARM64 VPS support.** Provisioning now detects the remote CPU architecture via `uname -m` and downloads the matching frps archive, so ARM VPS tiers work out of the box — Hetzner CAX, Oracle Cloud Ampere, AWS Graviton. amd64, arm64, arm (v6/v7), and 386 are mapped; anything else fails with a clear message instead of installing a broken binary.
- **Port ranges in Extra Service Ports.** Layer-4 schemes accept ranges — `udp:50000-50020:localhost:50000-50020` — rendered as a single FRP `range:` proxy, one Nginx stream `listen` range (proxied via `$server_port`), and a `ufw lo:hi` rule. Built for LiveKit/WebRTC-style services that need contiguous UDP blocks. Ranges require a `raw:`/`tcp:`/`udp:` scheme and equal-length remote/local ranges.
- **Desktop notifications on tunnel drops.** When a running tunnel errors, you get an OS notification; another fires when it reconnects. Transitions only — no repeat spam while a tunnel stays down, and no alerts for deliberate stops. Tray balloon on Windows, Notification Center via `osascript` on macOS, `notify-send` on Linux.

## Development

- 11 new tests (36 total): arch mapping, `extra_ports` range parsing and frpc config rendering, Nginx stream template output, and notification transition behavior.
