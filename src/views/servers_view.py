# In views/servers_view.py

import customtkinter as ctk
import logging
# Import ToolTip and ConfirmationDialog for buttons
from .dialogs import ToolTip, ConfirmationDialog 

class ServersView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent") # Blend with content area
        self.controller = controller
        # Ensure images are loaded and available from the controller
        self.images = controller.images if hasattr(controller, 'images') else {}
        self.btn_images = getattr(controller, 'btn_images', self.images)
        if not self.images:
            logging.error("ServersView: Images not found in controller!")
            
        # --- TOOLTIP INSTANCE ---
        # Get the shared tooltip instance from the controller (App)
        self.tooltip = self.controller.tooltip if hasattr(self.controller, 'tooltip') else None
        if not self.tooltip:
             logging.error("ServersView: Tooltip instance not found in controller!")
        # --- END TOOLTIP ---

        self.server_item_frames = {} # Cache for server item widgets

        # --- Main Layout ---
        self.grid_columnconfigure(0, weight=1) # Main column expands
        self.grid_rowconfigure(1, weight=1) # Row 1 (list container) expands vertically

        # --- Top Control Frame ---
        self.control_frame = ctk.CTkFrame(self)
        self.control_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")

        # Add Server Button (moved to top left)
        ctk.CTkButton(self.control_frame, text="Add Server",
                      image=self.btn_images.get("add"), compound="left",
                      command=self.controller.add_new_server # Use controller method
                     ).pack(side="left", padx=5, pady=5)

        # --- Server List Container ---
        self.list_container = ctk.CTkFrame(self, fg_color="transparent")
        self.list_container.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self.list_container.grid_rowconfigure(0, weight=1)
        self.list_container.grid_columnconfigure(0, weight=1)

        # --- Scrollable Frame (Created in load_servers) ---
        self.server_list_frame = None 

    def on_enter(self):
        """Called every time the view is shown."""
        logging.debug("Entering ServersView.")
        self.load_servers() # Renamed from _load_servers_data for consistency

    def on_leave(self):
        """Called when ServersView is hidden."""
        logging.debug("Leaving ServersView.")
        # Clear UI elements to prevent stale data or widget issues
        self._clear_server_list()

    def _clear_server_list(self):
        """Destroys all server item widgets inside the scrollable frame."""
        # Hide tooltip immediately if shown
        if self.tooltip:
             try:
                 # --- FIX: Use schedule_hide ---
                 self.tooltip.schedule_hide(event=None) 
             except Exception:
                 pass # Ignore error if tooltip is gone
        
        if hasattr(self, 'server_list_frame') and self.server_list_frame and self.server_list_frame.winfo_exists():
            # Destroy children widgets first
            for widget in self.server_list_frame.winfo_children():
                try:
                   if widget.winfo_exists(): widget.destroy()
                except Exception: pass
            # Destroy the frame itself
            try:
                 self.server_list_frame.destroy()
            except Exception: pass
                 
        self.server_list_frame = None # Reset reference
        self.server_item_frames.clear() # Clear the cache

    def load_servers(self):
        """Loads server data from controller and populates the UI list."""
        logging.info("Loading servers into view...")
        self._clear_server_list() # Clear previous frame and items

        # --- Re-create scrollable frame ---
        if not hasattr(self, 'list_container') or not self.list_container or not self.list_container.winfo_exists():
             logging.error("Cannot load servers, list_container not found.")
             return 

        self.server_list_frame = ctk.CTkScrollableFrame(self.list_container, label_text="Registered Servers")
        self.server_list_frame.grid(row=0, column=0, sticky="nsew")
        self.server_list_frame.grid_columnconfigure(0, weight=1) # Allow content to expand horizontally
        # --- End Frame Creation ---

        try:
            servers = self.controller.get_servers() # Get data from App controller
            if not servers:
                ctk.CTkLabel(self.server_list_frame, text="No servers registered yet. Click 'Add Server' to begin.").pack(pady=20, padx=20)
                return

            sorted_servers = sorted(servers, key=lambda s: s.get('name', ''))

            for server in sorted_servers:
                try:
                    server_id = server.get('id')
                    if not server_id:
                        logging.warning(f"Skipping server item with no ID: {server}")
                        continue

                    is_provisioned = server.get('is_provisioned', False)
                    server_name = server.get('name', 'Unnamed Server')

                    item_frame = ctk.CTkFrame(self.server_list_frame)
                    item_frame.pack(fill="x", pady=5, padx=5)
                    self.server_item_frames[server_id] = item_frame

                    item_frame.grid_columnconfigure(1, weight=1) 

                    # --- FIX: Use warning emoji for consistency ---
                    status_text = "✅ Ready" if is_provisioned else "⚠️ Setup Needed"
                    status_color = "green" if is_provisioned else ("#FFA000", "#FFC107")
                    status_label = ctk.CTkLabel(item_frame, text=status_text, width=120, text_color=status_color, anchor="w")
                    status_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")

                    ip_address = server.get('ip_address', 'No IP')
                    info_text = f"{server_name} ({ip_address})"
                    info_label = ctk.CTkLabel(item_frame, text=info_text, font=ctk.CTkFont(weight="bold"), anchor="w")
                    info_label.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

                    btn_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
                    btn_frame.grid(row=0, column=2, padx=(5, 10), pady=5, sticky="e")
                    btn_width = 30 

                    # --- Setup / Re-provision Button ---
                    setup_icon = self.btn_images.get("setup")
                    setup_btn = ctk.CTkButton(btn_frame, text="", image=setup_icon,
                                             width=btn_width,
                                             command=lambda s=server: self._ask_provision(s))
                    setup_btn.pack(side="left", padx=3)
                    
                    # --- FIX: Bind to setup_btn, not edit_btn ---
                    if self.tooltip:
                        tooltip_text = f"Re-provision {server_name}" if is_provisioned else f"Run Setup for {server_name}"
                        setup_btn.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
                        setup_btn.bind("<Leave>", self.tooltip.schedule_hide)

                    # --- Edit Button (always shown) ---
                    edit_icon = self.btn_images.get("edit")
                    edit_btn = ctk.CTkButton(btn_frame, text="", width=btn_width,
                                               image=edit_icon,
                                               command=lambda sid=server_id: self.controller.edit_server(sid))
                    edit_btn.pack(side="left", padx=3)

                    # --- FIX: Tooltip binding moved *after* button creation ---
                    if self.tooltip:
                        tooltip_text = f"Edit {server_name}"
                        edit_btn.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
                        edit_btn.bind("<Leave>", self.tooltip.schedule_hide)

                    # --- Delete Button (always shown) ---
                    delete_icon = self.btn_images.get("delete")
                    delete_btn = ctk.CTkButton(btn_frame, text="", width=btn_width,
                                                 image=delete_icon,
                                                 fg_color="#D32F2F", hover_color="#B71C1C",
                                                 command=lambda sid=server_id: self.controller.delete_server(sid))
                    delete_btn.pack(side="left", padx=3)
                    
                    # --- FIX: Tooltip binding moved *after* button creation ---
                    if self.tooltip:
                        tooltip_text = f"Delete {server_name}"
                        delete_btn.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
                        delete_btn.bind("<Leave>", self.tooltip.schedule_hide)


                except Exception as e:
                    logging.error(f"Error creating server widget for ID {server.get('id', 'UNKNOWN')}: {e}", exc_info=True)
                    if self.server_list_frame and self.server_list_frame.winfo_exists():
                         error_label = ctk.CTkLabel(self.server_list_frame, text=f"Error loading server {server.get('id', 'UNKNOWN')}", text_color="red")
                         error_label.pack(fill="x", pady=5, padx=5)

            logging.info(f"Displayed {len(servers)} servers.")

        except Exception as e:
            logging.error(f"Critical error during load_servers: {e}", exc_info=True)
            if self.server_list_frame and self.server_list_frame.winfo_exists():
                 ctk.CTkLabel(self.server_list_frame, text="An error occurred loading servers. Check logs.", text_color="red").pack(padx=20, pady=20)

    # --- *** MODIFIED METHOD *** ---
    def _ask_provision(self, server):
        """
        Handles the provision button click by *directly* calling the controller.
        The confirmation dialog has been removed.
        """
        server_name = server.get('name', server['ip_address'])
        logging.info(f"Setup button clicked for server {server_name}. Bypassing confirmation.")
        
        # Directly call the controller's provision method.
        # This method will now show the dialog to ask for credentials.
        self.controller.provision_server(server, "", "")