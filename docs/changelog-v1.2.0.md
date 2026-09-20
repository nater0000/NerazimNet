NerazimNet - Release Notes (v1.2.0)

This release brings a powerful new tunnel routing option plus a major Syncthing runtime refresh.

## 🚀 Features & Improvements

* **Local VPS Service Tunnel support:** Added a new tunnel route type for "Local VPS Service" so NerazimNet can configure the VPS to proxy traffic directly to `localhost:<port>` instead of creating a reverse SSH forward.
* **Tunnel UI upgrade:** The tunnel creation dialog now includes a route type selector and dynamically adjusts form fields for standard device tunnels versus local VPS service routes.
* **Dashboard clarity:** Local service tunnels now display as `hostname -> VPS Service (Port <remote_port>)`, making it easier to distinguish route types at a glance.
* **Updated tunnel manager logic:** The app now honors the new `route_type` property and builds SSH commands differently for local service routes versus traditional reverse tunnels.

## 🛡️ Stability & Runtime Fixes

* **Syncthing runtime updated:** Bundled `resources/syncthing/syncthing.exe` was refreshed to fix a version mismatch and align the packaged runtime with the shipped documentation.
* **Syncthing startup resilience:** Improved Syncthing management in `src/controllers/syncthing_manager.py` with:
  * orphaned process and database lock cleanup before launch
  * port scanning that skips reserved or already-in-use local ports
  * `--no-browser`, `--no-restart`, and `--no-upgrade` launch options to avoid unwanted background behavior
  * log capture from Syncthing when it exits immediately
  * automatic HTTPS/TLS detection for the Syncthing API
  * longer API wait time while DB migrations or defender scans complete
  * more robust termination handling on Windows and other platforms.
* **Startup exception fix:** Fixed a bug in `src/app.py` where the error dialog callback could reference the wrong exception variable.

## 🧰 Documentation & Housekeeping

* **README improvements:** Setup instructions were rewritten with explicit virtual environment creation, dependency installation, and build commands.
* **Security-focused .gitignore updates:** `.gitignore` now includes NerazimNet-specific ignore patterns for local SSH keys, Syncthing runtime data, logs, and local build artifacts.

This release improves tunnel routing flexibility and the stability of the embedded Syncthing runtime for Windows users.
