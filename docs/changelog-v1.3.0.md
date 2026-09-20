NerazimNet - Release Notes (v1.3.0)

This release adds automated support for extra TLS-terminated service ports, enabling secure WebSocket (WSS) endpoints such as LiveKit to be exposed through the same automated nginx workflow.

## 🚀 Features & Improvements

* **Extra Service Ports:** Tunnels can now specify additional `public:local` port pairs (e.g., `7880:localhost:7881`). NerazimNet will create an extra nginx `listen <port> ssl` block for each, proxy to the matching remote-forwarded local port, and open the port in UFW automatically.
* **LiveKit WSS ready:** The tunnel creation dialog now includes an "Extra Service Ports" field, making it easy to publish WebSocket endpoints alongside the standard HTTPS vhost without manual nginx edits.
* **Server-side automation extended:** `setup_tunnel.sh` now parses and provisions extra ports from `SSH_ORIGINAL_COMMAND`, reloads nginx with the combined config, and grants itself the required `ufw allow` permissions.
* **TunnelManager upgrade:** The tunnel manager now builds additional `-R` forwards and passes extra public ports to the server-side setup script.

## 🛡️ Stability & Security

* **Least-privilege firewall management:** The `tunnel` user is granted a targeted `sudo ufw allow [0-9][0-9][0-9][0-9]*/tcp` permission so extra service ports can be opened on demand without requiring manual root access.
* **Validation:** Both the client-side `TunnelDialog` and server-side `setup_tunnel.sh` validate extra port specs as numeric values before applying them.

## 🧰 Documentation & Housekeeping

* **Changelog:** This release note is bundled with the tag and uploaded to the GitHub release by the existing `build-and-release.yml` workflow.

This release keeps the automated provisioning flow unchanged while extending it to support WSS and other non-443 SSL services.
