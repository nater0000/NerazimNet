import customtkinter as ctk
import logging
from .dialogs import ToolTip # Assuming ToolTip is still your Toplevel-based class
import tkinter # Import tkinter for checking widget existence

class DashboardView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.images = controller.images if hasattr(controller, 'images') else {}
        self.current_statuses = {} # Cache statuses

        # --- Shared Tooltip Instance ---
        self.shared_tooltip = self.controller.tooltip if hasattr(self.controller, 'tooltip') else None
        if not self.shared_tooltip:
             logging.error("DashboardView: Tooltip instance not found in controller!")
        # --- End Tooltip Section ---
        
        # --- Status Colors ---
        default_text_color = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        self.status_colors = {
            "running": ("#2E7D32", "Connected"), # Green
            "stopped": (default_text_color, "Stopped"), # Default text color
            "error": ("#D32F2F", "Error"), # Red
            "disabled": ("#616161", "Managed Elsewhere") # Gray
        }

        # --- Grid Configuration for DashboardView Frame ---
        self.grid_columnconfigure(0, weight=1) # The single column expands horizontally
        self.grid_rowconfigure(0, weight=0) # Row 0: Control frame (fixed height)
        self.grid_rowconfigure(1, weight=1) # Row 1: Tunnel list (expands vertically)
        self.grid_rowconfigure(2, weight=0) # Row 2: Legend frame (fixed height)


        # --- Top Control Frame (Row 0) ---
        self.control_frame = ctk.CTkFrame(self)
        self.control_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew") # Pad bottom only 0
        ctk.CTkButton(self.control_frame, text="Add Tunnel",
                      image=self.images.get("add"), compound="left",
                      command=self.controller.add_new_tunnel).pack(side="left", padx=5, pady=5)
        start_stop_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        start_stop_frame.pack(side="right", padx=5, pady=5)
        ctk.CTkButton(start_stop_frame, text="Start All",
                      image=self.images.get("start"), compound="left",
                      command=self.controller.start_all_tunnels).pack(side="left", padx=5)
        ctk.CTkButton(start_stop_frame, text="Stop All",
                      image=self.images.get("stop"), compound="left",
                      fg_color="#D32F2F", hover_color="#B71C1C",
                      command=self.controller.stop_all_tunnels).pack(side="left", padx=5)


        # --- Tunnel List Container (Row 1 - Expands) ---
        self.list_container = ctk.CTkFrame(self, fg_color="transparent")
        self.list_container.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.list_container.grid_rowconfigure(0, weight=1)
        self.list_container.grid_columnconfigure(0, weight=1)
        self.tunnel_list_frame = ctk.CTkScrollableFrame(self.list_container, label_text="Configured Tunnels")
        self.tunnel_list_frame.grid(row=0, column=0, sticky="nsew")
        self.tunnel_list_frame.grid_columnconfigure(0, weight=1)


        # --- Bottom Legend Frame (Row 2 - Fixed Height) ---
        self.legend_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.legend_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        ctk.CTkLabel(self.legend_frame, text="Legend:").pack(side="left", padx=(5, 10))
        legend_items = [
            ("✅", self.status_colors["running"][0], "Running"),
            ("⚠️", self.status_colors["error"][0], "Error"),
            ("⚪", self.status_colors["stopped"][0], "Stopped"),
            ("🔘", self.status_colors["disabled"][0], "Managed Elsewhere")
        ]
        for icon, color, text in legend_items:
             item_frame = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
             item_frame.pack(side="left", padx=5)
             ctk.CTkLabel(item_frame, text=icon, text_color=color, font=ctk.CTkFont(size=14)).pack(side="left")
             ctk.CTkLabel(item_frame, text=text, font=ctk.CTkFont(size=12)).pack(side="left", padx=(2, 0))

        # --- Internal State ---
        self.status_check_timer = None
        self.tunnel_item_frames = {} # {tunnel_id: item_frame_widget}
        self.server_header_frames = {} # {server_name: header_widget}
        
    def on_enter(self):
        """Called when view is shown. Performs a full sync/rebuild."""
        logging.debug("Entering DashboardView.")
        self.sync_tunnel_list() # Use the differential sync method
        self.poll_statuses() # Start polling

    def on_leave(self):
        """Called when view is hidden."""
        logging.debug("Leaving DashboardView.")
        
        # 1. Stop the polling timer
        if self.status_check_timer:
            try: 
                self.after_cancel(self.status_check_timer)
            except ValueError: 
                pass
            self.status_check_timer = None

        # 2. Hide any active tooltip immediately
        if self.shared_tooltip:
            try:
                # --- FIX: Use schedule_hide ---
                self.shared_tooltip.schedule_hide(event=None) 
            except Exception:
                pass # Ignore errors if tooltip is already gone

        # --- DO NOT destroy widgets or clear caches here ---
        # The sync_tunnel_list method will handle this when we return.

    def poll_statuses(self):
        """Periodically polls tunnel statuses and refreshes UI if needed."""
        if not self.winfo_exists() or not self.winfo_viewable():
            self.status_check_timer = None
            return
        self.refresh_tunnel_statuses() # Call lightweight status update
        self.status_check_timer = self.after(2000, self.poll_statuses)

    def sync_tunnel_list(self):
        """
        Synchronizes the displayed list with the current tunnel configuration.
        Adds new tunnels, removes deleted ones, and updates existing ones.
        Called on entering the view.
        """
        logging.debug("Synchronizing tunnel list UI.")
        try:
            tunnels_config = self.controller.get_tunnels()
            if not tunnels_config: # No tunnels configured
                 if self.tunnel_list_frame and self.tunnel_list_frame.winfo_exists():
                      for widget in self.tunnel_list_frame.winfo_children():
                           widget.destroy()
                 self.tunnel_item_frames.clear()
                 self.server_header_frames.clear()
                 no_tunnels_label = ctk.CTkLabel(self.tunnel_list_frame, text="No tunnels configured...")
                 no_tunnels_label.pack(pady=20)
                 self.tunnel_item_frames["__no_tunnels_label__"] = no_tunnels_label 
                 self.current_statuses = {} 
                 return

            if "__no_tunnels_label__" in self.tunnel_item_frames:
                 if self.tunnel_item_frames["__no_tunnels_label__"].winfo_exists():
                      self.tunnel_item_frames["__no_tunnels_label__"].destroy()
                 del self.tunnel_item_frames["__no_tunnels_label__"]

            self.current_statuses = self.controller.get_tunnel_statuses()
            config_ids = {t['id'] for t in tunnels_config}
            current_ui_ids = set(self.tunnel_item_frames.keys())

            ids_to_remove = current_ui_ids - config_ids
            for tunnel_id in ids_to_remove:
                if tunnel_id in self.tunnel_item_frames:
                    logging.debug(f"Removing tunnel item {tunnel_id} from UI.")
                    item_frame = self.tunnel_item_frames.pop(tunnel_id)
                    if item_frame and item_frame.winfo_exists():
                        item_frame.destroy()
                self.current_statuses.pop(tunnel_id, None)

            sorted_tunnels_config = sorted(tunnels_config, key=lambda t: (
                self.controller.get_server_name(t.get('server_id', '')), t.get('hostname', '')
            ))

            processed_server_headers = set()
            last_packed_widget = None 

            for tunnel in sorted_tunnels_config:
                tunnel_id = tunnel['id']
                server_name = self.controller.get_server_name(tunnel.get('server_id', ''))

                if server_name not in self.server_header_frames:
                    header = ctk.CTkLabel(self.tunnel_list_frame, text=server_name,
                                          font=ctk.CTkFont(size=14, weight="bold"),
                                          anchor="w", fg_color=("gray90", "gray20"))
                    if last_packed_widget:
                        header.pack(fill="x", padx=5, pady=(10, 5), ipady=2, after=last_packed_widget)
                    else:
                        header.pack(fill="x", padx=5, pady=(10, 5), ipady=2)
                    self.server_header_frames[server_name] = header
                    last_packed_widget = header
                elif server_name not in processed_server_headers:
                     header = self.server_header_frames[server_name]
                     if last_packed_widget:
                          header.pack_configure(after=last_packed_widget)
                     else:
                          header.pack_configure() 
                     last_packed_widget = header
                processed_server_headers.add(server_name)


                status_obj = self.current_statuses.get(tunnel_id, {'status': 'stopped', 'message': 'Stopped'})
                if tunnel_id in self.tunnel_item_frames:
                    item_frame = self.tunnel_item_frames[tunnel_id]
                    if item_frame.winfo_exists():
                        self._update_item_status(item_frame, status_obj)
                        if last_packed_widget:
                             item_frame.pack_configure(after=last_packed_widget)
                        else:
                             item_frame.pack_configure()
                        last_packed_widget = item_frame
                    else:
                         logging.warning(f"Found invalid frame for {tunnel_id} during sync, recreating.")
                         del self.tunnel_item_frames[tunnel_id]
                         item_frame = self._create_tunnel_item(tunnel, after_widget=last_packed_widget)
                         last_packed_widget = item_frame
                else:
                    logging.debug(f"Adding tunnel item {tunnel_id} to UI.")
                    item_frame = self._create_tunnel_item(tunnel, after_widget=last_packed_widget)
                    last_packed_widget = item_frame

            headers_to_remove = set(self.server_header_frames.keys()) - processed_server_headers
            for server_name in headers_to_remove:
                 logging.debug(f"Removing unused server header: {server_name}")
                 header = self.server_header_frames.pop(server_name)
                 if header and header.winfo_exists():
                      header.destroy()

        except Exception as e:
            logging.error(f"Error during tunnel list sync: {e}", exc_info=True)
            if self.tunnel_list_frame and self.tunnel_list_frame.winfo_exists():
                 for widget in self.tunnel_list_frame.winfo_children(): widget.destroy()
                 ctk.CTkLabel(self.tunnel_list_frame, text="Error loading tunnels.", text_color="red").pack(pady=20)


    def _create_tunnel_item(self, tunnel: dict, after_widget=None):
        """Creates widgets for a tunnel item, binds events, packs, and returns the frame."""
        tunnel_id = tunnel.get('id')
        if not tunnel_id: return None

        status_obj = self.current_statuses.get(tunnel_id, {'status': 'stopped', 'message': 'Stopped'})

        item_frame = ctk.CTkFrame(self.tunnel_list_frame)
        if after_widget:
            item_frame.pack(fill="x", pady=5, padx=5, after=after_widget)
        else:
             item_frame.pack(fill="x", pady=5, padx=5) 

        item_frame.grid_columnconfigure(1, weight=1)
        item_frame.tunnel_id = tunnel_id

        status_label = ctk.CTkLabel(item_frame, text="", anchor="w", justify="left")
        status_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        item_frame.status_label = status_label
        # --- FIX: Tooltip binding ---
        if self.shared_tooltip:
            status_label.bind("<Enter>", lambda event, tid=tunnel_id: self._bind_status_tooltip(event, tid))
            status_label.bind("<Leave>", self.shared_tooltip.schedule_hide)


        hostname = tunnel.get('hostname', 'N/A')
        remote_port = tunnel.get('remote_port', 'N/A')
        
        # --- *** UPDATED DISPLAY LOGIC *** ---
        route_type = tunnel.get('route_type', 'tunnel')
        
        if route_type == 'local':
             # For local routes, show it points to the VPS itself
             info_text = f"{hostname} -> VPS Service (Port {remote_port})"
        else:
             # Standard tunnel display
             local_dest = tunnel.get('local_destination', 'N/A')
             info_text = f"{hostname} (Port: {remote_port}) -> {local_dest}"
        # --- *** END UPDATE *** ---

        info_label = ctk.CTkLabel(item_frame, text=info_text, font=ctk.CTkFont(weight="bold"), anchor="w")
        info_label.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        btn_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=(5, 10), pady=5, sticky="e")
        btn_width = 30

        start_stop_btn = ctk.CTkButton(btn_frame, text="", width=btn_width)
        start_stop_btn.pack(side="left", padx=3)
        item_frame.start_stop_btn = start_stop_btn
        # --- FIX: Tooltip binding ---
        if self.shared_tooltip:
            start_stop_btn.bind("<Enter>", lambda event, tid=tunnel_id: self._bind_startstop_tooltip(event, tid))
            start_stop_btn.bind("<Leave>", self.shared_tooltip.schedule_hide)

        logs_btn = ctk.CTkButton(btn_frame, text="", width=btn_width, image=self.images.get("logs"), command=lambda tid=tunnel_id: self.controller.view_tunnel_log(tid))
        logs_btn.pack(side="left", padx=3)
        logs_text = f"View Logs for {hostname}"
        # --- FIX: Tooltip binding ---
        if self.shared_tooltip:
            logs_btn.bind("<Enter>", lambda e, text=logs_text: self.shared_tooltip.schedule_show(e, text))
            logs_btn.bind("<Leave>", self.shared_tooltip.schedule_hide)

        edit_btn = ctk.CTkButton(btn_frame, text="", width=btn_width, image=self.images.get("edit"), command=lambda tid=tunnel_id: self.controller.edit_tunnel(tid))
        edit_btn.pack(side="left", padx=3)
        edit_text = f"Edit {hostname}"
        # --- FIX: Tooltip binding ---
        if self.shared_tooltip:
            edit_btn.bind("<Enter>", lambda e, text=edit_text: self.shared_tooltip.schedule_show(e, text))
            edit_btn.bind("<Leave>", self.shared_tooltip.schedule_hide)

        delete_btn = ctk.CTkButton(btn_frame, text="", width=btn_width, image=self.images.get("delete"), fg_color="#D32F2F", hover_color="#B71C1C", command=lambda tid=tunnel_id: self.controller.delete_tunnel(tid))
        delete_btn.pack(side="left", padx=3)
        delete_text = f"Delete {hostname}"
        # --- FIX: Tooltip binding ---
        if self.shared_tooltip:
            delete_btn.bind("<Enter>", lambda e, text=delete_text: self.shared_tooltip.schedule_show(e, text))
            delete_btn.bind("<Leave>", self.shared_tooltip.schedule_hide)

        self._update_item_status(item_frame, status_obj)

        self.tunnel_item_frames[tunnel_id] = item_frame
        return item_frame 

    def _bind_status_tooltip(self, event, tunnel_id):
        """Helper to get dynamic status text for tooltip binding."""
        if not self.shared_tooltip: return
        try:
            status_obj = self.current_statuses.get(tunnel_id, {})
            status_key = status_obj.get('status', 'stopped')
            status_message = status_obj.get('message', self.status_colors.get(status_key, ["","Unknown"])[1])
            tooltip_text = f"Status: {status_message}"
            if status_key == "error" and "Port in use" in status_message: tooltip_text += "\n(Server port busy or stale connection)"
            elif status_key == "error" and "auth" in status_message.lower(): tooltip_text += "\n(Check FRP token / server provisioning)"
            
            # --- FIX: Pass text to schedule_show method ---
            self.shared_tooltip.schedule_show(event, tooltip_text)
        except Exception as e:
            logging.error(f"Error in _bind_status_tooltip: {e}")

    def _bind_startstop_tooltip(self, event, tunnel_id):
        """Helper to get dynamic start/stop text for tooltip binding."""
        if not self.shared_tooltip: return
        try:
            status_obj = self.current_statuses.get(tunnel_id, {})
            status_key = status_obj.get('status', 'stopped')
            tooltip_text = "Stop Tunnel" if status_key == "running" else "Start Tunnel"
            
            # --- FIX: Pass text to schedule_show method ---
            self.shared_tooltip.schedule_show(event, tooltip_text)
        except Exception as e:
            logging.error(f"Error in _bind_startstop_tooltip: {e}")

    def _update_item_status(self, item_frame: ctk.CTkFrame, status_obj: dict):
        """Updates the status label and start/stop button for a tunnel item."""
        try:
            if not item_frame or not item_frame.winfo_exists():
                return
        except Exception:
            return 

        tunnel_id = item_frame.tunnel_id
        status_key = status_obj.get('status', 'stopped') 
        status_color, default_text = self.status_colors.get(status_key, self.status_colors["stopped"]) 
        status_message = status_obj.get('message', default_text) 

        icon = "⚪"; 
        if status_key == "running": icon = "✅"
        elif status_key == "error": icon = "⚠️"
        elif status_key == "disabled": icon = "🔘"
        status_text = f"{icon} {status_message}" 

        try:
            if hasattr(item_frame, 'status_label') and item_frame.status_label.winfo_exists():
                item_frame.status_label.configure(text=status_text, text_color=status_color)
        except Exception as e:
            logging.warning(f"Error updating status label {tunnel_id}: {e}")

        try:
            if hasattr(item_frame, 'start_stop_btn') and item_frame.start_stop_btn.winfo_exists():
                is_running = (status_key == "running")
                
                btn_image = self.images.get("stop") if is_running else self.images.get("start")
                if not btn_image:
                     logging.warning(f"Missing image for {'stop' if is_running else 'start'} button!")
                
                btn_command = (lambda tid=tunnel_id: self.controller.stop_tunnel(tid)) if is_running else (lambda tid=tunnel_id: self.controller.start_tunnel(tid))
                btn_state = "disabled" if status_key == "disabled" else "normal"

                item_frame.start_stop_btn.configure(
                    image=btn_image,   
                    command=btn_command,
                    state=btn_state,
                    text=""            
                )
        except Exception as e:
            logging.warning(f"Error updating start/stop button {tunnel_id}: {e}")

    def refresh_tunnel_statuses(self):
        """
        Updates the status display for existing tunnel items based on current statuses.
        Does NOT add or remove tunnel items (handled by sync_tunnel_list).
        """
        if not hasattr(self, 'tunnel_list_frame') or not self.tunnel_list_frame or not self.tunnel_list_frame.winfo_exists():
            return

        try:
            new_statuses = self.controller.get_tunnel_statuses()

            if new_statuses == self.current_statuses:
                 return

            self.current_statuses = new_statuses

            for tunnel_id, status_obj in new_statuses.items():
                item_frame = self.tunnel_item_frames.get(tunnel_id)
                if item_frame:
                    try:
                        if item_frame.winfo_exists():
                            self._update_item_status(item_frame, status_obj)
                        else:
                            logging.warning(f"Item frame for {tunnel_id} destroyed, removing ref.")
                            self.tunnel_item_frames.pop(tunnel_id, None)
                    except Exception: 
                        logging.warning(f"Error checking item frame for {tunnel_id}, removing ref.")
                        self.tunnel_item_frames.pop(tunnel_id, None)

            current_ui_ids = set(self.tunnel_item_frames.keys())
            status_ids = set(new_statuses.keys())
            deleted_ids = current_ui_ids - status_ids
            if deleted_ids:
                 logging.warning(f"Detected tunnels {deleted_ids} in UI cache but not in status. Re-sync needed.")
                 self.after(100, self.sync_tunnel_list)


        except Exception as e:
            logging.error(f"Error during status refresh: {e}", exc_info=True)
            self.after(500, self.sync_tunnel_list)
            