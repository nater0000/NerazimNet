# NerazimNet v1.4.0

## New Features

- **Raw TCP extra service ports (Nginx stream/L4 proxy)**
  - Extra service ports now support a `raw:` or `tcp:` scheme prefix.
  - Use `raw:7881:localhost:7881` to expose a plain TCP service (e.g. LiveKit media over ICE-TCP) through the VPS.
  - `setup_tunnel.sh` writes an Nginx `stream` server block for raw ports, proxying the public IP to the SSH reverse-forward on `127.0.0.1`.
  - HTTP/WSS extra ports remain the default and still get an SSL `listen` server block plus the `https://<hostname>/extraport-<port>/` path on the main 443 listener.

- **Robust public IP detection for extra-port listeners**
  - `setup_tunnel.sh` now tries multiple methods to derive `SERVER_IP` and filters RFC1918 / loopback addresses.
  - Prevents extra-port Nginx listeners from binding to the wrong interface when `ip route get` or `hostname -I` returns a private IP.

## Configuration Changes

- **Extra Service Ports field syntax**
  - Default (HTTP/WSS): `7880:localhost:7880`
  - Raw TCP: `raw:7881:localhost:7881` or `tcp:7881:localhost:7881`
  - Multiple ports are still comma-separated.

## Server Provisioning

- `server_provisioner.py` now:
  - Installs `nginx-extras` to guarantee the `stream` module is available.
  - Creates `/etc/nginx/streams-available` and `/etc/nginx/streams-enabled`.
  - Adds a top-level `stream { include /etc/nginx/streams-enabled/*; }` block to `nginx.conf`.
  - Grants `tunnel` user the sudo permissions needed to write and enable stream configs.

- `setup_vps.yml` updated with the same stream directories, `nginx.conf` include, and sudoers rules for Ansible-based installs.

## UI

- `TunnelDialog` tooltip and placeholder updated to document the `raw:` / `tcp:` scheme for raw TCP ports.

## Fixes

- Extra service ports could fail to bind to the public IP if `SERVER_IP` was resolved to a loopback or RFC1918 address.
- LiveKit and other non-HTTP TCP services can now be tunneled correctly instead of being treated as HTTP/WSS.
