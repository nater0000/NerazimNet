# NerazimNet v0.2.5

A large UI overhaul plus a deep security/reliability audit. The headline: the
"laggy GUI" is fixed — the app was re-deriving PBKDF2 keys for the entire
config history on the UI thread after every save.

## UI / UX

- **Native list widgets everywhere.** All lists (tunnels, servers, devices,
  history, debug objects) are now native `ttk.Treeview` — faster, with
  draggable column widths and a collapsible group per server marked by
  `▾`/`▸` text arrows instead of the theme's box indicator.
- **Custom `nerazim` / `nerazim-light` ttk themes** matched to the app's
  dark/light surfaces — no more mismatched checkbox, radio, or combobox
  background boxes.
- **UI Scale setting** (Settings > Appearance, 100–200%, persisted) that
  actually scales everything: tree text and rows, column headers, buttons,
  comboboxes, checkboxes, dialogs, and tooltips — via named fonts that
  re-realize on scale change. Bigger base sizes at 100% too.
- **Resizable split panes** in Debug and History views (`ttk.PanedWindow`
  sashes); dashboard/server action buttons right-aligned under Start/Stop All.
- **Cleaner route column**: extras show as a compact `(+N extra)` indicator
  with a themed hover tooltip listing each extra port (16pt font).
- **Modal polish**: form labels/entries/comboboxes at a consistent 14pt,
  Escape cancels every dialog, dialog surfaces match the theme, and the
  sidebar's misleading "focus" highlight is gone.

## Features

- **Accept Invite** (Settings > Devices): the invite-acceptance path existed
  in the controller but had no UI — you can now paste a `device_id|folder`
  string on the second device instead of only generating invites.
- **Session lock**: auto-lock after 5/15/30/60 min of inactivity plus Lock
  Now (Settings > Password and the tray menu); locking hides the UI while
  tunnels and sync keep running.
- **Encrypted backup export** (Settings > Password) and a **Check for
  Updates** button that also runs quietly in the background after unlock.
- **Password change + recovery flows** now actually wired, with a modal
  spinner while re-encryption runs.
- **Tray menu**: Show, Start/Stop All Tunnels, Lock, Quit.
- Stronger validation: tunnel ports 1–65535, hostname charset, extra-port
  spec grammar, required admin user, certbot email format; remote port
  suggestions are scoped per-server.

## Performance

- **The big one**: every save used to replay the *entire* patch history —
  decrypting every patch of every object (~100ms PBKDF2 each) on the UI
  thread, seconds of freeze that grew with use. Commits now update memory
  incrementally (verified identical to replay), and remote edits merge via
  a background poll that only pays for replay when SyncData changed.
- Every other PBKDF2-heavy path moved off the UI thread: History version
  viewer (async with a "Decrypting…" placeholder), history pruning/squash,
  and password re-encryption (behind a modal spinner).
- Syncthing invite-accept and device removal no longer freeze the UI; the
  window hides instantly on quit instead of staying visible during backend
  shutdown; a redundant `update()` event-pump call removed.

## Security

- **SSH host keys are now verified.** Provisioning silently accepted *any*
  host key on every connection (Fabric's AutoAddPolicy never loaded or saved
  known_hosts). Now trust-on-first-use via `~/.ssh/known_hosts` — a changed
  key aborts with BadHostKeyException.
- **No more plaintext config in logs**: the whole decrypted state was dumped
  at DEBUG into nerazimnet.log on every save.
- **frpc admin API auth**: the loopback status API now requires a random
  per-server basic-auth password.
- **Secret file permissions**: frpc.toml (FRP token + admin password),
  the SSH private key, and ssh_keys/ are `0600`/`0700` instead of default
  umask.
- **Shell-injection hardening**: every config value reaching a remote sudo
  command is regex-validated, including stale Nginx manifest entries;
  username and public-key lines were already guarded.
- **Atomic synced writes**: all SyncData files go through temp+rename, so a
  crash can't propagate a torn file to other devices.
- **Sync divergence detection**: forked patch histories truncate at the
  divergence point deterministically and warn once, instead of compounding
  corruption.
- **SSH private key encrypted at rest** (passphrase-protected PEM), with
  password recovery/change actually functional.

## Reliability / Internals

- Async-work races closed: commit order is patch-then-manifest so reloads
  never see a half-committed object, and concurrent pruning no longer
  produces false "Sync Conflict" warnings.
- Auto-lock activity listeners no longer stack across lock/unlock cycles;
  deleted tunnels drop all per-tunnel bookkeeping.
- Subprocess timeouts added to taskkill/pkill fallbacks; unclosed file
  handles fixed; dead code (unregister_daemon, get_devices, get_debug_info)
  and unused imports removed.
- Windows-specific fixes: schtasks-denied daemon fallback, ctypes pointer
  truncation in the OS-shutdown hook, Ctrl+C no longer orphans Syncthing.

## Tests

58 tests, all passing — and the suite runs ~2x faster now that saves don't
replay encrypted history.
