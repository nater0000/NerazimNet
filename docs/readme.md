# **NerazimNet 🛡️✨: Secure Reverse Tunnel Manager**

<p align="center">
  <a href="https://github.com/nater0000/nerazimnet/releases/latest"><img src="https://img.shields.io/github/v/release/nater0000/nerazimnet" alt="Latest Release"></a>
  &nbsp;
  <a href="https://github.com/nater0000/nerazimnet/blob/main/LICENSE"><img src="https://img.shields.io/github/license/nater0000/nerazimnet" alt="License"></a>
  &nbsp;
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python Version"></a>
  &nbsp;
  <a href="https://github.com/nater0000/nerazimnet/actions"><img src="https://img.shields.io/github/actions/workflow/status/nater0000/nerazimnet/build-and-package.yml?branch=main" alt="Build Status"></a>
</p>

NerazimNet is a robust, multi-device reverse tunnel management application for Windows, macOS, and Linux built on **Fast Reverse Proxy (FRP)**. It provides a user-friendly GUI built with **Python** and **CustomTkinter** to securely expose local services to the internet via a remote VPS, using a single QUIC-based `frpc` daemon instead of per-tunnel SSH connections.

<!-- retake tunnels-01.png: legacy NydusNet branding; also reused in Section 4 Step 3 -->
<p align="center">
  <img src="./images/tunnels-01.png" alt="NerazimNet Tunnels View Dashboard" width="600">
</p>

## **Table of Contents**
* [Core Features](#core-features-)
* [1. Installation](#1-installation)
* [2. First-Time Setup](#2-first-time-setup)
* [3. Server Provisioning](#3-server-provisioning-main-workflow)
* [4. Creating & Managing Tunnels](#4-creating--managing-tunnels)
* [5. Other Features & Settings](#5-other-features--settings)
* [6. Technical Specifications](#6-technical-specifications--architecture-)
* [7. Development Setup](#7-development-environment-setup-)

## **Core Features 🚀**

* **One-Click Server Provisioning**: Automatically configures a fresh Linux server with the **frps** daemon (QUIC), Nginx, Certbot, an admin SSH key, and a firewall.  
* **Effortless Tunnel Management**: Create, start, stop, edit, and delete tunnels with a clean, intuitive UI — all lists are native widgets with resizable columns and collapsible per-server groups.
* **Multi-Device Sync**: Uses a bundled **Syncthing** instance to automatically and securely sync your encrypted configuration across all your devices — pair them either direction (generate an invite on one, accept it on the other).  
* **Real-Time Status & Logs**: Tunnels show their live status (**Connecting**, **Connected**, **Error**) via the frpc admin API. View detailed FRP daemon logs directly within the app.  
* **Rock-Solid Security**: All configuration is encrypted at rest with a **master password** and a **recovery key** system, plus a session **auto-lock** and exponential-backoff unlock throttling.
* **Scalable, Themed UI**: Dark and light themes plus a persisted **UI Scale** setting (100–200%) that resizes text, rows, buttons, and inputs consistently.
* **Encrypted Backups & Update Checks**: Export the whole app-data directory as an encrypted zip from Settings, and check for new releases without leaving the app.
* **System Tray Integration**: Runs quietly in the background and can be managed from the system tray (Windows/Linux; on macOS the app lives in the Dock).
* **Desktop Notifications**: Get alerted when a tunnel drops unexpectedly and when it reconnects.

## **1. Installation**

Grab the package for your platform from the [latest release page](https://github.com/nater0000/nerazimnet/releases).

**Windows** — run `NerazimNet_Installer_*.exe` and follow the setup wizard:

Step 1: Select Destination Location  

<img src="./images/install-00.png" alt="NerazimNet Setup - Select Additional Tasks" width="300">

Step 2: Complete the Setup Wizard  

<img src="./images/install-01.png" alt="NerazimNet Setup - Completing the Wizard" width="300">

**macOS** — download `NerazimNet_macOS.zip`, unzip, and move `NerazimNet.app` to Applications. The app is not notarized, so on first launch macOS Gatekeeper will block it. Either:
* Right-click the app → **Open** → **Open** in the dialog, or
* Run `xattr -dr com.apple.quarantine /Applications/NerazimNet.app` in Terminal.

**Linux** — download `NerazimNet_Linux.tar.gz`, extract, and run the `NerazimNet` binary. A `nerazimnet.desktop` file and icon are included if you want a launcher entry:
```bash
tar -xzf NerazimNet_Linux.tar.gz
./NerazimNet
# Optional launcher entry:
# cp nerazimnet.desktop ~/.local/share/applications/ && cp nerazimnet.png ~/.local/share/icons/
```

## **2. First-Time Setup**

The first time you launch NerazimNet, you'll be guided through a one-time setup process.

Step 1: Welcome Screen  

<img src="./images/setup-00.png" alt="NerazimNet - Master Password Unlock" width="300">

Step 2: Create Master Password  
<!-- retake setup-01.png: same step, new theme/branding -->

<img src="./images/setup-01.png" alt="NerazimNet First-Time Setup - Create Master Password" width="300">

Step 3: Initializing Services  
The app decrypts your config store and starts the embedded Syncthing service.  

<img src="./images/setup-02.png" alt="NerazimNet First-Time Setup - Initializing Services" width="300">

Step 4: Firewall Permission (Windows only)  
On first launch, Windows Defender will ask for permission for Syncthing — Allow access for multi-device sync to work. If you accidentally deny it, use **Settings → Devices → "Fix Firewall Access"** to recreate the rules. macOS and Linux don't need this step.  
<!-- setup-win-security.png: still accurate (OS-drawn dialog, syncthing.exe path) — retake optional -->

<img src="./images/setup-win-security.png" alt="Windows Defender Firewall Alert for Syncthing" width="300">

Step 5: Save Your Recovery Key  
This is the only way to recover your data if you forget your master password. Save it somewhere safe!  

<img src="./images/setup-03.png" alt="NerazimNet First-Time Setup - Save Recovery Key" width="300">

SSH keys are **not** needed until you provision a server. When you start provisioning (Section 3), NerazimNet auto-generates a 2048-bit RSA pair if none exists — or you can create one anytime via **Settings -> SSH Keys -> "Generate New Key Pair"**. Keys live in the app data directory (`%APPDATA%\NerazimNet\ssh_keys` on Windows, `~/Library/Application Support/NerazimNet/ssh_keys` on macOS, `~/.config/nerazimnet/ssh_keys` on Linux).

## **3. Server Provisioning (Main Workflow)**

Once setup is complete, you must register and provision a server.

Step 1: Go to the Servers Tab  
Go to the Servers (🖥️ icon) tab. It will be empty.  

<img src="./images/start-01.png" alt="NerazimNet Servers Tab - Empty" width="300">

Step 2: Add New Server  
Click "Add Server". Required fields are **Server Name** and **IP Address / Host**. Two optional fields matter for route sync later:

* **Admin User:** the sudo-capable account your automation SSH key was installed for (auto-filled by provisioning).
* **Certbot Email:** the Let's Encrypt registration email used for certificates.

If the server is already fully configured outside the app, tick **"I have manually configured this server (Ready)"** to skip provisioning. Click Save.  

<img src="./images/add-server-01.png" alt="NerazimNet - Add New Server Dialog" width="300">

Step 3: Begin Provisioning  
Your server will appear in the list with the status "⚠️ Setup Needed". Click the Wrench (🔧) icon to begin provisioning.  
<!-- retake servers-01.png: same view, new theme/branding -->

<img src="./images/servers-01.png" alt="NerazimNet Servers Tab - Server Needs Setup" width="300">

A dialog will ask for the server's **administrative credentials** (e.g., root user and password) and an email for your Let's Encrypt SSL certificates. The **password is used one time only** and is **never saved** — during provisioning the app installs your automation public key for the admin user so all future server operations are passwordless.  
<!-- NEW screenshot: provision-00.png — the provisioning credentials dialog (admin user/password + certbot email). Not yet captured. -->
<!-- <img src="./images/provision-00.png" alt="NerazimNet - Server Provisioning Credentials Dialog" width="300"> -->

Provisioning installs and configures: **frps** (systemd service, QUIC + TCP on port 7000, token auth; CPU architecture auto-detected — amd64 and arm64 supported), **Nginx**, **Certbot**, and **UFW** rules. The server's **Admin User** and **Certbot Email** are stored on the server record for later route syncs, and the status changes to **"✅ Ready"** when complete.

Each server record also has an **FRP Transport** setting: **Auto** (the default) tries QUIC first and automatically falls back to TCP, then WebSocket, when proxies can't connect for ~60 seconds — e.g. on networks that block UDP. You can also pin a transport explicitly.

Note: If provisioning fails with a key error, generate or point to keys via **Settings -> SSH Keys** ("Generate New Key Pair" button).  
<!-- retake server-provision-fail-00.png: optional — error dialog is unchanged, still shows old theme -->

<img src="./images/server-provision-fail-00.png" alt="NerazimNet Server Provisioning - Key Error Example" width="300">

## **4. Creating & Managing Tunnels**

Once your server is "Ready," you can create tunnels.

Step 1: Go to the Tunnels Tab  
Go to the Tunnels (🔀 icon) tab. It will be empty.  

<img src="./images/start-00.png" alt="NerazimNet Tunnels Tab - Empty" width="300">

Step 2: Add New Tunnel  
Click "Add Tunnel". Fill in the details for your local service.

* **Route Type:** *Tunnel to Device* exposes a service on a synced device via FRP. *Local VPS Service* fronts a service already running on the VPS itself — no tunnel needed, Nginx proxies to it directly.
* **Hostname:** The public domain you want (e.g., service.mydomain.com). DNS must already point at your VPS.
* **Server:** Your newly provisioned server.
* **Remote Port:** The internal FRP proxy port on the VPS (e.g., 8080). Nginx fronts it publicly at `https://hostname` — visitors never see this port.
* **Client Device:** (Tunnel type only) Select "(This Device)".
* **Local Destination:** (Tunnel type only) Your local service (e.g., 127.0.0.1:8080).
* **Extra Service Ports:** Optional comma-separated `[scheme:]remote:local` pairs for additional ports (e.g., `7880:localhost:7880, raw:7881:localhost:7881`). `raw:`/`tcp:`/`udp:` ports are forwarded at layer 4 via Nginx stream; the rest get HTTPS server blocks. Layer-4 schemes also accept **port ranges** for services like LiveKit/WebRTC (e.g., `udp:50000-50020:localhost:50000-50020`) — remote and local ranges must be equal length.
* **Allowed IPs:** Optional comma-separated client IP/CIDR allow list (e.g., `203.0.113.7, 10.0.0.0/8`). Enforced by Nginx on the VPS — denied clients never reach your service. Applies to the hostname's HTTPS routes *and* its layer-4 stream ports. Empty = allow everyone.
* **Rate Limit:** Optional per-client request cap, e.g. `10r/s` or `100r/m` (a bare number means requests/second). Enforced by Nginx with a burst allowance of 20; over-limit clients get HTTP 429. Empty = unlimited.
* **Auto-start on this device:** Starts the tunnel automatically when the app launches.

**On Save**, NerazimNet synchronizes the server's Nginx config and requests the SSL certificate over admin SSH (using your automation key — no password needed once provisioned). It also checks that the hostname resolves to the server and warns if DNS doesn't point there yet — the tunnel is still saved so you can fix DNS afterward.


<img src="./images/add-tunnel-01.png" alt="NerazimNet - Add New Tunnel Dialog (Tunnel to Device)" width="300">  
<img src="./images/add-tunnel-02.png" alt="NerazimNet - Add New Tunnel Dialog (Local VPS Service)" width="300">

Step 3: Start Your Tunnel  
The new tunnel will appear in your dashboard in the "Stopped" state. Click the Start (▶️) button — the shared `frpc` daemon picks it up via hot reload and the status moves through **Connecting** → **Connected**. Once running, the tunnel is owned by the OS-managed background daemon: **closing the app does not stop it**, and it comes back automatically after reboot/login.

<img src="./images/tunnels-01.png" alt="NerazimNet Tunnels View Dashboard" width="300">

Step 4: Manage Your Tunnel  
You can manage the running tunnel using the action buttons:

* **Edit (✏️):** Opens the Edit dialog.  
* **Logs (📄):** Opens the live log viewer for that tunnel.  
* **Delete (🗑️):** Deletes the tunnel.

<!-- retake edit-tunnel-01.png + tunnel-log-00.png: same dialogs, new theme -->

<img src="./images/edit-tunnel-01.png" alt="NerazimNet - Edit Tunnel Dialog" width="300">  
<img src="./images/tunnel-log-00.png" alt="NerazimNet - Live Tunnel Log Viewer" width="300">

## **5. Other Features & Settings**

NerazimNet includes several other views for managing your application.

### **App Management**

Collapsible Sidebar  
Click "Collapse" to get more space.  

<img src="./images/start-collapsed-00.png" alt="NerazimNet - Collapsed Sidebar View" width="300">

System Tray  
The app runs in the system tray (Windows/Linux) with a menu to Show, Start/Stop All Tunnels, **Lock** the session, or Quit. Note that quitting the app leaves your tunnels running — see Section 6.2. Desktop notifications alert you when a running tunnel drops unexpectedly and when it reconnects (tray balloon on Windows, Notification Center on macOS, `notify-send` on Linux).  

<img src="./images/systray-icon-00.png" alt="NerazimNet - Icon in System Tray" width="300">  
<img src="./images/systray-click-00.png" alt="NerazimNet - System Tray Menu" width="300">

### **Settings Tabs**

Settings -> Devices  
Pair other devices to sync your config — **"Invite Another Device"** generates an invite string to copy over, and **"Accept Invite"** lets this device join a share generated elsewhere (the string is `device_id|folder_label`). The list shows every paired device; remove one with the button below it. On Windows this tab also has a **Network** section with **"Fix Firewall Access"** — an elevated repair that recreates the Windows Firewall rules for the bundled Syncthing/FRP binaries if a prompt was denied.  

<img src="./images/start-02.png" alt="NerazimNet Settings - Devices Tab" width="300">

Settings -> SSH Keys  
These keys back **administrative SSH** for server provisioning and route sync — tunnels themselves run over FRP/QUIC, not SSH. Use **"Generate New Key Pair"** to create a pair, or browse to existing keys.  

<img src="./images/start-03.png" alt="NerazimNet Settings - SSH Keys Tab" width="300">

Settings -> Password  
Manage your master password, view your recovery key, and configure the **Session Lock**: auto-lock after 5/15/30/60 minutes of inactivity (or lock instantly with **Lock Now** — also available from the tray menu). Locking hides the UI behind the password prompt while tunnels and sync keep running. This tab also has **Export Encrypted Backup** (a zip of the whole app-data folder — contents stay encrypted) and a **Check for Updates** button.  

<img src="./images/start-04.png" alt="NerazimNet Settings - Password Tab" width="300">

Settings -> Appearance  
Switch between the dark **nerazim** and **nerazim-light** themes, and set **UI Scale** (100%–200%) — the choice is persisted and resizes list text, column headers, buttons, inputs, and dialogs consistently.  

<img src="./images/start-05.png" alt="NerazimNet Settings - Appearance Tab" width="300">

### **History & Debugging**

History View  
Audit all configuration changes over time.  

<img src="./images/start-06.png" alt="NerazimNet History View" width="300">

Debug View  
View the raw, in-memory config objects.  

<img src="./images/start-07.png" alt="NerazimNet Debug View" width="300">

(Note: The Edit Server dialog is also available from the Servers tab.)  
<!-- retake edit-server-01.png: dialog has new Admin User / Certbot Email fields -->

<img src="./images/edit-server-01.png" alt="NerazimNet - Edit Server Dialog" width="300">

## **6. Technical Specifications & Architecture ⚙️**

NerazimNet is architected around a decentralized, encrypted, and auditable configuration system, managed by a set of specialized controllers.

### **6.1 Security and Cryptography (CryptoManager)**

All configuration data is encrypted at rest to maintain confidentiality, integrity, and non-repudiation.

| Component | Specification | Description |
| :---- | :---- | :---- |
| **Data Encryption** | **AES-128-CBC + HMAC-SHA256** (via Fernet) | All configuration objects are individually encrypted and authenticated before being written to disk. |
| **Key Derivation** | **PBKDF2-HMAC-SHA256** | The encryption key is derived from the Master Password using **480,000 iterations** to resist brute-force attacks. |
| **SSH Keys** | **RSA 2048-bit Key Pair** | Automatically generated with public_exponent=65537. Keys are stored in the `ssh_keys` folder of the platform app-data directory (see Section 2) with `0700`/`0600` permissions. |
| **SSH Host Keys** | **Trust-on-first-use** | Provisioning connections verify the server against `~/.ssh/known_hosts`, persisting first-seen keys — a changed key aborts the connection instead of silently succeeding. |
| **FRP Token** | **32-char random secret** | Generated once via `secrets.token_urlsafe` and stored encrypted in `credentials.json`; authenticates frpc→frps. |
| **frpc Admin API** | **Per-server random basic auth** | The loopback status API (`127.0.0.1`) requires a random per-server password stored only in the `0600` frpc config — other local processes can't read or mutate tunnel state. |
| **Unlock Throttling** | **Exponential backoff** | Failed unlock attempts enforce a doubling delay (up to 60s) on top of the deliberately-expensive key derivation. |
| **Atomic Writes** | **tempfile + rename** | Every synced file (index, credentials, patches, manifests) is written to a temp file and atomically renamed, so a crash can never leave a torn file that Syncthing would propagate. |
| **Per-Route Access Control** | **Nginx allow/deny + limit_req** | Each tunnel can restrict client IPs/CIDRs and cap per-client request rate at the VPS edge — denied or throttled traffic never reaches the service. |
| **frps Service** | **systemd hardening** | The frps unit runs with `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, and restricted address families/capabilities; kernel UDP buffers are raised via sysctl for QUIC throughput. |
| **Recovery System** | **Encrypted Recovery Key** | Provides a unique, high-entropy key as the sole mechanism for data recovery if the Master Password is lost. |

**Trust boundaries worth knowing:** HTTPS routes are terminated by Nginx on your VPS, so the provider (and anyone who can observe port 443) sees hostnames and traffic volumes, not payloads — the TLS session ends at your server, not ours (there is no "ours"). Authentication in front of a route is your service's job unless you set an **Allowed IPs** list: raw TCP/UDP stream ports especially carry no sign-in — whatever you expose there must authenticate itself. The FRP token authenticates devices to *your* frps only; it never leaves your config store.

### **6.2 Tunnel Execution and Control (TunnelManager + DaemonRunner)**

Tunnels are **persistent** — they keep running when the app is closed and start automatically on login, without the GUI ever opening.

* **Background daemon**: frpc processes are owned by a headless supervisor (`NerazimNet --daemon`), registered with the OS the first time a tunnel starts — a **Scheduled Task** on Windows (own-user logon trigger + restart-on-failure, no admin needed), a **systemd --user** unit on Linux, or a **LaunchAgent** on macOS. Closing the app leaves tunnels up; use the app's Stop buttons to actually tear them down.
* **FRP Client**: The bundled **frpc** binary is configured via a dynamically generated `frpc_<server>.toml` per server in `frp/` under the platform app-data directory. The GUI writes/deletes these configs; the daemon spawns, supervises, and stops frpc to match.
* **QUIC Transport**: Tunnels multiplex over a single QUIC (UDP/7000) connection with TLS, eliminating per-tunnel port collisions and reducing latency. In **Auto** mode, if no proxy on a server connects for ~60s the manager restarts frpc on the next transport in the chain (**QUIC → TCP → WebSocket**) — TCP/7000 is always open on provisioned servers, so tunnels survive UDP-blocked networks.
* **Hot Reload**: Starting or stopping a tunnel rewrites `frpc.toml` and issues `frpc reload`, adopting new routes without dropping other proxies.
* **Status via Admin API**: Tunnel health is read from each daemon's frpc admin API (`/api/status` on 127.0.0.1, ports allocated from 7400 upward per server, recovered from on-disk configs across restarts). The API requires per-server random basic-auth credentials — loopback binding alone still exposes it to other local processes.
* **Logging**: Each frpc daemon logs to `logs/frpc_<server>.log` in the app data directory; the tunnel log viewer tails it.
* **Graceful Termination**: The daemon signals the frpc process group on shutdown — `taskkill` on Windows, `SIGTERM` on macOS/Linux.

### **6.3 Data Persistence & Synchronization (ConfigManager & SyncthingManager)**

Configuration state is decentralized, version-controlled, and synchronized across devices.

* **Storage Location**: All encrypted data lives in `SyncData` under the platform app-data directory — `%APPDATA%\NerazimNet` on Windows, `~/Library/Application Support/NerazimNet` on macOS, `~/.config/nerazimnet` on Linux.  
* **File Indexing**: The ConfigManager maintains a central **_index.json** file for efficient lookup of all configuration objects (tunnels, servers, devices).  
* **Version History**: Changes are recorded as lightweight **patch files** (e.g., YYYYMMDDTHHMMSS_file_id.patch) generated using the **diff-match-patch** library. Per-file history is capped (squashed into a snapshot patch) so `SyncData` can't grow without bound, and manifests are kept longer to drive the History view timeline.
* **Write Path**: A save updates in-memory state directly and appends one encrypted patch — replaying (and re-deriving keys for) the full history on every write would block the UI for seconds. Edits synced in from other devices are merged by a background poll that replays history only when the SyncData directory actually changed.
* **Conflict Handling**: If two devices edit the same object offline, the divergent patch chain is truncated at the fork deterministically (every replica computes the same point) and the user is warned once per file — state stays consistent rather than silently corrupting.
* **Synchronization**: The application manages an embedded **Syncthing** binary, controlling the P2P sync process via its **REST API**.

### **6.4 Automated Server Provisioning (ServerProvisioner)**

The ServerProvisioner uses **Fabric** to execute secure, idempotent setup on a remote Linux VPS.

* **Services Provisioned**: Installs and configures **frps** (as a hardened systemd service — `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, syscall/namespace restrictions), **Nginx**, **Certbot**, and **UFW**.  
* **QUIC Tuning**: `net.core.rmem_max`/`wmem_max` are raised to 7.5 MB via `/etc/sysctl.d/` — frp's QUIC transport is throughput-limited by the kernel's small default UDP buffers.  
* **Firewall Policy**: UFW is configured to allow inbound traffic on **TCP/22** (admin SSH), **TCP/80**, **TCP/443**, and **7000 TCP+UDP** (FRP control channel + QUIC).  
* **Route Sync**: Nginx server blocks and Let's Encrypt certificates are provisioned up-front over admin SSH whenever a tunnel is saved, instead of lazily on connect. Per-tunnel **IP allow lists** and **rate-limit zones** (`conf.d/nrz_*.conf`) are written in the same pass.  
* **Key-Based Admin Access**: The automation public key is deployed to the admin user during provisioning, with scoped NOPASSWD sudo rules for route sync.  
* **Configuration Templating**: **Jinja2** is used to dynamically generate and deploy `frps.toml` and Nginx configuration files on the remote server.

## **7. Development Environment Setup 🧑‍💻**

### **Prerequisites**

* **Python 3.10+**
* **Git**

### **Project Setup**

1. **Clone the Repository**:
```bash
   git clone https://github.com/nater0000/nerazimnet.git  
   cd nerazimnet
```

2. **Initialize Virtual Environment**:
   It is strongly recommended to use a virtual environment to manage dependencies.

   * **Windows:**
     ```powershell
     python -m venv venv
     .\venv\Scripts\activate
     ```
   * **macOS / Linux:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install Dependencies**:
   The project uses `pyproject.toml` for dependency management. Install in editable mode:
```bash
   pip install -e .
```

4. **Run the Application**:
```bash
   python src/main.py
```

5. **Building the Executable**:
   The `build.py` script uses **PyInstaller** to package the application, explicitly including resources (`resources/syncthing`, `resources/server-setup`, etc.) via the `--add-data` flag. It downloads the correct Syncthing and FRP binaries for the platform you're building on — run it on the target OS (PyInstaller does not cross-compile):
```bash
   python scripts/build.py
```
   * **Windows** → `dist/NerazimNet.exe` (then `python scripts/create_installer.py` for the Inno Setup installer)
   * **macOS** → `dist/NerazimNet.app`
   * **Linux** → `dist/NerazimNet`

6. **Running Tests**:
   The suite uses pytest plus a stub `frpc` (`tests/fake_frpc.py`) that mimics the real admin API — no VPS or real binaries needed:
```bash
   pip install '.[dev]'
   python -m pytest tests/ -v
```
