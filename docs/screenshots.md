# Screenshot Refresh Checklist

All images live in `docs/images/` and are referenced from `readme.md` — keep the
same filenames and the docs need zero changes. `<!-- ... -->` comments in the
readme mark each slot's status.

Legend: 🔴 changed UI (retake required) · 🟡 new branding/theme (retake) ·
🟢 still accurate (optional) · ⬜ new shot needed

## Install & First-Run (Section 1–2)

| File | Shows | Status |
|---|---|---|
| install-00.png | Inno Setup — destination select | 🟡 says "NydusNet Setup" |
| install-01.png | Inno Setup — completion page | 🟡 same |
| setup-00.png | First-run welcome | 🟡 |
| setup-01.png | Create master password | 🟡 |
| setup-02.png | Initializing services | 🟡 |
| setup-win-security.png | Windows Defender prompt for syncthing | 🟢 OS-drawn dialog, still accurate |
| setup-03.png | Save recovery key | 🟡 |

## Servers (Section 3)

| File | Shows | Status |
|---|---|---|
| start-01.png | Servers tab, empty | 🟡 |
| add-server-01.png | Add Server dialog | 🔴 new Admin User / Certbot Email fields + Ready checkbox |
| servers-01.png | Server row in "Setup Needed" state | 🟡 |
| provision-00.png | Provisioning credentials dialog (admin user/password + certbot email) | ⬜ doesn't exist — capture, then uncomment the `<img>` in readme |
| server-provision-fail-00.png | Provisioning key-error dialog | 🟢 dialog unchanged (old theme only) |

## Tunnels (Section 4)

| File | Shows | Status |
|---|---|---|
| start-00.png | Tunnels tab, empty | 🟡 |
| add-tunnel-01.png | Add Tunnel dialog — Tunnel route type | 🔴 Route Type segmented button + Extra Service Ports field |
| add-tunnel-02.png | Add Tunnel dialog — advanced/filled out | 🔴 same |
| tunnels-01.png | Dashboard with running tunnel (used twice — hero + step 3) | 🟡 |
| edit-tunnel-01.png | Edit Tunnel dialog | 🟡 |
| tunnel-log-00.png | Live log viewer | 🟡 |

## App Views & Settings (Section 5)

| File | Shows | Status |
|---|---|---|
| start-collapsed-00.png | Collapsed sidebar | 🟡 |
| systray-click-00.png | System tray menu | 🟡 Windows/Linux only — no tray on macOS |
| start-02.png | Settings → Devices | 🔴 new Network section w/ "Fix Firewall Access" (Windows) |
| start-03.png | Settings → SSH Keys | 🟡 |
| start-04.png | Settings → Password | 🟡 |
| start-05.png | Settings → Appearance | 🟡 |
| start-06.png | History view | 🟡 |
| start-07.png | Debug view | 🟡 |
| edit-server-01.png | Edit Server dialog | 🔴 new Admin User / Certbot Email fields |

## Nice-to-haves (no slots yet)

- **Servers tab with a "✅ Ready" server** — pairs with servers-01.png
- **Dark + light mode side-by-side** of tunnels-01.png for the hero slot
- **macOS Gatekeeper warning** — could live in the install section
