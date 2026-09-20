# NerazimNet v0.1.0

First release of NerazimNet — forked from NydusNet and rebuilt around FRP.

## Highlights

- **FRP/QUIC tunnels replace per-tunnel OpenSSH subprocesses**
  - One hidden `frpc.exe` daemon per server instead of one `ssh.exe` per tunnel.
  - QUIC transport with TCP fallback, token authentication, TLS enabled.
- **Dynamic proxy management**
  - Tunnel start/stop rewrites `frpc.toml` and hot-reloads via `frpc reload`.
  - Status and health read from the frpc admin API (`webServer` on 127.0.0.1).
- **VPS provisioning**
  - `frps` installed to `/usr/local/bin/frps` and managed by systemd.
  - UFW rules for 7000/tcp (fallback) and 7000/udp (QUIC).
  - Scoped NOPASSWD sudoers for route sync operations.
- **Route sync over admin SSH**
  - Nginx server blocks + Certbot certificates provisioned on tunnel save.
  - Raw TCP/UDP extra ports handled via Nginx stream configs.
- **Rebrand**
  - NerazimNet naming throughout; new insignia icon; slate-teal/neon-green theme.
  - Windows `frpc.exe` (v0.71.0) bundled in the installer — no OpenSSH client required.

## Version

- Bumped to 0.1.0.
