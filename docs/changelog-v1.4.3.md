# NerazimNet v1.4.3

## Fixes

- **Corrected stream module detection in provisioner**
  - `server_provisioner.py` now recognizes `--with-stream=dynamic` as stream support, not only `--with-stream`.
  - The log now reports the actual matching flags instead of a false negative.

## Version

- Bumped to 1.4.3.
