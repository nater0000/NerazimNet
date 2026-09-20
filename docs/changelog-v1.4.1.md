# NerazimNet v1.4.1

## Fixes

- **Nginx now restarts (not just starts) during provisioning**
  - Ensures a freshly installed `nginx-extras` binary with the `stream` module is loaded into the running Nginx master process.
  - Resolves the issue where `nginx -s reload` could not activate raw TCP stream forwarding because the old `nginx-core` master was still running.

- **Ansible playbook uses `restarted` for Nginx service and triggers a full restart on stream include changes**
  - Aligns the Ansible path with the in-app provisioner so the stream block is loaded on first provision.

## Debugging / Observability

- **Version now visible in the UI**
  - Window title shows `NerazimNet vX.Y.Z`.
  - Debug view displays the current version at the top.
  - `pyproject.toml` is bundled into the PyInstaller build so the version can be read at runtime.

- **setup_tunnel.sh logs far more detail**
  - `SERVER_IP` after each detection method.
  - Parsed `extra_port_list` (http vs raw/tcp).
  - Nginx compile flags (`--with-stream`, etc.).
  - Generated stream config before it is written.
  - `ss -ltn` listener verification after Nginx reload.
  - Explicit reload result and listener warnings.

- **Tunnel manager logs the SSH original command**
  - The exact `hostname remote_port [extra_ports...]` string sent to the server is now logged at INFO level.

## Permissions

- Tunnel user is now granted `mkdir -p /etc/nginx/streams-available /etc/nginx/streams-enabled` so the setup script can create these directories if they are ever missing.
- Ansible sudoers line updated to match.
