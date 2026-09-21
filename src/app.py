# Full app.py

import customtkinter as ctk
import os
import logging
import threading
import sys
import pystray # For tray icon
import platform
from PIL import Image
from tkinter import PhotoImage
import re # Added for password validation in handle_first_run

# --- Controllers ---
from controllers.config_manager import ConfigManager
from controllers.syncthing_manager import SyncthingManager
from controllers.tunnel_manager import TunnelManager
from utils.crypto import CryptoManager
from utils.version import get_version, get_frp_version
from utils.staging import reset_firewall_rules
from controllers.server_provisioner import ServerProvisioner

# --- Views ---
from views.dashboard_view import DashboardView
from views.servers_view import ServersView
from views.settings_view import SettingsView
from views.history_view import HistoryView
from views.debug_view import DebugView
# --- CORRECTED TOOLTIP IMPORT ---
from views.dialogs import ToolTip 

# --- Dialogs ---
from views.dialogs import (
    BaseDialog, ErrorDialog, RecoveryKeyDialog, 
    ProvisionDialog, InviteDialog, ConfirmationDialog, 
    LogViewerDialog, ServerDialog, TunnelDialog, ProvisioningLogDialog
)

class App(ctk.CTk):

    def __init__(self, *args, **kwargs):
        """Initializes the main application window and core components."""
        super().__init__(*args, **kwargs)
        self._version = get_version()
        self.title(f"NerazimNet v{self._version}")

        # --- Icon Setup ---
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            base_dir = sys._MEIPASS
        icon_path = os.path.join(base_dir, "resources", "images", "nerazimnet.ico")
        self.tray_icon_path = icon_path
        if sys.platform == 'win32':
            if os.path.exists(icon_path):
                try:
                    self.iconbitmap(icon_path)
                    logging.info(f"App icon set from: {icon_path}")
                except Exception as e:
                    logging.warning(f"Failed to set app icon using iconbitmap: {e}")
            else:
                logging.warning(f"App icon file not found at: {icon_path}")
        else:
            # POSIX: iconbitmap only reads .ico on Windows — use PNG via iconphoto
            png_path = os.path.join(base_dir, "resources", "images", "nerazimnet_logo.png")
            if os.path.exists(png_path):
                try:
                    self.iconphoto(True, PhotoImage(file=png_path))
                    logging.info(f"App icon set from: {png_path}")
                except Exception as e:
                    logging.warning(f"Failed to set app icon via iconphoto: {e}")
        # --- End Icon Setup ---

        # --- Define Sizes ---
        self._initial_size = "400x400" 
        self._main_size = "900x550"
        self._main_minsize = (700, 500)
        
        # --- NEW: Sidebar Sizes ---
        self.sidebar_width_expanded = 160
        self.sidebar_width_collapsed = 60 # Icon-only width
        # Per-view sidebar backgrounds — slate-teal variants of the
        # theme's scrollbar color so each view stays in-family
        self.view_sidebar_colors = {
            "DashboardView": ("#527078", "#31505A"),
            "ServersView":   ("#4E6E82", "#2E4656"),
            "SettingsView":  ("#5D6E78", "#38474E"),
            "HistoryView":   ("#52756B", "#2C4F45"),
            "DebugView":     ("#63597A", "#3E3552"),
        }

        # --- Set Initial Geometry & State ---
        self.geometry(self._initial_size)
        self.resizable(False, False)
        self.minsize(0, 0)

        self.attributes("-topmost", True)
        logging.debug("Window set to always-on-top initially.")

        # --- Window Behavior Setup ---
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.minimized_to_tray = False

        # --- Initialize flags and events ---
        self.is_unlocked = False
        self.is_shutting_down = False
        self.syncthing_id_ready = threading.Event()
        self.sidebar_is_collapsed = False # State for new sidebar

        # --- Load Assets ---
        self.images = self._load_images()

        # --- Initialize managers ---
        self.config_manager = ConfigManager(self)
        self.syncthing_manager = SyncthingManager(self)
        self.tunnel_manager = TunnelManager(self)
        self.crypto_manager = CryptoManager()

        # --- Initialize the single tooltip instance ---
        # Added a longer delay so tooltips don't pop up in expanded view
        self.tooltip = ToolTip(self, show_delay_ms=700) 

        # --- Setup Blank/Initial UI Directly in App ---
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- Initialize UI component references ---
        self.sidebar_frame = None
        self.content_frame = None
        self.toggle_button = None # New toggle button (in sidebar)
        self.nav_buttons = [] # Cache for nav buttons
        # --- REMOVED: self.sidebar_toggle_button = None ---
        
        self.frames = {}
        self._initial_frame = None
        self.password_entry = None # For unlock screen
        self.setup_entry1 = None   # For setup screen
        self.setup_entry2 = None   # For setup screen
        self._focus_after_id = None
        self._loading_frame = None

        # Build the initial UI
        self._build_initial_ui()

        # --- Center Initial Window ---
        self.update_idletasks()
        self._center_window()

        # --- Setup System Tray Icon ---
        self.tray_icon = None
        self._setup_tray_icon()

        # --- Trigger initial state check ---
        self.after(50, self._initial_check)

    # --- Methods ---
    
    def _load_images(self) -> dict:
        logging.debug("Loading images...")
        images = {}
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            base_path = os.path.join(base_dir, "..")
        image_dir = os.path.join(base_path, "resources", "images")
        
        # --- Ensure "setup" key is here ---
        image_files = {
            "logo": "nerazimnet_logo.png", "logo_dark": "nerazimnet_logo.png",
            "menu": "menu.png", # For toggle button
            "dashboard": "dashboard.png", # Icon for Tunnels button
            "servers": "servers.png", # Icon for Servers button
            "settings": "settings.png", # Icon for Settings button
            "history": "history.png", # Icon for History button
            "debug": "debug.png", # Icon for Debug button
            "status-on": "status-on.png", "status-off": "status-off.png",
            "status-error": "status-error.png", "status-disabled": "status-disabled.png",
            "copy": "copy.png", "eye-show": "eye-show.png", "eye-hide": "eye-hide.png",
            "delete": "delete.png", "edit": "edit.png", "connect": "connect.png",
            "add": "add.png", "key": "key.png", "folder": "folder.png", "sync": "sync.png",
            "setup": "setup.png", # Added
            "start": "start.png", 
            "stop": "stop.png",
            "logs": "logs.png",
        }
        
        for name, filename in image_files.items():
            path = os.path.join(image_dir, filename)
            if os.path.exists(path):
                try:
                    img = Image.open(path)
                    if name.endswith("_dark"): continue
                    # Prefer a light-tinted variant for dark mode (dark/ subdir)
                    dark_path = os.path.join(image_dir, "dark", filename)
                    dark_img = Image.open(dark_path) if os.path.exists(dark_path) else img
                    images[name] = ctk.CTkImage(light_image=img, dark_image=dark_img, size=img.size)
                except Exception as e:
                    logging.warning(f"Failed to load image '{filename}': {e}")
            elif not name.endswith("_dark"):
                logging.warning(f"Image file not found: {path}")
        logging.debug(f"Loaded {len(images)} images.")

        # Button variants tinted to match the button text color in each mode
        # (surface icons are mint-on-dark; filled buttons need the inverse)
        btn_images = dict(images)
        for name, filename in image_files.items():
            if name.endswith("_dark"):
                continue
            light_path = os.path.join(image_dir, "btn", "light", filename)
            dark_path = os.path.join(image_dir, "btn", "dark", filename)
            if os.path.exists(light_path) and os.path.exists(dark_path):
                try:
                    light_img = Image.open(light_path)
                    dark_img = Image.open(dark_path)
                    btn_images[name] = ctk.CTkImage(light_image=light_img,
                                                  dark_image=dark_img,
                                                  size=light_img.size)
                except Exception as e:
                    logging.warning(f"Failed to load button image '{filename}': {e}")
        self.btn_images = btn_images

        # The sidebar sits on a dark slate in both appearance modes, so
        # its icons always use the light-tinted (dark/) variants
        sidebar_images = {}
        for name, filename in image_files.items():
            if name.endswith("_dark"):
                continue
            dark_path = os.path.join(image_dir, "dark", filename)
            if os.path.exists(dark_path):
                try:
                    icon = Image.open(dark_path)
                    sidebar_images[name] = ctk.CTkImage(
                        light_image=icon, dark_image=icon, size=icon.size)
                except Exception as e:
                    logging.warning(f"Failed to load sidebar image '{filename}': {e}")
        self.sidebar_images = sidebar_images
        return images

    def _create_sidebar(self, width: int):
        """Creates and populates the sidebar frame."""
        # --- Use passed-in width ---
        sidebar = ctk.CTkFrame(self, width=width, corner_radius=0,
                               fg_color=self.view_sidebar_colors["DashboardView"])
        sidebar.pack_propagate(False)
        sidebar.grid_propagate(False)
        
        # --- REMOVED Logo ---

        # --- NEW Toggle Button (at the top) ---
        # Sidebar bg is a dark slate in both modes — light text/icons always
        sidebar_text_color = ("#EAF5F0", "#EAF5F0")
        sidebar_hover = ("#6E8A92", "#40636E")
        menu_image = getattr(self, 'sidebar_images', {}).get("menu") or self.images.get("menu")
        self.toggle_button = ctk.CTkButton(
            sidebar,
            text="Collapse",
            image=menu_image,
            anchor="w",
            corner_radius=5,
            fg_color="transparent",
            hover_color=sidebar_hover,
            text_color=sidebar_text_color,
            command=self._toggle_sidebar
        )
        self.toggle_button.pack(fill="x", padx=10, pady=10)

        # Navigation buttons
        side_imgs = getattr(self, 'sidebar_images', {})
        nav_buttons = [
            ("DashboardView", "Tunnels", side_imgs.get("dashboard") or self.images.get("dashboard")),
            ("ServersView", "Servers", side_imgs.get("servers") or self.images.get("servers")),
            ("SettingsView", "Settings", side_imgs.get("settings") or self.images.get("settings")),
            ("HistoryView", "History", side_imgs.get("history") or self.images.get("history")),
            ("DebugView", "Debug", side_imgs.get("debug") or self.images.get("debug"))
        ]

        # --- Clear nav_buttons cache and rebuild ---
        self.nav_buttons = [] 
        for view_name, text, img in nav_buttons:
            btn = ctk.CTkButton(
                sidebar, text=text, image=img,
                anchor="w", corner_radius=5,
                fg_color="transparent", hover_color=sidebar_hover,
                text_color=sidebar_text_color,
                command=lambda v=view_name: self.show_frame(v)
            )
            btn.pack(fill="x", padx=10, pady=5)
            
            # --- Store original text for re-expansion ---
            btn.button_text = text 
            
            # --- Add tooltip for collapsed mode ---
            if self.tooltip:
                btn.bind("<Enter>", lambda e, text=text: self.tooltip.schedule_show(e, text))
                btn.bind("<Leave>", self.tooltip.schedule_hide)
            
            # --- Store button in cache ---
            self.nav_buttons.append(btn)

        return sidebar

    # --- *** REWRITTEN METHOD *** ---
    def _toggle_sidebar(self):
        """Shows or hides the sidebar text and resizes the frame."""
        if not self.sidebar_frame: return
        
        if self.sidebar_is_collapsed:
            # --- EXPAND ---
            logging.debug("Expanding sidebar...")
            self.sidebar_frame.configure(width=self.sidebar_width_expanded)
            
            # Configure toggle button
            self.toggle_button.configure(text="Collapse", anchor="w")
            
            # Configure nav buttons
            for btn in self.nav_buttons:
                btn.configure(text=btn.button_text, anchor="w")
                
            self.sidebar_is_collapsed = False
        else:
            # --- COLLAPSE ---
            logging.debug("Collapsing sidebar...")
            self.sidebar_frame.configure(width=self.sidebar_width_collapsed)
            
            # Configure toggle button (hide text, center icon)
            self.toggle_button.configure(text="", anchor="center")
            
            # Configure nav buttons (hide text, center icons)
            for btn in self.nav_buttons:
                btn.configure(text="", anchor="center")

            self.sidebar_is_collapsed = True

    def _build_main_ui(self):
        """Creates the main sidebar, content frame, and view frames AFTER unlock."""
        logging.debug("Building main UI components...")

        if hasattr(self, '_initial_frame') and self._initial_frame and self._initial_frame.winfo_exists():
             self._initial_frame.destroy(); self._initial_frame = None
        if hasattr(self, '_loading_frame') and self._loading_frame and self._loading_frame.winfo_exists():
             self._loading_frame.destroy(); self._loading_frame = None

        self.grid_columnconfigure(0, weight=0) # Sidebar column (fixed width)
        self.grid_columnconfigure(1, weight=1) # Content column (expands)
        self.grid_rowconfigure(0, weight=1)

        # Create and grid sidebar (use initial expanded width)
        self.sidebar_frame = self._create_sidebar(width=self.sidebar_width_expanded)
        if self.sidebar_frame: 
            self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        else: 
            logging.error("Failed to create sidebar frame!")

        # Create and grid content frame
        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_frame.grid(row=0, column=1, sticky="nsew")
        self.content_frame.grid_rowconfigure(0, weight=1); self.content_frame.grid_columnconfigure(0, weight=1)

        # --- REMOVED old toggle button creation and .place() ---

        # Create view frames inside content frame
        self.frames = {}
        logging.debug("Creating view frames...")
        for ViewClass in (DashboardView, ServersView, SettingsView, HistoryView, DebugView):
            page_name = ViewClass.__name__
            try:
                frame = ViewClass(parent=self.content_frame, controller=self)
                self.frames[page_name] = frame
                frame.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")
                frame.grid_remove()
                logging.debug(f"Created frame: {page_name}")
            except Exception as e:
                logging.error(f"Failed to create view frame {page_name}: {e}", exc_info=True)

        logging.debug("Main UI build complete.")

    def on_syncthing_id_ready(self):
        """Callback from SyncthingManager when the device ID is available."""
        self.syncthing_id_ready.set()
        logging.info("Syncthing ID is ready. Refreshing relevant views.")
        self.after(0, self.refresh_dashboard) 
        
        if "SettingsView" in self.frames and self.frames["SettingsView"].winfo_exists():
             if hasattr(self.frames["SettingsView"], 'on_syncthing_id_ready'):
                 self.after(0, self.frames["SettingsView"].on_syncthing_id_ready)
             elif hasattr(self.frames["SettingsView"], '_load_devices_data'): 
                  self.after(0, self.frames["SettingsView"]._load_devices_data)

    def _setup_tray_icon(self):
        """Initializes and starts the system tray icon thread."""
        if sys.platform == 'darwin':
            # pystray's macOS backend needs the Cocoa run loop on the main
            # thread, which collides with Tk's mainloop. macOS apps live in
            # the Dock natively, so skip the tray icon there.
            logging.info("Skipping system tray icon on macOS.")
            return
        try:
            if not os.path.exists(self.tray_icon_path):
                 logging.error(f"Cannot create tray icon: File not found at {self.tray_icon_path}")
                 return
                 
            image = Image.open(self.tray_icon_path)
            logging.info(f"Tray icon loaded from: {self.tray_icon_path}")
            
            menu = (
                pystray.MenuItem('Show NerazimNet', self.show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('Quit NerazimNet', self.quit_application)
            )
            self.tray_icon = pystray.Icon("nerazimnet", image, "NerazimNet", menu)
            
            threading.Thread(target=self._run_tray_icon, daemon=True).start()
            logging.info("System tray icon thread started.")
            
        except Exception as e:
            logging.error(f"Failed to create system tray icon: {e}", exc_info=True)

    def show_notification(self, title, message):
        """Fires an OS desktop notification (tray balloon / osascript / notify-send)."""
        from utils.notify import send_notification
        send_notification(title, message, tray_icon=getattr(self, 'tray_icon', None))

    def _run_tray_icon(self):
        """Target function for the pystray thread."""
        try:
            if self.tray_icon: self.tray_icon.run()
        except Exception as e:
             logging.error(f"Error in tray icon thread: {e}", exc_info=True)

    def show_window(self):
        """Brings the main window to the front."""
        try:
            self.after(0, self._show_window_on_main)
        except Exception as e:
             logging.warning(f"Error scheduling show_window: {e}")

    def _show_window_on_main(self):
        """Main-thread logic for showing the window."""
        try:
            if self.winfo_exists():
                self.deiconify()
                self.lift()
                self.focus_force()
        except Exception as e:
            logging.warning(f"Error in _show_window_on_main: {e}")

    def quit_application(self):
        """Stops the tray icon and schedules the app to close."""
        logging.info("Quit requested from tray icon.")
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception as e:
                logging.warning(f"Error stopping tray icon: {e}")
        self.after(0, self.on_closing, True) # force_quit=True

    def on_closing(self, force_quit=False):
        """Handles the WM_DELETE_WINDOW protocol (close button 'X')."""
        if self.is_shutting_down:
             return
             
        if force_quit:
            self.is_shutting_down = True
            logging.info("App closing (force_quit=True).")
            
            if self.is_unlocked:
                logging.info("Stopping backend services...")
                try:
                    # frpc daemons are owned by the OS service and keep
                    # running — tunnels survive app exit by design.
                    self.tunnel_manager.shutdown()
                    self.syncthing_manager.stop()
                    logging.info("Backend services stopped.")
                except Exception as e:
                    logging.error(f"Error stopping services: {e}", exc_info=True)
            
            if self.tray_icon and self.tray_icon.visible:
                try: self.tray_icon.stop()
                except Exception: pass 
            self.tray_icon = None
            
            logging.info("Scheduling app destroy.")
            self.after(200, self.destroy)
        elif self.tray_icon is not None:
            logging.info("Hiding to system tray via close button.")
            self.minimized_to_tray = True
            self.withdraw()
        else:
            # No tray icon (macOS, or tray backend unavailable) — close for real
            self.on_closing(force_quit=True)
            
    def handle_first_run(self):
        """Handles first run by showing password setup UI directly."""
        logging.info("Handling first run: showing setup UI.")
        if hasattr(self, '_initial_frame') and self._initial_frame and self._initial_frame.winfo_exists():
            for widget in self._initial_frame.winfo_children(): widget.destroy()
            self._initial_frame.destroy()
        self._initial_frame = ctk.CTkFrame(self, fg_color="transparent"); self._initial_frame.grid(row=0, column=0, sticky="nsew")
        self._initial_frame.grid_rowconfigure(0, weight=1); self._initial_frame.grid_columnconfigure(0, weight=1)
        center_frame = ctk.CTkFrame(self._initial_frame, corner_radius=10); center_frame.grid(row=0, column=0, sticky="", padx=30, pady=30)
        ctk.CTkLabel(center_frame, text="Welcome to NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk.CTkLabel(center_frame, text="Create a New Master Password:").pack(padx=30, pady=(10, 0))
        entry_frame1 = ctk.CTkFrame(center_frame, fg_color="transparent"); entry_frame1.pack(padx=30, pady=5)
        self.setup_entry1 = ctk.CTkEntry(entry_frame1, show="*", width=200); self.setup_entry1.pack(side="left")
        show_icon = self.btn_images.get("eye-show"); use_icons = bool(show_icon)
        toggle_btn1 = ctk.CTkButton(entry_frame1, image=show_icon if use_icons else None, text="👁️" if not use_icons else "", width=28, anchor="center", command=lambda: self._toggle_setup_password_visibility(self.setup_entry1, toggle_btn1)); toggle_btn1.pack(side="left", padx=(5, 0))
        ctk.CTkLabel(center_frame, text="Confirm Master Password:").pack(padx=30, pady=(10, 0))
        entry_frame2 = ctk.CTkFrame(center_frame, fg_color="transparent"); entry_frame2.pack(padx=30, pady=5)
        self.setup_entry2 = ctk.CTkEntry(entry_frame2, show="*", width=200); self.setup_entry2.pack(side="left")
        self.setup_entry2.bind("<Return>", self._on_setup_confirm)
        toggle_btn2 = ctk.CTkButton(entry_frame2, image=show_icon if use_icons else None, text="👁️" if not use_icons else "", width=28, anchor="center", command=lambda: self._toggle_setup_password_visibility(self.setup_entry2, toggle_btn2)); toggle_btn2.pack(side="left", padx=(5, 0))
        ctk.CTkLabel(center_frame, text="Allowed: A-Z a-z 0-9 Space !@#$%^&*()_+-=[]{}|;:,.<>?", font=("", 10), wraplength=250).pack(padx=30, pady=5)
        ctk.CTkButton(center_frame, text="Create Password", command=self._on_setup_confirm, width=230).pack(padx=30, pady=20)
        self.after(100, lambda: self._safe_focus(self.setup_entry1))

    def _toggle_setup_password_visibility(self, entry, button):
        """Toggles visibility for password entries in the setup UI."""
        if not entry or not button: return
        try:
            show_icon = self.btn_images.get("eye-show")
            hide_icon = self.btn_images.get("eye-hide")
            use_icons = bool(show_icon and hide_icon)

            if entry.cget("show") == "*":
                entry.configure(show="")
                button.configure(image=hide_icon if use_icons else None,
                                 text="🔒" if not use_icons else "")
            else:
                entry.configure(show="*")
                button.configure(image=show_icon if use_icons else None,
                                 text="👁️" if not use_icons else "")
        except Exception as e:
             logging.warning(f"Error toggling setup password visibility: {e}")

    def _on_setup_confirm(self, event=None):
        """Validates and processes the password setup."""
        if not self.setup_entry1 or not self.setup_entry2:
             logging.error("Setup password entries not found.")
             return

        password = self.setup_entry1.get()
        password2 = self.setup_entry2.get()
        
        allowed_chars = r"^[A-Za-z0-9 !@#$%^&*()_+\-=\[\]{}|;:,.<>?]*$"
        if password != password2:
            self.show_error("Setup Error", "Passwords do not match.")
            return
        if not password:
            self.show_error("Setup Error", "Password cannot be empty.")
            return
        if not re.fullmatch(allowed_chars, password):
            self.show_error("Setup Error", "Password contains invalid characters..."); return
            
        unlocked, recovery_key = self.config_manager.unlock_with_password(password)
        
        if unlocked and recovery_key:
            logging.info("First run setup complete via ConfigManager. Proceeding...")
            self.is_unlocked = True; self.setup_entry1 = None; self.setup_entry2 = None
            try: self.focus_set() 
            except Exception: pass
            if self._initial_frame and self._initial_frame.winfo_exists(): self._initial_frame.destroy()
            self._initial_frame = None; logging.debug("Initial setup frame destroyed.")
            self._show_loading_screen()
            self._start_backend_services_threaded(callback=lambda rk=recovery_key: self._on_loading_complete(rk)) 
        elif unlocked and not recovery_key:
             logging.error("ConfigManager unlocked but did not return recovery key on first run.")
             self.show_error("Setup Error", "Password set, but recovery key not generated.")
        else:
             logging.error("ConfigManager.unlock_with_password failed during first run setup.")
             self.show_error("Setup Error", "Failed to finalize password setup.")

    def _build_initial_ui(self):
        """Creates the initial password entry or loading UI directly in the App window."""
        logging.debug("Building initial UI...")
        is_first_run = not self.config_manager.is_configured()
        self._initial_frame = ctk.CTkFrame(self, fg_color="transparent"); self._initial_frame.grid(row=0, column=0, sticky="nsew")
        self._initial_frame.grid_rowconfigure(0, weight=1); self._initial_frame.grid_columnconfigure(0, weight=1)
        center_frame = ctk.CTkFrame(self._initial_frame, corner_radius=10); center_frame.grid(row=0, column=0, sticky="", padx=30, pady=30)
        if not is_first_run:
            ctk.CTkLabel(center_frame, text="NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
            ctk.CTkLabel(center_frame, text="Enter Master Password:").pack(padx=30, pady=(10, 0))
            entry_frame = ctk.CTkFrame(center_frame, fg_color="transparent"); entry_frame.pack(padx=30, pady=10)
            self.password_entry = ctk.CTkEntry(entry_frame, show="*", width=200); self.password_entry.pack(side="left")
            def on_unlock_return(event=None):
                if hasattr(self, 'password_entry') and self.password_entry: self.attempt_unlock(self.password_entry.get())
                return "break"
            self.password_entry.bind("<Return>", on_unlock_return)
            show_icon = self.btn_images.get("eye-show"); use_icons = bool(show_icon)
            toggle_btn1 = ctk.CTkButton(entry_frame, image=show_icon if use_icons else None, text="👁️" if not use_icons else "", width=28, anchor="center", command=lambda: self._toggle_initial_password_visibility()); toggle_btn1.pack(side="left", padx=(5, 0))
            button_frame = ctk.CTkFrame(center_frame, fg_color="transparent"); button_frame.pack(padx=30, pady=(10, 20))
            ctk.CTkButton(button_frame, text="Unlock", width=110, command=lambda: self.attempt_unlock(self.password_entry.get() if self.password_entry else "")).pack(side="left", padx=5)
            ctk.CTkButton(button_frame, text="Forgot Password?", fg_color="transparent", width=110, text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"], command=self.forgot_password).pack(side="left", padx=5)
        else:
            ctk.CTkLabel(center_frame, text="Welcome to NerazimNet!", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=30)
            ctk.CTkLabel(center_frame, text="Initial setup required.").pack(padx=30, pady=10)
            ctk.CTkButton(center_frame, text="Start Setup", command=self.handle_first_run).pack(padx=30, pady=20)
        logging.debug("Initial UI built.")

    def _toggle_initial_password_visibility(self):
        """Toggles visibility for the password entry in the initial UI."""
        entry = self.password_entry; button = None
        if entry and entry.master:
            for w in entry.master.winfo_children():
                if isinstance(w, ctk.CTkButton): button = w; break
        if not entry or not button: return
        try:
            show_icon = self.btn_images.get("eye-show"); hide_icon = self.btn_images.get("eye-hide"); use_icons = bool(show_icon and hide_icon)
            if entry.cget("show") == "*": entry.configure(show=""); button.configure(image=hide_icon if use_icons else None, text="🔒" if not use_icons else "")
            else: entry.configure(show="*"); button.configure(image=show_icon if use_icons else None, text="👁️" if not use_icons else "")
        except Exception as e: logging.warning(f"Error toggling initial password visibility: {e}")

    def _initial_check(self):
        """Performs initial checks (like first run) and sets focus."""
        logging.debug("Performing initial check...")
        is_first_run = not self.config_manager.is_configured()
        if is_first_run:
            setup_button = None
            if self._initial_frame:
                 try:
                     center_frame = self._initial_frame.winfo_children()[0]
                     for w in center_frame.winfo_children():
                          if isinstance(w, ctk.CTkButton) and "Setup" in w.cget("text"): setup_button = w; break
                 except Exception: pass
            if setup_button: self._safe_focus(setup_button)
        else:
            if hasattr(self, 'password_entry') and self.password_entry: self._safe_focus(self.password_entry)

    def _safe_focus(self, entry_widget):
        """Safely sets focus if the App window and widget still exist."""
        if hasattr(self, '_focus_after_id'): self._focus_after_id = None
        try:
            if self.winfo_exists() and entry_widget and entry_widget.winfo_exists():
                try: entry_widget.focus_set()
                except Exception as focus_e: logging.warning(f"Error during focus_set call: {focus_e}")
        except Exception as e: logging.error(f"Error checking widget for focus: {e}", exc_info=True)

    def attempt_unlock(self, password):
        """Attempts to unlock, shows loading screen on success."""
        logging.info("Attempting unlock...")
        if self.is_unlocked: return
        if self._focus_after_id:
            try: self.after_cancel(self._focus_after_id)
            except Exception: pass
            self._focus_after_id = None
        unlocked, message_or_recovery_key = self.config_manager.unlock_with_password(password)
        if unlocked:
            self.is_unlocked = True
            try: self.focus_set() 
            except Exception: pass
            if hasattr(self, '_initial_frame') and self._initial_frame and self._initial_frame.winfo_exists(): self._initial_frame.destroy()
            self._initial_frame = None; self.password_entry = None
            logging.debug("Initial UI frame destroyed.")
            self._show_loading_screen()
            self._start_backend_services_threaded(callback=lambda rk=message_or_recovery_key: self._on_loading_complete(rk)) 
        else:
            ErrorDialog(self, title="Unlock Failed", message=message_or_recovery_key or "Incorrect password.")
            if hasattr(self, 'password_entry') and self.password_entry:
                self.password_entry.delete(0, 'end')
                self._focus_after_id = self.after(50, lambda: self._safe_focus(self.password_entry))
                
    def _show_loading_screen(self):
        """Displays a loading screen UI."""
        logging.debug("Showing loading screen...")
        self._loading_frame = ctk.CTkFrame(self, fg_color="transparent"); self._loading_frame.grid(row=0, column=0, sticky="nsew")
        self._loading_frame.grid_rowconfigure(0, weight=1); self._loading_frame.grid_columnconfigure(0, weight=1)
        center_frame = ctk.CTkFrame(self._loading_frame, corner_radius=10); center_frame.grid(row=0, column=0, sticky="")
        ctk.CTkLabel(center_frame, text="NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk.CTkLabel(center_frame, text="Initializing services...").pack(padx=30, pady=10)
        progressbar = ctk.CTkProgressBar(center_frame, mode="indeterminate"); progressbar.pack(padx=30, pady=10, fill="x"); progressbar.start()
        ctk.CTkLabel(center_frame, text="Please wait...", text_color="gray60").pack(padx=30, pady=(0, 20))
        self.update(); self.update_idletasks()
        
    def _start_backend_services_threaded(self, callback=None):
        """Starts Syncthing and Tunnel Monitor in a separate thread."""
        logging.info("Starting backend services in a new thread...")
        def service_starter():
            logging.debug("Service starter thread begins.")
            try:
                logging.info("Attempting to start Syncthing...")
                self.syncthing_manager.start()
                logging.info("Syncthing started successfully.")
            except Exception as e:
                # Capture exception for the lambda to prevent NameError
                logging.critical(f"Critical error starting Syncthing: {e}", exc_info=True)
                self.after(0, lambda err=e: self.show_error(f"Syncthing Startup Failed", f"Could not start Syncthing:\n{err}"))

            logging.info("Attempting to start Tunnel Manager monitor...")
            try:
                self.tunnel_manager.start_all_tunnels()
                logging.info("Initial tunnel start sequence triggered.")
            except Exception as e:
                logging.error(f"Error starting initial tunnels: {e}", exc_info=True)

            logging.debug("Service starter thread finished.")
            if callback:
                logging.debug("Scheduling loading complete callback.")
                self.after(0, callback) 

        thread = threading.Thread(target=service_starter, daemon=True); thread.start()
        
    def _on_loading_complete(self, recovery_key=None):
        """Callback run after services are initialized. Resizes window and builds UI."""
        logging.info("Backend services initialized. Resizing window and building main UI.")

        logging.debug("Resizing window to main size...")
        try:
            self.geometry(self._main_size)
            self.resizable(True, True)
            self.minsize(self._main_minsize[0], self._main_minsize[1])
            self.update_idletasks() 
            self._center_window()
            logging.debug("Window resized and centered.")
        except Exception as e:
             logging.error(f"Error resizing/centering window in _on_loading_complete: {e}")

        if hasattr(self, '_loading_frame') and self._loading_frame and self._loading_frame.winfo_exists():
            self._loading_frame.destroy()
        self._loading_frame = None
        logging.debug("Loading frame destroyed.")

        self._build_main_ui()
        self.update_idletasks()
        self.show_frame("DashboardView") # Show Tunnels (Dashboard) view first
        logging.info("Main UI is now visible.")
        
        self.attributes("-topmost", False)
        logging.debug("Window always-on-top disabled.")

        if recovery_key:
             self.after(200, lambda: self.view_recovery_key(recovery_key))
                 
    def _center_window(self):
        """Centers the window on the screen."""
        try:
            self.update_idletasks() 
            width = self.winfo_width(); height = self.winfo_height()
            screen_width = self.winfo_screenwidth(); screen_height = self.winfo_screenheight()
            x = max(0, (screen_width - width) // 2); y = max(0, (screen_height - height) // 2)
            self.geometry(f"+{x}+{y}")
        except Exception as e: logging.warning(f"Error centering window: {e}")

    def forgot_password(self):
        """Handles the 'Forgot Password' button click."""
        logging.info("'Forgot Password' clicked.")
        self.show_error("Password Recovery", "Password recovery using key not yet implemented.")
        
    def change_master_password(self):
        """Shows placeholder as ChangePasswordDialog is missing."""
        logging.debug("Change master password requested.")
        self.show_error("Not Implemented", "Changing the master password is not yet implemented in this version.")

    def view_recovery_key(self, key=None):
        """Shows the recovery key (if provided) or retrieves and shows it."""
        logging.debug("View recovery key requested.")
        recovery_key = key
        if not recovery_key:
             if not self.is_unlocked: self.show_error("Error", "Must be unlocked."); return
             recovery_key = self.config_manager.get_recovery_key()
        if recovery_key: RecoveryKeyDialog(self, recovery_key=recovery_key, title="Recovery Key")
        else: self.show_error("Error", "Could not retrieve recovery key.")

    def show_frame(self, page_name: str):
        """Raises the specified frame to the top and makes it visible."""
        if not self.is_unlocked or not self.frames:
             logging.warning(f"Cannot show frame {page_name}, not unlocked or UI not built.")
             return

        logging.info(f"Switching to view: {page_name}")
        frame_to_show = self.frames.get(page_name)
        if not frame_to_show:
             logging.error(f"Cannot show frame: View '{page_name}' not found."); return

        for name, frame in self.frames.items():
            if frame is not frame_to_show and frame.winfo_ismapped():
                 if hasattr(frame, 'on_leave'):
                      try: frame.on_leave()
                      except Exception as e: logging.error(f"Error calling on_leave for {name}: {e}")
                 frame.grid_remove() 

        if frame_to_show:
            if hasattr(frame_to_show, 'on_enter'):
                 try: frame_to_show.on_enter()
                 except Exception as e: logging.error(f"Error calling on_enter for {page_name}: {e}", exc_info=True)
            frame_to_show.grid(row=0, column=0, padx=0, pady=0, sticky="nsew") # Make visible
            frame_to_show.tkraise() # Bring to front
            if self.sidebar_frame:
                self.sidebar_frame.configure(
                    fg_color=self.view_sidebar_colors.get(page_name,
                                                          self.view_sidebar_colors["DashboardView"]))

    def refresh_dashboard(self):
        """Refreshes the dashboard view if it exists."""
        logging.debug("Scheduling Tunnels (Dashboard) refresh.")
        if ("DashboardView" in self.frames 
            and self.frames["DashboardView"] 
            and self.frames["DashboardView"].winfo_exists()):
            self.after(0, self.frames["DashboardView"].sync_tunnel_list)
        else: 
            logging.debug("Skipping Tunnels refresh (frame not created/destroyed).")
             
    def show_error(self, title: str, message: str = None):
        """Displays a modal error dialog."""
        if message is None: message = title
        logging.warning(f"Showing Error Dialog: Title='{title}', Message='{message}'")
        if self.winfo_exists():
             self.after(0, lambda: ErrorDialog(self, title=title, message=message))
        
    def set_appearance_mode(self, mode: str):
        """Sets the app's appearance mode (Light/Dark/System)."""
        logging.info(f"Setting appearance mode to: {mode}")
        ctk.set_appearance_mode(mode.lower())
        
    def provision_server(self, server: dict, admin_pass: str = "", certbot_email: str = ""): # Made args optional
        """Starts the provisioning process for a server in a new thread."""
        logging.info(f"Starting provisioning for server: {server.get('name')}")
        pub_key = self.get_automation_public_key()
        if not pub_key:
            # No key configured yet — generate one automatically
            logging.info("No automation key found; generating a new SSH key pair.")
            gen_result = self.generate_ssh_key_pair()
            if gen_result:
                self.save_automation_credentials(gen_result[0], gen_result[1])
                pub_key = self.get_automation_public_key()
        if not pub_key: self.show_error("Provisioning Failed", "Could not read public SSH key. Configure or generate keys in Settings."); return
        frp_token = self.get_or_create_frp_token()
        if not frp_token: self.show_error("Provisioning Failed", "Could not create an FRP auth token."); return

        prov_dialog = ProvisionDialog(self, server_name=server.get('name', server['ip_address']), server_ip=server['ip_address'])
        result = prov_dialog.get_input() # Get user/pass/email
        if not result: logging.info("Provisioning cancelled by user."); return

        log_dialog = ProvisioningLogDialog(self, server_name=server.get('name', server['ip_address']))

        def run_provisioning():
            try:
                provisioner = ServerProvisioner(host=server['ip_address'], admin_user=result['user'], admin_password=result['password'], automation_public_key=pub_key, frp_token=frp_token, frp_version=get_frp_version(), certbot_email=result['email'])
                success, logs = provisioner.provision_vps()
                self.after(0, log_dialog.update_log, logs)
                if success:
                    self.after(0, log_dialog.complete, True); server['is_provisioned'] = True; server['admin_user'] = result['user']; server['certbot_email'] = result['email']; self.save_object(server['id'], server)
                    if "ServersView" in self.frames and self.frames["ServersView"].winfo_exists(): self.after(100, self.frames["ServersView"].load_servers)
                else: self.after(0, log_dialog.complete, False)
            except Exception as e:
                error_msg = f"\n\n--- CRITICAL ERROR ---\n{e}"; logging.error(f"Critical provisioning error: {e}", exc_info=True)
                self.after(0, log_dialog.update_log, [error_msg]); self.after(0, log_dialog.complete, False)

        threading.Thread(target=run_provisioning, daemon=True).start()

    def sync_server_routes(self, server_id: str):
        """
        Pushes the current tunnel configs for a server to its Nginx/Certbot setup
        over administrative SSH. Runs in a background thread with a log dialog.
        """
        if not self.is_unlocked: return
        server = self.get_object_by_id(server_id)
        if not server:
            self.show_error("Route Sync Failed", "Could not find the server configuration.")
            return

        server_label = server.get('name', server.get('ip_address', server_id))
        creds = self.get_automation_credentials() or {}
        key_path = creds.get('ssh_private_key_path')
        key_ok = bool(key_path) and os.path.exists(key_path)
        admin_user = server.get('admin_user')
        admin_password = ""
        certbot_email = server.get('certbot_email', '')

        # Without a stored admin user + working key we must ask for one-off creds.
        if not admin_user or not key_ok:
            logging.info(f"Route sync for {server_label} requires admin credentials (key auth unavailable).")
            prov_dialog = ProvisionDialog(self, server_name=server_label, server_ip=server['ip_address'])
            result = prov_dialog.get_input()
            if not result:
                logging.info("Route sync cancelled by user.")
                return
            admin_user = result['user']
            admin_password = result['password']
            certbot_email = certbot_email or result['email']

        tunnels = [t for t in self.get_tunnels() if t.get('server_id') == server_id]
        log_dialog = ProvisioningLogDialog(self, server_name=server_label, action="Route Sync")

        def run_sync():
            try:
                provisioner = ServerProvisioner(
                    host=server['ip_address'], admin_user=admin_user,
                    admin_password=admin_password,
                    admin_key_path=key_path if key_ok else None,
                    frp_version=get_frp_version(), certbot_email=certbot_email
                )
                success, logs = provisioner.sync_server_routes(tunnels)
                self.after(0, log_dialog.update_log, logs)
                self.after(0, log_dialog.complete, success)
                if success:
                    logging.info(f"Route sync completed for {server_label}.")
                else:
                    logging.error(f"Route sync failed for {server_label}.")
            except Exception as e:
                error_msg = f"\n\n--- CRITICAL ERROR ---\n{e}"
                logging.error(f"Critical route sync error: {e}", exc_info=True)
                self.after(0, log_dialog.update_log, [error_msg])
                self.after(0, log_dialog.complete, False)

        threading.Thread(target=run_sync, daemon=True).start()

    # --- Passthrough Methods ---
    def get_object_by_id(self, obj_id: str): return self.config_manager.get_object_by_id(obj_id) if self.is_unlocked else None
    def get_tunnels(self): return self.config_manager.get_tunnels() if self.is_unlocked else []
    def get_servers(self): return self.config_manager.get_servers() if self.is_unlocked else []
    def get_clients(self): return self.config_manager.get_clients() if self.is_unlocked else []
    def get_server_name(self, server_id: str): return self.config_manager.get_server_name(server_id) if self.is_unlocked else "Unknown"
    def get_client_name(self, client_id: str): return self.config_manager.get_client_name(client_id) if self.is_unlocked else client_id[:8] if client_id else "None"
    def get_automation_credentials(self): return self.config_manager.get_automation_credentials() if self.is_unlocked else None
    def get_automation_public_key(self) -> str | None:
        creds = self.get_automation_credentials(); path = creds.get('ssh_public_key_path') if creds else None
        if not path or not os.path.exists(path): return None
        try:
             with open(path, 'r', encoding='utf-8') as f: return f.read().strip()
        except Exception: return None
    def save_object(self, obj_id: str, data: dict): self.config_manager.update_object(obj_id, data)
    def add_object(self, obj_type: str, data: dict) -> str: return self.config_manager.add_object(obj_type, data)
    def delete_object(self, obj_id: str): self.config_manager.delete_object(obj_id)
    def save_automation_credentials(self, private_key_path: str, public_key_path: str): self.config_manager.save_or_update_automation_credentials(private_key_path, public_key_path)
    def get_or_create_frp_token(self) -> str | None: return self.config_manager.get_or_create_frp_token() if self.is_unlocked else None
    def generate_ssh_key_pair(self) -> tuple[str, str] | None:
        try: return self.crypto_manager.generate_ssh_key_pair()
        except Exception as e:
            logging.error(f"SSH key generation failed: {e}", exc_info=True); return None
    def get_my_device_id(self) -> str | None: return self.syncthing_manager.my_device_id
    def get_my_device_name(self) -> str: return os.getenv('COMPUTERNAME') or platform.node() or 'My Device'
    def get_syncthing_devices(self) -> list: return self.syncthing_manager.get_devices()
    def generate_syncthing_invite(self) -> str | None: return self.syncthing_manager.generate_invite()
    def accept_syncthing_invite(self, invite_string: str) -> bool: return self.syncthing_manager.accept_invite(invite_string)
    def remove_syncthing_device(self, device_id: str): self.syncthing_manager.remove_device(device_id)

    def repair_firewall_access(self):
        """Recreates Windows Firewall allow rules for the bundled exes.
        Fixes cases where the user clicked 'Deny' on the initial prompt."""
        dialog = ConfirmationDialog(
            self, title="Fix Firewall Access?",
            message=("This recreates the Windows Firewall rules that let "
                     "NerazimNet sync with other devices.\n\nWindows will "
                     "show a one-time administrator prompt."))
        if not dialog.get_input():
            return
        paths = {self.syncthing_manager.syncthing_exe_path,
                 self.tunnel_manager.frpc_command[0]}
        ok, message = reset_firewall_rules(sorted(paths))
        if ok:
            ErrorDialog(self, title="Firewall Access", message=message)
        else:
            ErrorDialog(self, title="Firewall Fix Failed", message=message)
    def start_tunnel(self, tunnel_id: str) -> tuple[bool, str]: return self.tunnel_manager.start_tunnel(tunnel_id)
    def stop_tunnel(self, tunnel_id: str): self.tunnel_manager.stop_tunnel(tunnel_id)
    def get_tunnel_statuses(self) -> dict: return self.tunnel_manager.get_tunnel_statuses()
    def get_tunnel_log(self, tunnel_id: str) -> str: return self.tunnel_manager.get_tunnel_log(tunnel_id)
    def get_clients_for_dropdown(self) -> tuple[dict, list]:
        client_map = {}; client_names = []
        my_id = self.get_my_device_id(); my_name = f"{self.get_my_device_name()} (This Device)"
        if my_id: client_map[my_name] = my_id; client_names.append(my_name)
        for client in self.get_clients():
            cid = client.get('syncthing_id'); cname = client.get('name') or f"Unknown ({cid[:7]}...)" if cid else "Invalid Client"
            if cid:
                display_name = cname; count = 1
                while display_name in client_map: count += 1; display_name = f"{cname} ({count})"
                client_map[display_name] = cid; client_names.append(display_name)
        client_names = sorted(client_names, key=lambda x: (x != my_name, x))
        return client_map, client_names
    def get_debug_info(self) -> dict:
        info = { "app": {"is_unlocked": self.is_unlocked, "is_shutting_down": self.is_shutting_down, "syncthing_id_ready": self.syncthing_id_ready.is_set()}, "syncthing": {"is_running": self.syncthing_manager.is_running, "my_device_id": self.syncthing_manager.my_device_id, "api_client": bool(self.syncthing_manager.api_client), "exe_path": self.syncthing_manager.syncthing_exe_path, "sync_folder_path": self.syncthing_manager.sync_folder_path}, "tunnels": {"frp_daemons": {sid: {'admin_port': d.get('admin_port'), 'api_up': d.get('api_up')} for sid, d in self.tunnel_manager.frp_daemons.items()}, "desired_tunnels": list(self.tunnel_manager.desired_tunnels), "error_messages": self.tunnel_manager.tunnel_error_messages, "log_keys": list(self.tunnel_manager.tunnel_logs.keys())}, "config": {"sync_path": self.config_manager.sync_path, "credentials_loaded": bool(self.config_manager._credentials), "object_count": len(self.config_manager._in_memory_state), "index_count": len(self.config_manager._file_index)} }
        return info
        
    def get_all_objects_for_debug(self):
        if not self.is_unlocked: return {}
        return self.config_manager.get_all_objects_for_debug()
    def get_history_file_index(self):
        if not self.is_unlocked: return []
        return self.config_manager.get_history_file_index()
    def get_file_version_history(self, file_id: str):
        if not self.is_unlocked: return []
        return self.config_manager.get_file_version_history(file_id)
    def get_file_content_at_version(self, file_id: str, timestamp):
        if not self.is_unlocked: return "[Unlock Required]"
        return self.config_manager.get_file_content_at_version(file_id, timestamp)
        
    def browse_for_file(self, title="Select File"):
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(title=title, parent=self) 
        return filepath if filepath else None

    def remove_client(self, client_id: str):
        """Removes a client device after confirmation."""
        logging.info(f"Remove client requested: {client_id}")
        if not self.is_unlocked: return
        client_name = self.get_client_name(client_id)
        dialog = ConfirmationDialog(self, title="Remove Device?", message=f"Remove device '{client_name}'?")
        if dialog.get_input():
            try:
                self.remove_syncthing_device(client_id)
                client_to_delete = None
                for obj_id, obj_data in self.config_manager.get_all_objects_for_debug().items():
                    if obj_data.get('type') == 'client' and obj_data.get('syncthing_id') == client_id:
                        client_to_delete = obj_id; break
                if client_to_delete: self.delete_object(client_to_delete)
                if "SettingsView" in self.frames and self.frames["SettingsView"].winfo_exists():
                    self.after(50, self.frames["SettingsView"]._load_devices_data) 
            except Exception as e:
                logging.error(f"Failed to remove device {client_id}: {e}", exc_info=True)
                self.show_error("Remove Failed", f"Could not remove device:\n{e}")
        else: logging.info(f"Remove device {client_id} cancelled.")

    # --- Added missing tunnel/server actions ---
    def start_all_tunnels(self):
        logging.info("Start All tunnels requested from UI.")
        if not self.is_unlocked: return
        self.tunnel_manager.start_all_tunnels()

    def stop_all_tunnels(self):
        logging.info("Stop All tunnels requested from UI.")
        if not self.is_unlocked: return
        dialog = ConfirmationDialog(self, title="Stop All Tunnels?", message="Stop all active tunnels managed by this device?")
        if dialog.get_input(): self.tunnel_manager.stop_all_tunnels()
        else: logging.info("Stop All tunnels cancelled.")

    def view_tunnel_log(self, tunnel_id: str):
        logging.info(f"View log requested for tunnel: {tunnel_id}")
        if not self.is_unlocked: return
        tunnel = self.get_object_by_id(tunnel_id)
        tunnel_name = tunnel.get('hostname', tunnel_id) if tunnel else tunnel_id
        log_content = self.get_tunnel_log(tunnel_id)
        LogViewerDialog(self, log_content=log_content, title=f"Logs: {tunnel_name}")

    def edit_tunnel(self, tunnel_id: str):
        logging.info(f"Edit tunnel requested: {tunnel_id}")
        if not self.is_unlocked: return
        initial_data = self.get_object_by_id(tunnel_id)
        if not initial_data: self.show_error("Error", f"Could not find tunnel: {tunnel_id}"); return
        dialog = TunnelDialog(self, controller=self, title="Edit Tunnel", initial_data=initial_data)
        result = dialog.get_input()
        if result:
            try:
                self.save_object(tunnel_id, result); self.refresh_dashboard()
                # Sync the new server, and the old one too if the tunnel moved
                server_ids = {initial_data.get('server_id'), result.get('server_id')} - {None}
                for sid in server_ids: self.sync_server_routes(sid)
            except Exception as e: self.show_error("Save Failed", f"Could not update tunnel:\n{e}")
        else: logging.info(f"Edit tunnel {tunnel_id} cancelled.")

    def delete_tunnel(self, tunnel_id: str):
        logging.info(f"Delete tunnel requested: {tunnel_id}")
        if not self.is_unlocked: return
        tunnel = self.get_object_by_id(tunnel_id); tunnel_name = tunnel.get('hostname', tunnel_id) if tunnel else tunnel_id
        dialog = ConfirmationDialog(self, title="Delete Tunnel?", message=f"Delete tunnel '{tunnel_name}'?")
        if dialog.get_input():
            try:
                statuses = self.get_tunnel_statuses()
                if statuses.get(tunnel_id, {}).get('status') == 'running': self.stop_tunnel(tunnel_id)
                server_id = tunnel.get('server_id') if tunnel else None
                self.delete_object(tunnel_id); self.refresh_dashboard()
                if server_id: self.sync_server_routes(server_id) # Cleans up the stale Nginx route
            except Exception as e: self.show_error("Delete Failed", f"Could not delete tunnel:\n{e}")
        else: logging.info(f"Delete tunnel {tunnel_id} cancelled.")

    def edit_server(self, server_id: str):
        logging.info(f"Edit server requested: {server_id}")
        if not self.is_unlocked: return
        initial_data = self.get_object_by_id(server_id)
        if not initial_data: self.show_error("Error", f"Could not find server: {server_id}"); return
        
        dialog = ServerDialog(self, controller=self, title="Edit Server", initial_data=initial_data)
        result = dialog.get_input()
        if result:
            try:
                self.save_object(server_id, result)
                if "ServersView" in self.frames and self.frames["ServersView"].winfo_exists(): self.after(50, self.frames["ServersView"].load_servers)
            except Exception as e: self.show_error("Save Failed", f"Could not update server:\n{e}")
        else: logging.info(f"Edit server {server_id} cancelled.")

    def delete_server(self, server_id: str):
        logging.info(f"Delete server requested: {server_id}")
        if not self.is_unlocked: return
        server = self.get_object_by_id(server_id); server_name = server.get('name', server_id) if server else server_id
        tunnels_using_server = [t for t in self.get_tunnels() if t.get('server_id') == server_id]
        if tunnels_using_server:
            tunnel_names = [t.get('hostname', t['id']) for t in tunnels_using_server]
            self.show_error("Cannot Delete Server", f"Server '{server_name}' is used by tunnels:\n- {', '.join(tunnel_names)}\nDelete/reassign tunnels first.")
            return
        dialog = ConfirmationDialog(self, title="Delete Server?", message=f"Delete server '{server_name}'?")
        if dialog.get_input():
            try:
                self.delete_object(server_id)
                if "ServersView" in self.frames and self.frames["ServersView"].winfo_exists(): self.after(50, self.frames["ServersView"].load_servers)
            except Exception as e: self.show_error("Delete Failed", f"Could not delete server:\n{e}")
        else: logging.info(f"Delete server {server_id} cancelled.")


    def add_new_tunnel(self):
        """Shows the TunnelDialog to add a new tunnel."""
        logging.info("Add new tunnel requested.")
        if not self.is_unlocked: return 

        dialog = TunnelDialog(self, controller=self, title="Add New Tunnel")
        result = dialog.get_input() 

        if result:
            try:
                new_id = self.add_object("tunnel", result)
                logging.info(f"New tunnel added with ID: {new_id}")
                self.refresh_dashboard()
                if result.get('server_id'): self.sync_server_routes(result['server_id'])
            except Exception as e:
                logging.error(f"Failed to save new tunnel: {e}", exc_info=True)
                self.show_error("Save Failed", f"Could not save the new tunnel:\n{e}")
        else:
            logging.info("Add new tunnel cancelled.")

    def add_new_server(self):
        """Shows the ServerDialog to add a new server."""
        logging.info("Add new server requested.")
        if not self.is_unlocked: return

        dialog = ServerDialog(self, controller=self, title="Add New Server", initial_data=None)
        result = dialog.get_input()

        if result:
            try:
                new_id = self.add_object("server", result)
                logging.info(f"New server added with ID: {new_id}")
                if "ServersView" in self.frames and self.frames["ServersView"].winfo_exists():
                    self.after(50, self.frames["ServersView"].load_servers)
            except Exception as e:
                logging.error(f"Failed to save new server: {e}", exc_info=True)
                self.show_error("Save Failed", f"Could not save the new server:\n{e}")
        else:
            logging.info("Add new server cancelled.")

    def add_new_device(self):
        """Shows the Syncthing Invite dialog."""
        logging.info("Add new device requested.")
        if not self.is_unlocked: return

        invite_string = self.generate_syncthing_invite()
        if invite_string:
            InviteDialog(self, invite_string=invite_string)
        else:
            self.show_error("Error Generating Invite",
                            "Could not generate Syncthing invite.\n"
                            "Please ensure Syncthing is running and initialized (check Debug view).")
            