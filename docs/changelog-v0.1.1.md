# NerazimNet v0.1.1

## Fixes

- **Installer version stamping**
  - `create_installer.iss` now guards `MyAppVersion` with `#ifndef`, so the
    `/DMyAppVersion` flag passed by `create_installer.py` takes effect.
  - Release installers are now correctly named e.g. `NerazimNet_Installer_0.1.1.exe`.

## Version

- Bumped to 0.1.1.
