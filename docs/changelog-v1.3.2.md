# NerazimNet v1.3.2

## New Features

- **443-path proxy for extra service ports**
  - Extra service ports now also appear as `https://<hostname>/extraport-<port>/` on the existing `443` HTTPS listener.
  - This lets services like LiveKit be reached even if the hosting provider blocks the extra port (e.g. `7880`) upstream.
  - Re-provisioning regenerates the nginx config with the new `location` blocks automatically.

## Fixes

- Extra port nginx listeners are still bound to the VPS public IP to avoid conflict with the SSH reverse tunnel on `127.0.0.1`.

## Notes

- The standalone `https://<hostname>:<extra-port>/` endpoint remains available if the provider's firewall allows it.
- For a provider-blocked port, point your client at the `443` path, e.g. `wss://join.dev.btwo.me/extraport-7880/rtc`.
