import logging
import os
import re
import time
import sys
from fabric import Connection, Config
from invoke.exceptions import UnexpectedExit, CommandTimedOut
from io import BytesIO
from jinja2 import Environment, FileSystemLoader

class ServerProvisioner:
    """
    Handles one-time VPS provisioning (FRP server daemon + Nginx) and ongoing
    Nginx/Certbot route synchronization over administrative SSH.

    Administrative SSH is authenticated either with the automation SSH key
    (deployed to the admin user during provisioning) or with a one-off
    admin password supplied by the user.
    """

    FRP_BIND_PORT = 7000
    MANIFEST_PATH = "/etc/nginx/nerazimnet-managed.list"

    def __init__(self, host, admin_user, admin_password="", admin_key_path=None,
                 automation_public_key=None, frp_token=None,
                 frp_version="v0.71.0", certbot_email=""):
        """
        Initializes the provisioner.

        Args:
            host (str): The server's IP address or hostname.
            admin_user (str): The administrative (sudo-capable) user to connect as.
            admin_password (str): Optional admin password (one-off auth + sudo).
            admin_key_path (str): Optional path to the automation private key
                used for passwordless admin SSH (preferred for route syncs).
            automation_public_key (str): Public key content to install for the
                admin user during provisioning.
            frp_token (str): Shared auth token written to frps.toml.
            frp_version (str): FRP release tag to install (e.g. 'v0.71.0').
            certbot_email (str): Email address for Let's Encrypt registration.
        """
        self.host = host
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.admin_key_path = admin_key_path
        self.automation_public_key = automation_public_key
        self.frp_token = frp_token
        self.frp_version = frp_version
        self.certbot_email = certbot_email
        self.log_output = []

        # --- DETERMINE TEMPLATE DIRECTORY PATH ---
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            # Running packaged: templates are in resources/server-setup inside _MEIPASS
            base_path = sys._MEIPASS
            self.template_dir = os.path.join(base_path, 'resources', 'server-setup')
            logging.info(f"Running packaged. Template path: {self.template_dir}")
        else:
            # Running as script: determine path relative to this file
            self.script_dir = os.path.dirname(os.path.abspath(__file__)) # .../src/controllers
            project_root = os.path.dirname(os.path.dirname(self.script_dir)) # Go up two levels to project root
            self.template_dir = os.path.join(project_root, 'resources', 'server-setup')
            logging.info(f"Running script. Template path: {self.template_dir}")

        # Verify template directory exists - Raise error if not found
        if not os.path.isdir(self.template_dir):
             logging.error(f"Template directory NOT FOUND at: {self.template_dir}")
             raise FileNotFoundError(f"Template directory not found at {self.template_dir}. Ensure 'resources/server-setup' exists and is bundled.")

        try:
            # Load templates from the determined directory
            self.jinja_env = Environment(loader=FileSystemLoader(self.template_dir), autoescape=False)
            logging.debug("Jinja2 environment initialized successfully.")
        except Exception as e:
            logging.error(f"Failed to initialize Jinja2 environment: {e}", exc_info=True)
            raise RuntimeError(f"Failed to set up Jinja2 templating: {e}") from e
        # --- END PATH DETERMINATION ---


    def _log(self, message):
        """Helper to log messages."""
        logging.info(f"[Provisioner:{self.host}] {message}")
        self.log_output.append(message)

    def _connect(self) -> Connection:
        """Builds a Fabric connection using key auth (preferred) or password auth."""
        connect_kwargs = {}
        config = None
        if self.admin_key_path:
            connect_kwargs["key_filename"] = self.admin_key_path
            # Key-authed sessions rely on the NOPASSWD sudoers rules for route sync,
            # but fall back to the admin password if one was supplied this session.
            if self.admin_password:
                config = Config(overrides={'sudo': {'password': self.admin_password}})
        elif self.admin_password:
            connect_kwargs["password"] = self.admin_password
            config = Config(overrides={'sudo': {'password': self.admin_password}})
        return Connection(host=self.host, user=self.admin_user,
                          connect_kwargs=connect_kwargs, config=config)

    def _upload_config(self, c: Connection, content: str, remote_path: str, mode: str = "644") -> None:
        """Uploads file content to a root-owned path via a /tmp staging file."""
        stage_path = f"/tmp/nerazimnet-{os.path.basename(remote_path)}"
        with BytesIO(content.encode('utf-8')) as file_obj:
            c.put(file_obj, remote=stage_path)
        c.sudo(f'cp {stage_path} {remote_path}', hide=True)
        c.sudo(f'chmod {mode} {remote_path}', hide=True)
        c.run(f'rm -f {stage_path}', warn=True, hide=True)

    # =========================================================================
    # One-time VPS provisioning
    # =========================================================================

    def provision_vps(self) -> tuple[bool, list[str]]:
        """
        Main entry point to perform full VPS provisioning.
        Connects as the admin user (password auth on first run).
        """
        self._log(f"Starting full VPS provisioning for {self.host} as '{self.admin_user}'...")
        if not self.frp_token:
            self._log("❌ No FRP auth token available. Configure credentials in Settings first.")
            return False, self.log_output
        try:
            with self._connect() as c:
                self._log("Admin connection successful.")

                # Run setup steps sequentially, checking return values
                if not self._install_packages(c): return False, self.log_output
                if not self._deploy_admin_key(c): return False, self.log_output
                if not self._grant_sudo_permissions(c): return False, self.log_output
                if not self._create_webroot(c): return False, self.log_output
                if not self._install_frps(c): return False, self.log_output
                if not self._write_frps_config(c): return False, self.log_output
                if not self._create_frps_service(c): return False, self.log_output
                if not self._configure_firewall(c): return False, self.log_output
                if not self._ensure_nginx_running(c): return False, self.log_output
                if not self._ensure_nginx_stream_support(c): return False, self.log_output

                self._log("\n✅ Full VPS provisioning completed successfully!")
                return True, self.log_output
        except Exception as e:
            self._log(f"\n❌ A critical error occurred during provisioning: {e}")
            logging.error(f"Provisioning failed for {self.host}", exc_info=True)
            return False, self.log_output

    def _install_packages(self, c: Connection) -> bool:
        """Updates apt cache and installs required packages."""
        self._log("Updating package cache and installing required packages...")
        packages = [
            "nginx-extras", "certbot", "python3-certbot-nginx",
            "fail2ban", "ufw", "lsof", "curl", "tar"
        ]

        apt_command = "DEBIAN_FRONTEND=noninteractive apt-get -y "

        try:
            # Run update and install in one command
            c.sudo(f"{apt_command} update && {apt_command} install {' '.join(packages)}", hide=True)
            self._log("Packages installed successfully.")
            return True
        except UnexpectedExit as e:
            self._log(f"❌ Failed to install packages: {e}")
            return False

    def _deploy_admin_key(self, c: Connection) -> bool:
        """
        Installs the automation public key into the admin user's authorized_keys
        so route syncs can run later over passwordless admin SSH.
        """
        if not self.automation_public_key:
            self._log("⚠️ No automation public key provided; skipping admin key deployment.")
            self._log("   Route sync will require the admin password each time.")
            return True

        self._log(f"Installing automation key for admin user '{self.admin_user}'...")
        key_line = self.automation_public_key.strip()
        try:
            home_dir = c.run('echo "$HOME"', hide=True).stdout.strip()
            ssh_dir = f"{home_dir}/.ssh"
            authorized_keys = f"{ssh_dir}/authorized_keys"

            c.run(f'mkdir -p {ssh_dir} && chmod 700 {ssh_dir}', hide=True)
            # Append the key only if it is not already present
            check = c.run(f"grep -qF '{key_line}' {authorized_keys} 2>/dev/null", warn=True, hide=True)
            if check.ok:
                self._log("Automation key already present.")
            else:
                c.run(f"echo '{key_line}' >> {authorized_keys}", hide=True)
                c.run(f'chmod 600 {authorized_keys}', hide=True)
                self._log("Automation key installed.")

            self._log("✅ Admin key configuration completed.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to deploy admin key: {e}")
            logging.error("Exception during admin key deployment", exc_info=True)
            return False

    def _grant_sudo_permissions(self, c: Connection) -> bool:
        """
        Grants the admin user NOPASSWD sudo for the specific commands needed by
        route synchronization (Nginx config writes, Certbot, UFW, reloads).
        """
        self._log(f"Granting route-sync sudo permissions to '{self.admin_user}'...")
        sudoers_file_path = "/etc/sudoers.d/nerazimnet-routes"
        allowed_commands = [
            "/usr/sbin/nginx -t",
            "/usr/sbin/nginx -s reload",
            "/usr/bin/systemctl reload nginx",
            "/bin/systemctl reload nginx",
            "/usr/bin/certbot *",
            "/usr/sbin/ufw allow *",
            "/usr/bin/cp /tmp/nerazimnet-* /etc/nginx/sites-available/*",
            "/usr/bin/cp /tmp/nerazimnet-* /etc/nginx/streams-available/*",
            "/usr/bin/cp /tmp/nerazimnet-* /etc/nginx/*",
            "/usr/bin/ln -sfn /etc/nginx/sites-available/* /etc/nginx/sites-enabled/",
            "/usr/bin/ln -sfn /etc/nginx/streams-available/* /etc/nginx/streams-enabled/",
            "/usr/bin/rm -f /etc/nginx/sites-available/*",
            "/usr/bin/rm -f /etc/nginx/sites-enabled/*",
            "/usr/bin/rm -f /etc/nginx/streams-available/*",
            "/usr/bin/rm -f /etc/nginx/streams-enabled/*",
            "/usr/bin/chmod 644 /etc/nginx/*",
            "/usr/bin/lsof -iTCP:*", # Port conflict checks (check_port_status)
            "/usr/bin/kill -9 *",    # Port conflict remediation (kill_process_on_port)
            "/usr/bin/mkdir -p /etc/nginx/streams-available /etc/nginx/streams-enabled",
            "/bin/mkdir -p /etc/nginx/streams-available /etc/nginx/streams-enabled",
        ]
        sudo_line = f"{self.admin_user} ALL=(ALL) NOPASSWD: {', '.join(allowed_commands)}"

        try:
            check_cmd = f"test -f {sudoers_file_path} && grep -q -F '{sudo_line}' {sudoers_file_path}"
            result = c.run(check_cmd, warn=True, hide=True)

            if result.ok:
                self._log("Sudo permissions already configured correctly.")
                return True

            self._log("Configuring sudo permissions...")
            self._upload_config(c, sudo_line + "\n", sudoers_file_path, mode="440")
            self._log("Sudo permissions granted.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to grant sudo permissions: {e}")
            return False

    def _create_webroot(self, c: Connection) -> bool:
        """Creates the webroot directory for Certbot challenges."""
        self._log("Creating webroot directory for Certbot...")
        webroot_path = "/var/www/html"
        try:
            c.sudo(f'mkdir -p {webroot_path}', hide=True)
            c.sudo(f'chmod 755 {webroot_path}', hide=True)
            self._log("Webroot directory created.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to create webroot directory: {e}")
            return False

    def _install_frps(self, c: Connection) -> bool:
        """Downloads the Linux frps binary and installs it to /usr/local/bin/frps."""
        self._log(f"Installing FRP server binary ({self.frp_version})...")

        # Skip if the installed version already matches
        try:
            ver_result = c.run('/usr/local/bin/frps --version', warn=True, hide=True)
            if ver_result.ok and self.frp_version.lstrip('v') in ver_result.stdout:
                self._log("frps already installed at the requested version.")
                return True
        except Exception:
            pass

        plain_version = self.frp_version.lstrip('v')
        tag = self.frp_version if self.frp_version.startswith('v') else f"v{self.frp_version}"

        # FRP publishes per-arch archives; many budget VPS tiers are ARM64
        arch_result = c.run('uname -m', hide=True)
        machine = arch_result.stdout.strip()
        frp_arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64',
                    'armv7l': 'arm', 'armv6l': 'arm', 'i386': '386', 'i686': '386'}.get(machine)
        if not frp_arch:
            self._log(f"❌ Unsupported server architecture: {machine}")
            return False
        archive_name = f"frp_{plain_version}_linux_{frp_arch}"
        url = f"https://github.com/fatedier/frp/releases/download/{tag}/{archive_name}.tar.gz"
        self._log(f"Detected {machine} -> {archive_name}")

        try:
            c.run(f'curl -fsSL -o /tmp/frp.tar.gz "{url}"', hide=True)
            c.run(f'tar -xzf /tmp/frp.tar.gz -C /tmp {archive_name}/frps', hide=True)
            c.sudo('cp /tmp/' + archive_name + '/frps /usr/local/bin/frps', hide=True)
            c.sudo('chmod 755 /usr/local/bin/frps', hide=True)
            c.run('rm -rf /tmp/frp.tar.gz /tmp/' + archive_name, warn=True, hide=True)
            self._log("frps binary installed to /usr/local/bin/frps.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to install frps: {e}")
            logging.error("frps install failed", exc_info=True)
            return False

    def _write_frps_config(self, c: Connection) -> bool:
        """Writes /etc/frp/frps.toml with QUIC enabled and the shared auth token."""
        self._log("Writing /etc/frp/frps.toml ...")
        try:
            template = self.jinja_env.get_template('frps.toml.j2')
            content = template.render(
                bind_port=self.FRP_BIND_PORT,
                quic_bind_port=self.FRP_BIND_PORT,
                frp_token=self.frp_token,
            )
            c.sudo('mkdir -p /etc/frp', hide=True)
            self._upload_config(c, content, '/etc/frp/frps.toml', mode='600')
            self._log("frps.toml written.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to write frps.toml: {e}")
            return False

    def _create_frps_service(self, c: Connection) -> bool:
        """Creates and starts the systemd unit for frps."""
        self._log("Creating frps systemd service...")
        try:
            template = self.jinja_env.get_template('frps.service.j2')
            content = template.render()
            self._upload_config(c, content, '/etc/systemd/system/frps.service')
            c.sudo('systemctl daemon-reload', hide=True)
            c.sudo('systemctl enable frps', hide=True)
            c.sudo('systemctl restart frps', hide=True)

            status = c.sudo('systemctl is-active frps', warn=True, hide=True)
            if status.ok and 'active' in status.stdout:
                self._log("frps service is active.")
                return True
            self._log("❌ frps service did not report 'active' after start.")
            c.run('journalctl -u frps -n 20 --no-pager', warn=True)
            return False
        except Exception as e:
            self._log(f"❌ Failed to create frps service: {e}")
            return False

    def _configure_firewall(self, c: Connection) -> bool:
        """Configures UFW for admin SSH, Nginx, and FRP (TCP fallback + QUIC/UDP)."""
        self._log("Configuring firewall (UFW)...")
        try:
            # Check if ufw is active first
            status_result = c.sudo('ufw status', hide=True, warn=True)
            is_active = 'Status: active' in status_result.stdout

            # Allow necessary services/ports
            c.sudo('ufw allow OpenSSH', hide=True) # Admin SSH still required
            c.sudo('ufw allow "Nginx Full"', hide=True) # Handles 80 and 443
            c.sudo(f'ufw allow {self.FRP_BIND_PORT}/tcp', hide=True) # FRP fallback transport
            c.sudo(f'ufw allow {self.FRP_BIND_PORT}/udp', hide=True) # FRP QUIC transport
            self._log("Firewall rules for SSH, Nginx, and FRP (7000 tcp/udp) added/updated.")

            if not is_active:
                self._log("Enabling firewall...")
                c.sudo('ufw --force enable', hide=True) # Use --force for non-interactive
                self._log("Firewall enabled.")
            else:
                 self._log("Firewall is already active.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to configure firewall: {e}")
            return False

    def _ensure_nginx_running(self, c: Connection) -> bool:
        """Ensures the Nginx service is started and enabled on boot."""
        self._log("Ensuring Nginx service is running and enabled...")
        try:
            # Use systemctl if available. We restart (not just start) so a freshly
            # installed nginx-extras binary with stream support is loaded.
            if c.run('command -v systemctl', hide=True, warn=True).ok:
                self._log("Restarting Nginx service with systemctl...")
                c.sudo('systemctl enable nginx', hide=True)
                c.sudo('systemctl restart nginx', hide=True)
            # Fallback for older systems (less likely but possible)
            elif c.run('command -v update-rc.d', hide=True, warn=True).ok:
                 self._log("Restarting Nginx service with service...")
                 c.sudo('update-rc.d nginx defaults', hide=True)
                 c.sudo('service nginx restart', hide=True)
            else:
                 self._log("❌ Could not determine service manager (systemctl or service). Cannot manage Nginx service.")
                 return False

            self._log("Nginx service restarted and enabled.")

            # Verify the running Nginx binary supports the stream module
            try:
                nginx_flags = c.sudo('/usr/sbin/nginx -V', hide=True, warn=True)
                if nginx_flags.ok:
                    flags = nginx_flags.stderr.strip().split()
                    with_stream = any(f.startswith('--with-stream') for f in flags)
                    self._log(f"Nginx stream module support detected: {with_stream}")
                    if not with_stream:
                        self._log("⚠️ Nginx does not appear to have --with-stream. Raw TCP/UDP forwarding will not work.")
                    else:
                        self._log(f"Stream flag(s): {[f for f in flags if f.startswith('--with-stream')]}")
                else:
                    self._log("⚠️ Could not verify Nginx compile flags.")
            except Exception as e:
                self._log(f"⚠️ Could not verify Nginx compile flags: {e}")

            return True
        except Exception as e:
            self._log(f"❌ Failed to start/enable Nginx service: {e}")
            return False

    def _ensure_nginx_stream_support(self, c: Connection) -> bool:
        """Creates stream config directories and ensures nginx.conf includes them."""
        self._log("Ensuring Nginx stream (raw TCP/UDP) support...")
        try:
            # Create stream config directories
            c.sudo('mkdir -p /etc/nginx/streams-available /etc/nginx/streams-enabled', hide=True)
            c.sudo('chown root:root /etc/nginx/streams-available /etc/nginx/streams-enabled', hide=True)
            c.sudo('chmod 755 /etc/nginx/streams-available /etc/nginx/streams-enabled', hide=True)
            self._log("Nginx stream config directories created.")

            # Ensure nginx.conf has a top-level stream include
            nginx_conf = "/etc/nginx/nginx.conf"
            stream_line = "stream { include /etc/nginx/streams-enabled/*; }"
            check_result = c.run(f'grep -qF "{stream_line}" {nginx_conf}', warn=True, hide=True)
            if not check_result.ok:
                self._log("Adding stream include to nginx.conf...")
                c.sudo(f'sh -c \'printf "\\n{stream_line}\\n" >> {nginx_conf}\'', hide=True)
                self._log("Stream include added to nginx.conf.")
            else:
                self._log("Stream include already present in nginx.conf.")

            # Reload Nginx so the stream include is actually active
            self._log("Reloading Nginx to activate stream support...")
            reload_result = c.sudo('/usr/sbin/nginx -t && systemctl reload nginx', hide=True, warn=True)
            if reload_result.ok:
                self._log("Nginx reloaded with stream support.")
            else:
                self._log("❌ Nginx config test or reload failed; stream support may not be active.")
                return False

            return True
        except Exception as e:
            self._log(f"❌ Failed to ensure Nginx stream support: {e}")
            logging.error("Nginx stream support setup failed", exc_info=True)
            return False

    # =========================================================================
    # Route synchronization (runs when tunnel configs are saved)
    # =========================================================================

    def sync_server_routes(self, tunnels: list[dict]) -> tuple[bool, list[str]]:
        """
        Generates Nginx server blocks, obtains certificates via certbot --nginx,
        and reloads Nginx for every tunnel hosted on this server.

        Called over administrative SSH when a user saves a tunnel. Uses the
        automation SSH key when available, otherwise the admin password.

        Args:
            tunnels: All tunnel config dicts whose server_id matches this server.
        """
        self._log(f"Synchronizing Nginx routes on {self.host} ({len(tunnels)} tunnel(s))...")
        try:
            with self._connect() as c:
                self._log("Admin connection established for route sync.")

                server_ip = self._resolve_server_ip(c)
                if not server_ip:
                    self._log("⚠️ Could not resolve public server IP; extra-port listeners will bind all interfaces.")

                managed_hostnames = set()
                for tunnel in tunnels:
                    if not self._sync_single_route(c, tunnel, server_ip):
                        return False, self.log_output
                    managed_hostnames.add(tunnel['hostname'])

                if not self._cleanup_stale_configs(c, managed_hostnames):
                    return False, self.log_output

                # Final validation + reload
                self._log("Testing final Nginx configuration...")
                test_result = c.sudo('/usr/sbin/nginx -t', warn=True, hide=True)
                if not test_result.ok:
                    self._log(f"❌ Nginx config test failed: {test_result.stderr.strip()}")
                    return False, self.log_output
                c.sudo('systemctl reload nginx', hide=True)
                self._log("Nginx reloaded with synchronized routes.")

                self._log("✅ Route synchronization completed successfully!")
                return True, self.log_output
        except Exception as e:
            self._log(f"\n❌ A critical error occurred during route sync: {e}")
            logging.error(f"Route sync failed for {self.host}", exc_info=True)
            return False, self.log_output

    def _resolve_server_ip(self, c: Connection) -> str:
        """Resolves the server's primary public IPv4 for binding extra-port listeners."""
        try:
            result = c.run(
                "ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \\([0-9.][0-9.]*\\).*/\\1/p' | head -1",
                warn=True, hide=True
            )
            ip = result.stdout.strip()
            if re.fullmatch(r'[0-9.]+', ip or ''):
                return ip
            # Fallback: first non-private global address
            result = c.run(
                "hostname -I 2>/dev/null | tr ' ' '\\n' | "
                "grep -vE '^(127\\.|10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)' | head -1",
                warn=True, hide=True
            )
            ip = result.stdout.strip()
            return ip if re.fullmatch(r'[0-9.]+', ip or '') else ""
        except Exception:
            return ""

    def _parse_extra_ports(self, extra_ports_str: str) -> tuple[list[int], list[dict]]:
        """
        Parses the extra_ports spec string ('[scheme:]remote:local, ...').

        Remote ports may be single ('7881') or a range ('50000-50020');
        ranges are only valid for raw/tcp/udp schemes.

        Returns (http_ports, stream_ports):
          - http_ports: remote ports fronted by an HTTPS Nginx server block
          - stream_ports: [{'port': int|str, 'udp': bool, 'range': bool}]
            fronted by Nginx stream ('port' is 'lo-hi' when 'range' is set)
        """
        http_ports, stream_ports = [], []
        for spec in (extra_ports_str or '').split(','):
            spec = spec.strip()
            if not spec:
                continue
            match = re.fullmatch(r'(?:(raw|tcp|udp|http|wss):)?(\d+(?:-\d+)?):(.+)',
                                 spec, re.IGNORECASE)
            if not match:
                self._log(f"⚠️ Skipping invalid extra port spec '{spec}' (expected [scheme:]remote:local).")
                continue
            scheme = (match.group(1) or 'http').lower()
            remote = match.group(2)
            is_range = '-' in remote
            if is_range and scheme not in ('raw', 'tcp', 'udp'):
                self._log(f"⚠️ Skipping extra port spec '{spec}': port ranges require a raw/tcp/udp scheme.")
                continue
            if scheme == 'udp':
                stream_ports.append({'port': remote if is_range else int(remote),
                                     'udp': True, 'range': is_range})
            elif scheme in ('raw', 'tcp'):
                stream_ports.append({'port': remote if is_range else int(remote),
                                     'udp': False, 'range': is_range})
            else:
                http_ports.append(int(remote))
        return http_ports, stream_ports

    def _ensure_certificate(self, c: Connection, hostname: str) -> bool:
        """Obtains a Let's Encrypt certificate via certbot --nginx if missing."""
        cert_dir = f"/etc/letsencrypt/live/{hostname}"
        check = c.sudo(f'certbot certificates 2>/dev/null | grep -q "{cert_dir}/fullchain.pem"',
                       warn=True, hide=True)
        if check.ok:
            self._log(f"Certificate for {hostname} already exists.")
            return True

        if not self.certbot_email:
            self._log(f"❌ Cannot obtain certificate for {hostname}: no certbot email configured.")
            return False

        self._log(f"No certificate for {hostname}. Bootstrapping HTTP block for validation...")
        try:
            template = self.jinja_env.get_template('nginx_bootstrap.conf.j2')
            content = template.render(hostname=hostname)
            site_path = f"/etc/nginx/sites-available/{hostname}"
            self._upload_config(c, content, site_path)
            c.sudo(f'ln -sfn {site_path} /etc/nginx/sites-enabled/', hide=True)

            test_result = c.sudo('/usr/sbin/nginx -t', warn=True, hide=True)
            if not test_result.ok:
                self._log(f"❌ Bootstrap Nginx config failed validation: {test_result.stderr.strip()}")
                return False
            c.sudo('systemctl reload nginx', hide=True)
            time.sleep(1)

            self._log(f"Running certbot --nginx for {hostname}...")
            certbot_result = c.sudo(
                f'certbot --nginx -d {hostname} --non-interactive --agree-tos '
                f'-m {self.certbot_email} --keep-until-expiring',
                warn=True, hide=True
            )
            if not certbot_result.ok:
                self._log(f"❌ Certbot failed for {hostname}: {certbot_result.stderr.strip()}")
                return False

            verify = c.sudo(f'certbot certificates 2>/dev/null | grep -q "{cert_dir}/fullchain.pem"',
                            warn=True, hide=True)
            if not verify.ok:
                self._log(f"❌ Certbot reported success but {cert_dir} was not found.")
                return False

            self._log(f"Certificate for {hostname} obtained.")
            return True
        except Exception as e:
            self._log(f"❌ Failed to obtain certificate for {hostname}: {e}")
            return False

    def _sync_single_route(self, c: Connection, tunnel: dict, server_ip: str) -> bool:
        """Writes the Nginx site + stream configs and firewall rules for one tunnel."""
        hostname = (tunnel.get('hostname') or '').strip()
        remote_port = str(tunnel.get('remote_port', '')).strip()

        if not re.fullmatch(r'[a-zA-Z0-9.-]+', hostname or ''):
            self._log(f"❌ Skipping tunnel with invalid hostname '{hostname}'.")
            return False
        if not remote_port.isdigit():
            self._log(f"❌ Skipping {hostname}: invalid remote port '{remote_port}'.")
            return False

        self._log(f"--- Syncing route for {hostname} (app port {remote_port}) ---")

        http_ports, stream_ports = self._parse_extra_ports(tunnel.get('extra_ports', ''))

        # Stage 1: ensure certificate exists before referencing it in the final config
        if not self._ensure_certificate(c, hostname):
            return False

        # Stage 2: write the final HTTPS site config
        try:
            template = self.jinja_env.get_template('nginx_site.conf.j2')
            content = template.render(
                hostname=hostname,
                remote_port=int(remote_port),
                http_extra_ports=http_ports,
                server_ip=server_ip,
            )
            site_path = f"/etc/nginx/sites-available/{hostname}"
            self._upload_config(c, content, site_path)
            c.sudo(f'ln -sfn {site_path} /etc/nginx/sites-enabled/', hide=True)
            self._log(f"Site config written for {hostname}.")
        except Exception as e:
            self._log(f"❌ Failed to write site config for {hostname}: {e}")
            return False

        # Stage 3: write raw stream (TCP/UDP) config if any extra stream ports exist
        stream_path = f"/etc/nginx/streams-available/{hostname}"
        try:
            if stream_ports:
                c.sudo('mkdir -p /etc/nginx/streams-available /etc/nginx/streams-enabled', hide=True)
                template = self.jinja_env.get_template('nginx_stream.conf.j2')
                content = template.render(stream_ports=stream_ports, server_ip=server_ip)
                self._upload_config(c, content, stream_path)
                c.sudo(f'ln -sfn {stream_path} /etc/nginx/streams-enabled/', hide=True)
                self._log(f"Stream config written for {hostname} ({len(stream_ports)} port(s)).")
            else:
                # Remove a stale stream config if the tunnel no longer defines stream ports
                c.sudo(f'rm -f /etc/nginx/streams-available/{hostname}', warn=True, hide=True)
                c.sudo(f'rm -f /etc/nginx/streams-enabled/{hostname}', warn=True, hide=True)
        except Exception as e:
            self._log(f"❌ Failed to write stream config for {hostname}: {e}")
            return False

        # Stage 4: open firewall for extra service ports
        for port in http_ports:
            c.sudo(f'ufw allow {port}/tcp', warn=True, hide=True)
        for spec in stream_ports:
            proto = 'udp' if spec['udp'] else 'tcp'
            ufw_port = str(spec['port']).replace('-', ':')  # ufw ranges use 'lo:hi'
            c.sudo(f"ufw allow {ufw_port}/{proto}", warn=True, hide=True)
        if http_ports or stream_ports:
            self._log("Firewall rules updated for extra service ports.")

        return True

    def _cleanup_stale_configs(self, c: Connection, current_hostnames: set) -> bool:
        """Removes Nginx configs for hostnames that are no longer managed."""
        try:
            result = c.run(f'cat {self.MANIFEST_PATH} 2>/dev/null || true', warn=True, hide=True)
            previous = {line.strip() for line in result.stdout.splitlines() if line.strip()}
            stale = previous - current_hostnames
            for hostname in stale:
                self._log(f"Removing stale Nginx configs for {hostname}...")
                c.sudo(f'rm -f /etc/nginx/sites-available/{hostname}', warn=True, hide=True)
                c.sudo(f'rm -f /etc/nginx/sites-enabled/{hostname}', warn=True, hide=True)
                c.sudo(f'rm -f /etc/nginx/streams-available/{hostname}', warn=True, hide=True)
                c.sudo(f'rm -f /etc/nginx/streams-enabled/{hostname}', warn=True, hide=True)

            manifest = "".join(f"{h}\n" for h in sorted(current_hostnames))
            self._upload_config(c, manifest, self.MANIFEST_PATH)
            if stale:
                self._log(f"Removed {len(stale)} stale route(s).")
            return True
        except Exception as e:
            self._log(f"❌ Failed to clean up stale configs: {e}")
            return False

    # =========================================================================
    # Administrative helpers (key- or password-authenticated)
    # =========================================================================

    def check_port_status(self, port: int) -> tuple[bool, dict | None, str]:
        """
        Checks if a given port is in use on the remote server using admin credentials.
        Returns a tuple: (success, process_info, message).
        process_info format: {'pid': '1234', 'command': 'sshd', 'user': 'tunnel'}
        """
        self._log(f"[Admin Check] Checking status of port {port} on {self.host} as '{self.admin_user}'...")
        try:
            with self._connect() as c:
                # Use lsof: -iTCP:port, -sTCP:LISTEN, -P (no port names), -n (no host names)
                # -Fpcu outputs parsable lines: p<PID>, c<COMMAND>, u<USER>
                result = c.sudo(f'lsof -iTCP:{port} -sTCP:LISTEN -P -n -Fpcu', warn=True, hide=True)

                if result.failed or not result.stdout.strip():
                    msg = f"Port {port} is free."
                    self._log(f"[Admin Check] {msg}")
                    return True, None, msg

                lines = result.stdout.strip().split('\n')
                info = {}
                current_pid = None
                # Parse the -Fpcu output (e.g., p1234, csshd, u-tunnel)
                # Assumes output per process starts with 'p'
                for line in lines:
                    if not line: continue
                    type_char = line[0]
                    value = line[1:]
                    if type_char == 'p':
                        current_pid = value
                        info = {'pid': current_pid} # Start new info dict for this PID
                    elif current_pid: # Only add if we have a current PID context
                        if type_char == 'c': info['command'] = value
                        if type_char == 'u': info['user'] = value

                # Return the info for the *last* process found (usually only one listener)
                if 'pid' in info:
                    msg = f"Port {port} is in use by PID {info['pid']} ({info.get('command', 'N/A')}, user {info.get('user', 'N/A')})."
                    self._log(f"[Admin Check] {msg}")
                    return True, info, msg
                else:
                    # Fallback if parsing fails but output was found
                    self._log(f"[Admin Check] Port {port} is in use, but PID/details could not be reliably parsed.")
                    return True, {'pid': 'Unknown'}, "Port in use, details unknown"

        except Exception as e:
            msg = f"❌ Failed to check port {port} using admin creds: {e}"
            self._log(f"[Admin Check] {msg}")
            logging.error(msg, exc_info=True)
            return False, None, msg


    def kill_process_on_port(self, port: int) -> tuple[bool, str]:
        """Finds and kills the process listening on the given port using admin credentials."""
        success, info, msg = self.check_port_status(port)
        if not success:
            return False, f"Could not check port status before killing: {msg}"
        if not info or 'pid' not in info:
            return True, f"No process found to kill on port {port}."

        pid = info['pid']
        if pid == 'Unknown':
             return False, "Cannot kill process: PID is unknown."

        self._log(f"[Admin Action] Attempting to kill process with PID {pid} on port {port}...")
        try:
            with self._connect() as c:
                c.sudo(f'kill -9 {pid}', hide=True) # Force kill
                msg = f"Successfully killed process {pid} on port {port}."
                self._log(f"[Admin Action] {msg}")
                # Verify it's gone
                time.sleep(0.5)
                success, info_after, _ = self.check_port_status(port)
                if success and not info_after:
                    self._log("[Admin Action] Verified process is no longer listening.")
                else:
                    self._log("[Admin Action] Warning: Process may not have been killed or another took its place.")
                return True, msg
        except Exception as e:
            msg = f"❌ Failed to kill process {pid} using admin creds: {e}"
            self._log(f"[Admin Action] {msg}")
            logging.error(msg, exc_info=True)
            return False, msg
