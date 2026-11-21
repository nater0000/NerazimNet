# Screenshot Refresh Checklist

All images live in `docs/images/` and are referenced from `readme.md` — keep the
same filenames and the docs need zero changes. `<!-- ... -->` comments in the
readme mark each slot's status.

Legend: 🔴 changed UI (retake required) · 🟡 new branding/theme (retake) ·
🟢 still accurate (optional) · ⬜ new shot needed · ✅ refreshed

> **Note (v0.2.5):** the UI overhaul (larger named fonts, text `▾`/`▸` group
> arrows, right-aligned action buttons, new Settings sections) means most
> previously-refreshed app screenshots are stale again — marked 🔴 below.
> The `retake` comments in readme.md remain accurate.

## Install & First-Run (Section 1–2)

| File | Shows | Status |
|---|---|---|
| install-00.png | Inno Setup — additional tasks | ✅ refreshed
| install-01.png | Inno Setup — completion page | ✅ refreshed
| setup-00.png | Master password unlock | 🔴 larger label fonts |
| setup-01.png | Create master password | 🔴 larger label fonts |
| setup-02.png | Initializing services | 🔴 larger label fonts |
| setup-win-security.png | Windows Defender prompt for syncthing | 🟢 OS-drawn dialog, still accurate |
| setup-03.png | Save recovery key | 🔴 larger label fonts |

## Servers (Section 3)

| File | Shows | Status |
|---|---|---|
| start-01.png | Servers tab, empty | 🔴 bigger tree font/rows, right-aligned actions |
| add-server-01.png | Add Server dialog | 🔴 14pt inputs/labels, themed radios |
| servers-01.png | Server row in "Setup Needed" state | 🔴 bigger fonts, `▾` group arrow |
| provision-00.png | Provisioning credentials dialog (admin user/password + certbot email) | ⬜ doesn't exist — capture, then uncomment the `<img>` in readme |
| server-provision-fail-00.png | Provisioning key-error dialog | 🟡 dialog unchanged but on new theme/fonts |

## Tunnels (Section 4)

| File | Shows | Status |
|---|---|---|
| start-00.png | Tunnels tab, empty | 🔴 bigger tree font/rows, right-aligned actions |
| add-tunnel-01.png | Add Tunnel dialog — Tunnel route type | 🔴 14pt inputs/labels, no radio bg boxes |
| add-tunnel-02.png | Add Tunnel dialog — Local VPS Service type | 🔴 same as above |
| tunnels-01.png | Dashboard with running tunnel (used twice — hero + step 3) | 🔴 `▾`/`▸` arrows, `(+N extra)` route indicator, right-aligned actions, bigger fonts |
| edit-tunnel-01.png | Edit Tunnel dialog | 🔴 14pt inputs/labels |
| tunnel-log-00.png | Live log viewer | 🔴 14pt monospace |

## App Views & Settings (Section 5)

| File | Shows | Status |
|---|---|---|
| start-collapsed-00.png | Collapsed sidebar | 🟡 minor (no more nav-highlight box) |
| systray-icon-00.png | Icon in system tray | ✅ refreshed |
| systray-click-00.png | System tray menu | 🔴 menu now includes Lock |
| start-02.png | Settings → Devices | 🔴 new **Accept Invite** button |
| start-03.png | Settings → SSH Keys | 🔴 bigger fonts |
| start-04.png | Settings → Password | 🔴 Session Lock / Backup / Updates sections |
| start-05.png | Settings → Appearance | 🔴 stacked Mode + UI Scale rows |
| start-06.png | History view | 🔴 paned-window dividers, bigger fonts |
| start-07.png | Debug view | 🔴 paned-window divider, bigger fonts |
| edit-server-01.png | Edit Server dialog | 🔴 Admin User / Certbot Email fields + new fonts |

## Nice-to-haves (no slots yet)

- **Accept Invite dialog** (Settings → Devices → "Accept Invite")
- **Servers tab with a "✅ Ready" server** — pairs with servers-01.png
- **Dark + light mode side-by-side** of tunnels-01.png for the hero slot
- **Route extras hover tooltip** (`(+N extra)` on a multi-port tunnel)
- **Lock screen** after auto-lock / Lock Now
- **macOS Gatekeeper warning** — could live in the install section
