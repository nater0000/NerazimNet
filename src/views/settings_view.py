import customtkinter as ctk
import logging
import os

class SettingsView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent") # Blend with content_frame
        self.controller = controller
        self.images = controller.images # Get images from controller

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1) # Main content now on row 0

        # --- Tabbed Interface ---
        self.tab_view = ctk.CTkTabview(self, anchor="w")
        self.tab_view.grid(row=0, column=0, padx=10, pady=10, sticky="nsew") # Grid row=0
        # --- REORDERED AND RENAMED Tabs ---
        self.tab_view.add("Devices")
        self.tab_view.add("SSH Keys") # Was part of Security
        self.tab_view.add("Password") # Was part of Security
        self.tab_view.add("Appearance") # Was General
        # --- Set new default tab ---
        self.tab_view.set("Devices")

        # Create the static content (widgets) for each tab
        self._create_devices_tab()
        self._create_ssh_keys_tab()
        self._create_password_tab()
        self._create_appearance_tab()

    def on_enter(self):
        """Called every time the view is shown to refresh all dynamic data."""
        logging.debug("Entering SettingsView, reloading data.")
        # Load data for the visible tabs
        self._load_devices_data()
        self._load_ssh_keys_data()
        self._load_appearance_data() # Update appearance menu setting

    # --- REMOVED Server Tab Methods ---

    # --- Devices Tab ---
    def _create_devices_tab(self):
        tab = self.tab_view.tab("Devices")
        tab.grid_columnconfigure(0, weight=1); tab.grid_rowconfigure(2, weight=1) # Row 2 for scroll frame

        # Frame for displaying this device's info
        this_device_frame = ctk.CTkFrame(tab)
        this_device_frame.pack(fill="x", padx=10, pady=(10, 5)) # Reduced bottom padding
        self.this_device_name_label = ctk.CTkLabel(this_device_frame, text="This Device: ...", font=ctk.CTkFont(weight="bold"))
        self.this_device_name_label.pack(anchor="w", padx=10, pady=(10,0))
        self.this_device_id_label = ctk.CTkLabel(this_device_frame, text="ID: ...", wraplength=500, justify="left")
        self.this_device_id_label.pack(anchor="w", padx=10, pady=(0,10))

        # Invite button below the device info
        ctk.CTkButton(tab, text="Invite Another Device",
                      image=self.images.get("add_device"), compound="left",
                      command=self.controller.add_new_device).pack(pady=5) # Reduced padding

        # Scrollable frame for other devices
        self.other_devices_frame = ctk.CTkScrollableFrame(tab, label_text="Other Synced Devices")
        self.other_devices_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10)) # Reduced top padding

    def _load_devices_data(self):
        # Update this device's info
        self.this_device_name_label.configure(text=f"This Device: {self.controller.get_my_device_name()}")
        my_id = self.controller.get_my_device_id()
        self.this_device_id_label.configure(text=f"ID: {my_id or 'Initializing...'}")

        # Clear and reload other devices list
        for widget in self.other_devices_frame.winfo_children(): widget.destroy()

        clients = self.controller.get_clients()
        # Filter out this device if its ID is known
        other_clients = [c for c in clients if my_id and c.get('syncthing_id') != my_id]

        if not other_clients:
            ctk.CTkLabel(self.other_devices_frame, text="No other devices have been added yet.").pack(pady=20, padx=20)
            return

        for client in other_clients:
            client_id = client.get('syncthing_id', 'Unknown ID')
            client_name = client.get('name', 'Unnamed Device')

            item_frame = ctk.CTkFrame(self.other_devices_frame)
            item_frame.pack(fill="x", pady=5, padx=5)
            item_frame.grid_columnconfigure(0, weight=1) # Label expands

            label_text = f"{client_name}\nID: {client_id}"
            ctk.CTkLabel(item_frame, text=label_text, justify="left", anchor="w").grid(row=0, column=0, padx=10, pady=5, sticky="ew")

            remove_btn = ctk.CTkButton(item_frame, text="", width=30,
                                       image=self.images.get("delete"),
                                       fg_color="#D32F2F", hover_color="#B71C1C", # Standard delete colors
                                       command=lambda cid=client_id: self.controller.remove_client(cid))
            remove_btn.grid(row=0, column=1, padx=10, pady=5)
            # Add Tooltip if desired: ToolTip(remove_btn, f"Remove {client_name}")

    # --- SSH Keys Tab ---
    def _create_ssh_keys_tab(self):
        tab = self.tab_view.tab("SSH Keys")
        tab.grid_columnconfigure(1, weight=1) # Entry column expands

        ssh_frame = ctk.CTkFrame(tab)
        ssh_frame.pack(fill="x", padx=10, pady=10)
        ssh_frame.grid_columnconfigure(1, weight=1) # Entry column expands

        ctk.CTkLabel(ssh_frame, text="SSH Key Pair for Server Administration", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 5))
        ctk.CTkLabel(ssh_frame, text="Used for one-time provisioning and route sync on your servers.\nTunnels themselves run over FRP/QUIC (no per-tunnel SSH).",
                     font=ctk.CTkFont(size=11), text_color="gray").grid(row=1, column=0, columnspan=3, padx=10, pady=(0, 10))

        ctk.CTkLabel(ssh_frame, text="Private Key:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.priv_key_entry = ctk.CTkEntry(ssh_frame)
        self.priv_key_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkButton(ssh_frame, text="Browse...", width=80, command=self._browse_private_key).grid(row=2, column=2, padx=10, pady=5)

        ctk.CTkLabel(ssh_frame, text="Public Key:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        self.pub_key_entry = ctk.CTkEntry(ssh_frame)
        self.pub_key_entry.grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkButton(ssh_frame, text="Browse...", width=80, command=self._browse_public_key).grid(row=3, column=2, padx=10, pady=5)

        btn_row = ctk.CTkFrame(ssh_frame, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=3, pady=(15, 10))
        ctk.CTkButton(btn_row, text="Save SSH Key Paths", command=self._save_ssh_keys_action).pack(side="left", padx=5)
        ctk.CTkButton(btn_row, text="Generate New Key Pair", command=self._generate_keys_action,
                      fg_color="transparent", border_width=1,
                      text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"]).pack(side="left", padx=5)

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

    def _save_ssh_keys_action(self):
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

        self.controller.save_automation_credentials(priv_path, pub_path)

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
        priv_path, pub_path = result
        self.priv_key_entry.delete(0, "end")
        self.priv_key_entry.insert(0, priv_path)
        self.pub_key_entry.delete(0, "end")
        self.pub_key_entry.insert(0, pub_path)
        self._save_ssh_keys_action()

    # --- Password Tab ---
    def _create_password_tab(self):
        tab = self.tab_view.tab("Password")
        # Use a main frame for centering content if needed, or just pack directly
        pass_frame = ctk.CTkFrame(tab)
        pass_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(pass_frame, text="Master Password Management", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        ctk.CTkButton(pass_frame, text="Change Master Password...", command=self.controller.change_master_password).pack(pady=5, anchor="center", padx=20)
        ctk.CTkButton(pass_frame, text="View Recovery Key...", command=self.controller.view_recovery_key).pack(pady=5, anchor="center", padx=20)

    # --- Appearance Tab ---
    def _create_appearance_tab(self):
        tab = self.tab_view.tab("Appearance")
        appearance_frame = ctk.CTkFrame(tab)
        appearance_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(appearance_frame, text="Appearance Mode:").pack(side="left", padx=10, pady=10)

        self.appearance_menu = ctk.CTkOptionMenu(appearance_frame,
                                                 values=["Light", "Dark", "System"],
                                                 command=self.controller.set_appearance_mode)
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