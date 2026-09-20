# NerazimNet v1.4.2

## Fixes

- **Provisioner now reloads Nginx after stream include setup**
  - `_ensure_nginx_stream_support` runs `nginx -t && systemctl reload nginx` after creating the stream directories and adding the `stream` include to `nginx.conf`.
  - This ensures the stream block is active immediately after the first (or any subsequent) provision.

- **More provisioning output**
  - `_ensure_nginx_running` now says it is **restarting** Nginx, not just starting.
  - Verifies and logs whether `--with-stream` is compiled into the Nginx binary.
  - Verifies Nginx is listening on port 443.

## Observability

- **setup_tunnel.sh logs are now mirrored to stdout**
  - Every `log()` message is written to both `/tmp/setup_tunnel.log` and stdout.
  - The NerazimNet client captures stdout, so you can now see `SERVER_IP`, parsed `extra_port_list`, `nginx -V` compile flags, generated stream config, and `ss -ltn` listener checks directly in the app.

## Version

- Bumped to 1.4.2.
