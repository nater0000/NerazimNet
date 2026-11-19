import customtkinter as ctk
import logging
import os
import sys
from tkinter import ttk
from PIL import ImageTk
from views.tree_style import apply_tree_style, palette, tk_color

class SettingsView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent") # Blend with content_frame
        self.controller = controller
        self.images = controller.images # Get images from controller
        self.btn_images = getattr(controller, 'btn_images', self.images)
        self._tk_images = {}  # keep PhotoImage refs alive for ttk widgets
        apply_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1) # Main content now on row 0

        # --- Tabbed Interface (native ttk.Notebook) ---
        self.tab_view = ttk.Notebook(self)
        self.tab_view.grid(row=0, column=0, padx=10, pady=10, sticky="nsew") # Grid row=0
        self._tabs = {}
        for name in ("Devices", "SSH Keys", "Password", "Appearance"):
            tab = ttk.Frame(self.tab_view)
            self.tab_view.add(tab, text=name)
            self._tabs[name] = tab

        # Create the static content (widgets) for each tab
        self._create_devices_tab()
        self._create_ssh_keys_tab()
        self._create_password_tab()
        self._create_appearance_tab()

    def _tk_image(self, name):
        """CTkImage -> tk PhotoImage, cached (ttk widgets can't take CTkImage)."""
        if name in self._tk_images:
            return self._tk_images[name]
        cimg = self.btn_images.get(name)
        if not cimg:
            return None
        try:
            tk_img = ImageTk.PhotoImage(cimg._light_image)
        except Exception:
            return None
        self._tk_images[name] = tk_img
        return tk_img

    def on_theme_changed(self):
        self.devices_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])

    def on_enter(self):
        """Called every time the view is shown to refresh all dynamic data."""
        logging.debug("Entering SettingsView, reloading data.")
        # Load data for the visible tabs
        self._load_devices_data()
        self._load_ssh_keys_data()
        self._load_security_data()
        self._load_appearance_data() # Update appearance menu setting

    # --- REMOVED Server Tab Methods ---

    # --- Devices Tab ---
    def _create_devices_tab(self):
        tab = self._tabs["Devices"]
        tab.grid_columnconfigure(0, weight=1); tab.grid_rowconfigure(2, weight=1) # Row 2 for scroll frame

        # Frame for displaying this device's info
        this_device_frame = ctk.CTkFrame(tab)
        this_device_frame.pack(fill="x", padx=10, pady=(10, 5)) # Reduced bottom padding
        self.this_device_name_label = ctk.CTkLabel(this_device_frame, text="This Device: ...", font=ctk.CTkFont(weight="bold"))
        self.this_device_name_label.pack(anchor="w", padx=10, pady=(10,0))
        self.this_device_id_label = ctk.CTkLabel(this_device_frame, text="ID: ...", wraplength=500, justify="left")
        self.this_device_id_label.pack(anchor="w", padx=10, pady=(0,10))

        # Invite button below the device info
        ttk.Button(tab, text="Invite Another Device",
                   image=self._tk_image("add"), compound="left",
                   style="primary.TButton",
                   command=self.controller.add_new_device).pack(pady=5)

        # Native Treeview for other devices
        apply_tree_style()
        ctk.CTkLabel(tab, text="Other Synced Devices",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        devices_frame = ctk.CTkFrame(tab, fg_color=tk_color("bg"))
        devices_frame.pack(fill="both", expand=True, padx=10, pady=(2, 5))
        devices_frame.grid_rowconfigure(0, weight=1)
        devices_frame.grid_columnconfigure(0, weight=1)

        self.devices_tree = ttk.Treeview(
            devices_frame, columns=("devid",), show="tree headings",
            selectmode="browse")
        self.devices_tree.heading("#0", text="Device Name", anchor="w")
        self.devices_tree.heading("devid", text="Syncthing ID", anchor="w")
        self.devices_tree.column("#0", width=200, minwidth=120, stretch=True)
        self.devices_tree.column("devid", width=380, minwidth=200, stretch=True)
        self.devices_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])
        dev_vsb = ttk.Scrollbar(devices_frame, orient="vertical",
                                command=self.devices_tree.yview)
        self.devices_tree.configure(yscrollcommand=dev_vsb.set)
        self.devices_tree.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        dev_vsb.grid(row=0, column=1, sticky="ns")

        remove_row = ctk.CTkFrame(tab, fg_color="transparent")
        remove_row.pack(fill="x", padx=10, pady=(0, 5))
        ttk.Button(remove_row, text="Remove Selected Device", style="danger.TButton",
                   command=self._remove_selected_device).pack(side="right", padx=5, pady=5)

        # Firewall repair — Windows-only (interactive firewall prompts don't
        # exist on macOS/Linux the same way)
        if sys.platform == 'win32':
            net_frame = ctk.CTkFrame(tab)
            net_frame.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkLabel(net_frame,
                         text="Devices not syncing? Windows may have blocked network access.",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=10, pady=8)
            ttk.Button(net_frame, text="Fix Firewall Access",
                       style="secondary.TButton",
                       command=self.controller.repair_firewall_access).pack(side="right", padx=10, pady=8)

    def _load_devices_data(self):
        # Update this device's info
        self.this_device_name_label.configure(text=f"This Device: {self.controller.get_my_device_name()}")
        my_id = self.controller.get_my_device_id()
        self.this_device_id_label.configure(text=f"ID: {my_id or 'Initializing...'}")

        # Reload other devices list
        for iid in self.devices_tree.get_children(""):
            self.devices_tree.delete(iid)

        clients = self.controller.get_clients()
        # Filter out this device if its ID is known
        other_clients = [c for c in clients if my_id and c.get('syncthing_id') != my_id]

        if not other_clients:
            self.devices_tree.insert("", "end", iid="__empty__",
                                     text="No other devices added", values=("",),
                                     tags=("placeholder",))
            return

        for client in sorted(other_clients, key=lambda c: c.get('name', '')):
            client_id = client.get('syncthing_id', 'Unknown ID')
            self.devices_tree.insert("", "end", iid=client_id,
                                     text=client.get('name', 'Unnamed Device'),
                                     values=(client_id,))

    def _remove_selected_device(self):
        sel = self.devices_tree.selection()
        if not sel or sel[0] == "__empty__":
            return
        self.controller.remove_client(sel[0])

    # --- SSH Keys Tab ---
    def _create_ssh_keys_tab(self):
        tab = self._tabs["SSH Keys"]
        tab.grid_columnconfigure(1, weight=1) # Entry column expands

        ssh_frame = ctk.CTkFrame(tab)
        ssh_frame.pack(fill="x", padx=10, pady=10)
        ssh_frame.grid_columnconfigure(1, weight=1) # Entry column expands

        ctk.CTkLabel(ssh_frame, text="SSH Key Pair for Server Administration", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 5))
        ctk.CTkLabel(ssh_frame, text="Used for one-time provisioning and route sync on your servers.\nTunnels themselves run over FRP/QUIC (no per-tunnel SSH).",
                     font=ctk.CTkFont(size=11), text_color="gray").grid(row=1, column=0, columnspan=3, padx=10, pady=(0, 10))

        ctk.CTkLabel(ssh_frame, text="Private Key:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.priv_key_entry = ttk.Entry(ssh_frame)
        self.priv_key_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ttk.Button(ssh_frame, text="Browse...", style="secondary.TButton", command=self._browse_private_key).grid(row=2, column=2, padx=10, pady=5)

        ctk.CTkLabel(ssh_frame, text="Public Key:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.pub_key_entry = ttk.Entry(ssh_frame)
        self.pub_key_entry.grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        ttk.Button(ssh_frame, text="Browse...", style="secondary.TButton", command=self._browse_public_key).grid(row=3, column=2, padx=10, pady=5)

        btn_row = ctk.CTkFrame(ssh_frame, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=3, pady=(15, 10))
        ttk.Button(btn_row, text="Save SSH Key Paths", style="primary.TButton", command=self._save_ssh_keys_action).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Generate New Key Pair", style="secondary.TButton",
                   command=self._generate_keys_action).pack(side="left", padx=5)

        # Add a frame for confirmation message
        self.ssh_confirm_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.ssh_confirm_frame.pack(fill="x", padx=10, pady=0)


    def _load_ssh_keys_data(self):
        creds = self.controller.get_automation_credentials()
        self.priv_key_entry.delete(0, "end")
        self.pub_key_entry.delete(0, "end")
        if creds:
            self.priv_key_entry.insert(0, creds.get('ssh_private_key_path', ''))
            self.pub_key_entry.insert(0, creds.get('ssh_public_key_path', ''))
        # Clear any previous confirmation message
        for widget in self.ssh_confirm_frame.winfo_children():
            widget.destroy()

    def _save_ssh_keys_action(self, passphrase: str = None):
        priv_path = self.priv_key_entry.get().strip()
        pub_path = self.pub_key_entry.get().strip()
        if not priv_path:
            self.controller.show_error("Private key path cannot be empty.")
            return
        if not os.path.exists(priv_path):
             self.controller.show_error(f"Private key file not found:\n{priv_path}")
             return
        if pub_path and not os.path.exists(pub_path):
             self.controller.show_error(f"Public key file not found:\n{pub_path}")
             return

        # Clear previous confirmation first
        for widget in self.ssh_confirm_frame.winfo_children():
            widget.destroy()

        self.controller.save_automation_credentials(priv_path, pub_path, passphrase)

        # Show confirmation message within its dedicated frame
        confirm_label = ctk.CTkLabel(self.ssh_confirm_frame, text="✓ SSH key paths saved!", text_color="green")
        confirm_label.pack(pady=(0, 10))
        # Schedule removal
        confirm_label.after(3000, lambda: confirm_label.destroy() if confirm_label.winfo_exists() else None)

    def _generate_keys_action(self):
        """Generates a fresh RSA key pair in the app data dir and saves the paths."""
        result = self.controller.generate_ssh_key_pair()
        if not result:
            self.controller.show_error("Key Generation Failed", "Could not generate an SSH key pair.")
            return
        priv_path, pub_path, passphrase = result
        self.priv_key_entry.delete(0, "end")
        self.priv_key_entry.insert(0, priv_path)
        self.pub_key_entry.delete(0, "end")
        self.pub_key_entry.insert(0, pub_path)
        self._save_ssh_keys_action(passphrase=passphrase)

    # --- Password Tab ---
    def _create_password_tab(self):
        tab = self._tabs["Password"]
        # Use a main frame for centering content if needed, or just pack directly
        pass_frame = ctk.CTkFrame(tab)
        pass_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(pass_frame, text="Master Password Management", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        ttk.Button(pass_frame, text="Change Master Password...", style="primary.TButton", command=self.controller.change_master_password).pack(pady=5, anchor="center", padx=20)
        ttk.Button(pass_frame, text="View Recovery Key...", style="secondary.TButton", command=self.controller.view_recovery_key).pack(pady=5, anchor="center", padx=20)

        lock_frame = ctk.CTkFrame(tab)
        lock_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(lock_frame, text="Session Lock", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 5))
        al_row = ctk.CTkFrame(lock_frame, fg_color="transparent")
        al_row.pack(pady=5)
        ctk.CTkLabel(al_row, text="Auto-lock after:").pack(side="left", padx=5)
        self._auto_lock_labels = ("Never", "5 minutes", "15 minutes", "30 minutes", "60 minutes")
        self._auto_lock_values = {label: m for label, m in zip(self._auto_lock_labels, (0, 5, 15, 30, 60))}
        self._auto_lock_inv = {m: label for label, m in self._auto_lock_values.items()}
        self.auto_lock_menu = ttk.Combobox(al_row, values=list(self._auto_lock_labels),
                                           state="readonly", width=12)
        self.auto_lock_menu.set("Never")
        self.auto_lock_menu.bind("<<ComboboxSelected>>", self._on_auto_lock_change)
        self.auto_lock_menu.pack(side="left", padx=5)
        ttk.Button(lock_frame, text="Lock Now", style="secondary.TButton",
                   command=self.controller.lock).pack(pady=(0, 10), anchor="center", padx=20)

        backup_frame = ctk.CTkFrame(tab)
        backup_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(backup_frame, text="Backup", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 5))
        ctk.CTkLabel(backup_frame, text="Exports an encrypted copy of your config, credentials, and keys. Restore by unzipping into the app data folder.",
                     justify="center", text_color="#aaaaaa", wraplength=380).pack(pady=5)
        ttk.Button(backup_frame, text="Export Encrypted Backup...", style="secondary.TButton",
                   command=self.controller.export_backup).pack(pady=(0, 10), anchor="center", padx=20)

        update_frame = ctk.CTkFrame(tab)
        update_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(update_frame, text="Updates", font=ctk.CTkFont(weight="bold")).pack(pady=(10, 5))
        self.update_status_label = ctk.CTkLabel(update_frame, text="",
                                              text_color="#aaaaaa", wraplength=380, justify="center")
        self.update_status_label.pack(pady=5)
        ttk.Button(update_frame, text="Check for Updates", style="secondary.TButton",
                   command=self._check_for_updates).pack(pady=(0, 10), anchor="center", padx=20)

    def _load_security_data(self):
        minutes = 0
        try:
            minutes = int(self.controller.get_setting('auto_lock_minutes', 0) or 0)
        except (TypeError, ValueError, AttributeError):
            pass
        self.auto_lock_menu.set(self._auto_lock_inv.get(minutes, "Never"))

    def _on_auto_lock_change(self, _event=None):
        minutes = self._auto_lock_values.get(self.auto_lock_menu.get(), 0)
        self.controller.set_setting('auto_lock_minutes', minutes)

    def _check_for_updates(self):
        self.update_status_label.configure(text="Checking...", text_color=tk_color("fg_dim"))
        self.controller.check_for_updates(
            lambda text: self.update_status_label.configure(
                text=text,
                text_color=("#2da44e", "#3fb950") if text.startswith("Up to date") else
                          ("#9a6a00", "#e0a030") if text.startswith("Update available") else
                          ("#c93c37", "#f08080")))

    # --- Appearance Tab ---
    def _create_appearance_tab(self):
        tab = self._tabs["Appearance"]
        appearance_frame = ctk.CTkFrame(tab)
        appearance_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(appearance_frame, text="Appearance Mode:").pack(side="left", padx=10, pady=10)

        self.appearance_menu = ttk.Combobox(appearance_frame,
                                            values=["Light", "Dark", "System"],
                                            state="readonly", width=12)
        self.appearance_menu.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.controller.set_appearance_mode(self.appearance_menu.get()))
        self.appearance_menu.pack(side="left", padx=10, pady=10)
        # Value is set in _load_appearance_data

    def _load_appearance_data(self):
         """Sets the appearance menu to the current mode."""
         current_mode = ctk.get_appearance_mode()
         self.appearance_menu.set(current_mode)

    # --- Browse Methods (Unchanged) ---
    def _browse_private_key(self):
        path = self.controller.browse_for_file("Select Private Key File");
        if path:
             self.priv_key_entry.delete(0, "end")
             self.priv_key_entry.insert(0, path)

    def _browse_public_key(self):
        path = self.controller.browse_for_file("Select Public Key File")
        if path:
             self.pub_key_entry.delete(0, "end")
             self.pub_key_entry.insert(0, path)