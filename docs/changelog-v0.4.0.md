# NerazimNet v0.4.0

Transport resilience and per-route access controls, inspired by a design
review of the super-matrix project — plus server and daemon hardening.

## Features

- **FRP transport fallback.** The server's new **FRP Transport** setting
  defaults to **Auto**: when every proxy on a server stays unconnected for
  ~60s (the classic symptom of a UDP-blocked network), the manager advances
  frpc through **QUIC → TCP → WebSocket**, rewrites the config, and restarts
  the process — the daemon respawns it on the new protocol. A pinned
  transport can still be chosen explicitly. TCP/7000 has always been open on
  provisioned servers, so the fallback needs nothing extra.
- **Per-tunnel IP allow lists.** The new **Allowed IPs** field accepts
  comma-separated IPs/CIDRs (e.g. `203.0.113.7, 10.0.0.0/8`), enforced by
  Nginx `allow`/`deny` on the VPS edge — in both the HTTPS site blocks and
  the layer-4 stream blocks, so denied clients never reach your service.
- **Per-tunnel rate limiting.** The new **Rate Limit** field (`10r/s`,
  `100r/m`, or a bare number for req/s) creates a per-hostname
  `limit_req_zone` (`nrz_*` files in sites-enabled, riding the same include
  every route uses — no conf.d dependency, no new sudo rules) applied with a
  burst of 20; over-limit clients get 429.
- **IPv6 listeners.** Route sync probes the remote kernel and emits `[::]`
  listeners in site and stream configs when IPv6 is enabled — AAAA records
  previously hit the default server block.
- **DNS check on tunnel save.** If the hostname doesn't resolve, or resolves
  to an address that isn't the server's, a post-save warning explains the
  route won't work until DNS is fixed. Non-blocking; the tunnel still saves.

## Security & reliability

- **frps systemd hardening**: `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, restricted address families, namespaces,
  syscalls and more.
- **Daemon user unit hardening**: the systemd `--user` supervisor gets the
  same treatment at `ProtectSystem=full` (strict would break its log/pid
  writes under `$HOME`).
- **QUIC buffer tuning**: provisioning writes
  `/etc/sysctl.d/90-nerazimnet-quic.conf` raising `net.core.rmem_max`/
  `wmem_max` to 7.5MB — frp's QUIC is throughput-limited by the kernel
  defaults. Non-fatal on read-only `/proc/sys` (containers).
- Docs: a **trust-boundaries** paragraph spells out what is and isn't
  authenticated at the edge, and the revocation semantics on tunnel
  stop/delete (frpc proxy dropped client-side, Nginx drains gracefully).

## Edge cases handled

- Fallback detection ignores zero-proxy configs and admin-API downtime, and
  can't deadlock its restart marker on a second reconcile pass.
- Garbage `transport` values arriving via synced config are treated as
  `auto` (never crash reconcile); `0r/s` rate limits are rejected before
  they can break `nginx -t`; removing a tunnel/server cleans up all
  transport bookkeeping and the Nginx zone file.
