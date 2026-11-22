# Full app.py

import customtkinter as ctk
import os
import logging
import threading
import sys
import atexit
import time
import zipfile
import pystray # For tray icon
import platform
from PIL import Image
from tkinter import PhotoImage
import re # Added for password validation in handle_first_run
import secrets

# --- Controllers ---
from controllers.config_manager import ConfigManager
from controllers.syncthing_manager import SyncthingManager
from controllers.tunnel_manager import TunnelManager
from utils.crypto import CryptoManager
from utils.version import get_version, get_frp_version
from utils.staging import reset_firewall_rules
from controllers.server_provisioner import ServerProvisioner
from utils.paths import get_app_data_dir

# --- Views ---
from views.tree_style import ctk_label, status_text_color, tk_color
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
    LogViewerDialog, ServerDialog, TunnelDialog, ProvisioningLogDialog,
    PasswordChangeDialog, RecoveryDialog, _validate_new_password,
    AcceptInviteDialog, WorkingDialog
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
        # Toolbar (Add Tunnel + filter + summary + Start/Stop ~850px)
        # plus sidebar makes anything under ~1040px clip controls.
        self._main_minsize = (1040, 560)
        
        # --- NEW: Sidebar Sizes ---
        self.sidebar_width_expanded = 160
        self.sidebar_width_collapsed = 60 # Icon-only width

        # --- Set Initial Geometry & State ---
        self.geometry(self._initial_size)
        self.resizable(False, False)
        self.minsize(0, 0)

        self.attributes("-topmost", True)
        logging.debug("Window set to always-on-top initially.")

        # --- Window Behavior Setup ---
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._backend_stopped = False
        self._install_shutdown_hooks()
        self.minimized_to_tray = False

        # --- Lock / auto-lock state ---
        self.is_locked = False
        self._unlock_failures = 0
        self._unlock_lockout_until = 0.0
        self._auto_lock_minutes = 0
        self._last_activity = time.monotonic()
        self._auto_lock_job = None
        self._activity_bound = False
        self._config_reload_job = None
        self._config_reload_running = False

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
                    # CTkImage requires light/dark sources of equal size
                    if dark_img.size != img.size:
                        dark_img = dark_img.resize(img.size, Image.LANCZOS)
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
                    if dark_img.size != light_img.size:
                        dark_img = dark_img.resize(light_img.size, Image.LANCZOS)
                    btn_images[name] = ctk.CTkImage(light_image=light_img,
                                                  dark_image=dark_img,
                                                  size=light_img.size)
                except Exception as e:
                    logging.warning(f"Failed to load button image '{filename}': {e}")
        self.btn_images = btn_images

        return images

    def _create_sidebar(self, width: int):
        """Creates and populates the sidebar frame."""
        # --- Use passed-in width ---
        sidebar = ctk.CTkFrame(self, width=width, corner_radius=0)
        sidebar.pack_propagate(False)
        sidebar.grid_propagate(False)
        
        # --- REMOVED Logo ---

        # --- NEW Toggle Button (at the top) ---
        menu_image = self.images.get("menu")
        self.toggle_button = ctk.CTkButton(
            sidebar,
            text="Collapse",
            image=menu_image,
            anchor="w",
            corner_radius=5,
            fg_color="transparent",
            hover_color=("gray75", "gray25"),
            text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"],
            command=self._toggle_sidebar
        )
        self.toggle_button.pack(fill="x", padx=10, pady=10)

        # Navigation buttons
        nav_buttons = [
            ("DashboardView", "Tunnels", "dashboard"),
            ("ServersView", "Servers", "servers"),
            ("SettingsView", "Settings", "settings"),
            ("HistoryView", "History", "history"),
            ("DebugView", "Debug", "debug")
        ]

        # --- Clear nav_buttons cache and rebuild ---
        self.nav_buttons = []
        for view_name, text, image_key in nav_buttons:
            btn = ctk.CTkButton(
                sidebar, text=text, image=self.images.get(image_key),
                anchor="w", corner_radius=5,
                fg_color="transparent", hover_color=("gray75", "gray25"),
                text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"],
                command=lambda v=view_name: self.show_frame(v)
            )
            btn.view_name = view_name
            btn.image_key = image_key
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
                pystray.MenuItem('Start All Tunnels', lambda icon, item: self.after(0, self._tray_start_all)),
                pystray.MenuItem('Stop All Tunnels', lambda icon, item: self.after(0, self._tray_stop_all)),
                pystray.MenuItem('Lock', lambda icon, item: self.after(0, self.lock)),
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
            # Backend cleanup can take seconds — hide the window first so the
            # app feels gone instead of frozen on screen.
            try: self.withdraw()
            except Exception: pass

            # frpc daemons are owned by the OS service and keep running —
            # tunnels survive app exit by design.
            self._graceful_shutdown()
            
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
            

    def _graceful_shutdown(self):
        """Synchronous backend cleanup — safe when the Tk loop is gone."""
        if self._backend_stopped:
            return
        self._backend_stopped = True
        if not self.is_unlocked:
            return
        logging.info("Stopping backend services...")
        try:
            self.tunnel_manager.shutdown()
            self.syncthing_manager.stop()
            logging.info("Backend services stopped.")
        except Exception as e:
            logging.error(f"Error stopping services: {e}", exc_info=True)

    def _install_shutdown_hooks(self):
        """Stop backends on OS shutdown / signals, not just the Quit path.

        Windows: the OS sends WM_QUERYENDSESSION to top-level windows during
        logoff/shutdown; Tk's default window proc simply lets TerminateProcess
        happen, so we subclass it to run a synchronous cleanup first.
        POSIX: a SIGTERM/SIGHUP handler schedules the normal quit path.
        atexit covers every other interpreter-exit path.
        """
        atexit.register(self._graceful_shutdown)

        if sys.platform != 'win32':
            try:
                import signal
                for sig in (signal.SIGTERM, signal.SIGHUP):
                    signal.signal(sig, self._on_posix_sigterm)
            except (ValueError, OSError) as e:
                logging.debug(f"Could not install signal handlers: {e}")
            return

        # Windows: subclass the top-level window proc.
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
            self._orig_wndproc = None
            # Declare 64-bit signatures — without these ctypes truncates
            # pointers to 32-bit ints and CallWindowProcW crashes.
            set_proc = getattr(self._user32, 'SetWindowLongPtrW', None) or \
                self._user32.SetWindowLongW  # 32-bit Windows fallback
            set_proc.restype = ctypes.c_void_p
            set_proc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            self._user32.CallWindowProcW.restype = ctypes.c_int64
            self._user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                ctypes.c_uint64, ctypes.c_int64]
            hwnd = ctypes.c_void_p(int(self.wm_frame(), 16))
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int64, ctypes.c_void_p, ctypes.c_uint,
                ctypes.c_uint64, ctypes.c_int64)
            self._wndproc_ref = WNDPROC(self._shutdown_wndproc)
            prev = set_proc(hwnd, -4,  # GWLP_WNDPROC
                            ctypes.cast(self._wndproc_ref, ctypes.c_void_p))
            if prev:
                self._orig_wndproc = ctypes.c_void_p(prev)
                logging.info("Installed Windows shutdown hook (WM_QUERYENDSESSION).")
            else:
                logging.warning("SetWindowLongPtrW failed; shutdown hook inactive.")
        except Exception as e:
            logging.warning(f"Could not install Windows shutdown hook: {e}")

    def _on_posix_sigterm(self, signum, frame):
        logging.info(f"Received signal {signum}; shutting down.")
        try:
            self.after(0, self.on_closing, True)
        except Exception:
            self._graceful_shutdown()

    def _shutdown_wndproc(self, hwnd, msg, wparam, lparam):
        # Never let an exception escape a ctypes callback — it just spams
        # "Exception ignored" per message and can crash the mainloop.
        try:
            WM_QUERYENDSESSION = 0x0011
            if msg == WM_QUERYENDSESSION:
                logging.info("System shutdown detected — stopping backend services.")
                self._graceful_shutdown()
                return 1  # TRUE: allow the session to end
            return self._user32.CallWindowProcW(
                self._orig_wndproc, hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def _tray_start_all(self):
        if self.is_unlocked and not self.is_shutting_down:
            try:
                self.tunnel_manager.start_all_tunnels()
            except Exception as e:
                logging.error(f"Tray start-all failed: {e}")

    def _tray_stop_all(self):
        if self.is_unlocked and not self.is_shutting_down:
            try:
                self.tunnel_manager.stop_all_tunnels()
            except Exception as e:
                logging.error(f"Tray stop-all failed: {e}")

    def lock(self):
        """Screen-lock: tear down the main UI and re-prompt for the password.

        Backends (Syncthing, tunnel daemon) keep running — this gates UI
        access, it does not re-encrypt or stop services. If the window is
        hidden in the tray the lock screen appears on the next Show.
        """
        if not self.is_unlocked or self.is_locked:
            return
        if self.grab_current():
            logging.info("Lock deferred — a modal dialog is open.")
            return
        logging.info("Locking application UI.")
        self.is_locked = True
        for attr in ('sidebar_frame', 'content_frame'):
            frame = getattr(self, attr, None)
            if frame is not None and frame.winfo_exists():
                frame.destroy()
        self.sidebar_frame = None
        self.content_frame = None
        self.frames = {}
        self.grid_columnconfigure(1, weight=0)
        self._build_initial_ui()
        self._initial_check()

    def get_setting(self, key, default=None):
        if not self.is_unlocked:
            return default
        return self.config_manager.get_setting(key, default)

    def set_setting(self, key, value):
        if self.is_unlocked:
            try:
                self.config_manager.set_setting(key, value)
            except Exception as e:
                logging.error(f"Failed to persist setting {key}: {e}")
        if key == 'auto_lock_minutes':
            self._auto_lock_minutes = int(value or 0)

    def _record_activity(self, _event=None):
        self._last_activity = time.monotonic()

    def _start_auto_lock_timer(self):
        self._last_activity = time.monotonic()
        # bind_all(add="+") stacks — bind once or every lock→unlock cycle
        # would fire _record_activity an extra time per event.
        if not self._activity_bound:
            self.bind_all("<KeyPress>", self._record_activity, add="+")
            self.bind_all("<Button>", self._record_activity, add="+")
            self._activity_bound = True
        if self._auto_lock_job is None:
            self._auto_lock_job = self.after(30_000, self._auto_lock_tick)

    def _auto_lock_tick(self):
        if self.is_shutting_down:
            self._auto_lock_job = None
            return
        if (self.is_unlocked and not self.is_locked
                and self._auto_lock_minutes > 0
                and not self.grab_current()
                and time.monotonic() - self._last_activity >= self._auto_lock_minutes * 60):
            self.lock()
        self._auto_lock_job = self.after(30_000, self._auto_lock_tick)

    def copy_tunnel_address(self, tunnel_id: str):
        """Copies the tunnel's public address (hostname:remote_port) to the clipboard."""
        tunnel = self.get_object_by_id(tunnel_id)
        if not tunnel:
            return
        host = tunnel.get('hostname')
        if not host:
            server = self.get_object_by_id(tunnel.get('server_id'))
            host = (server or {}).get('ip_address', '')
        port = tunnel.get('remote_port')
        address = f"{host}:{port}" if host and port else host
        if not address:
            return
        self.clipboard_clear()
        self.clipboard_append(address)
        self.show_notification("Address copied", address)

    def export_backup(self):
        """Zips the encrypted state (SyncData + ssh_keys) to a user-chosen
        location. Whitelist, not blocklist: frp/*.toml holds the FRP token
        and admin passwords in plaintext, syncthing_config holds the device
        keypair and API key, and bin/ is ~50MB of staged executables — none
        belong in an archive claimed to be encrypted."""
        from tkinter import filedialog
        dest = filedialog.asksaveasfilename(
            parent=self, title="Export Backup",
            defaultextension=".zip", initialfile="nerazimnet-backup.zip",
            filetypes=[("Zip archive", "*.zip")])
        if not dest:
            return
        src_dir = get_app_data_dir()
        try:
            with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
                for sub in ('SyncData', 'ssh_keys'):
                    sub_dir = os.path.join(src_dir, sub)
                    if not os.path.isdir(sub_dir):
                        continue
                    for root, _dirs, files in os.walk(sub_dir):
                        for name in files:
                            p = os.path.join(root, name)
                            z.write(p, os.path.relpath(p, src_dir))
            self.show_notification("Backup exported", dest)
        except Exception as e:
            logging.error(f"Backup export failed: {e}", exc_info=True)
            self.show_error("Backup Failed", str(e))

    def check_for_updates(self, callback):
        """Queries GitHub for the latest release; invokes callback(text)
        on the main thread."""
        def _ver_tuple(v):
            try:
                return tuple(int(x) for x in v.lstrip('v').split('.'))
            except (ValueError, AttributeError):
                return ()
        def worker():
            try:
                import requests
                r = requests.get(
                    "https://api.github.com/repos/nater0000/NerazimNet/releases/latest",
                    timeout=10, headers={"Accept": "application/vnd.github+json"})
                if r.status_code != 200:
                    result = f"Update check failed (HTTP {r.status_code})"
                else:
                    latest = (r.json().get("tag_name") or "").lstrip("v")
                    if latest and _ver_tuple(latest) > _ver_tuple(self._version):
                        result = (f"Update available: v{latest} (you have v{self._version})\n"
                                  "github.com/nater0000/NerazimNet/releases")
                    else:
                        result = f"Up to date (v{self._version})"
            except Exception as e:
                result = f"Update check failed: {e}"
            self.after(0, lambda: callback(result))
        threading.Thread(target=worker, daemon=True).start()

    def handle_first_run(self):
        """Handles first run by showing password setup UI directly."""
        logging.info("Handling first run: showing setup UI.")
        if hasattr(self, '_initial_frame') and self._initial_frame and self._initial_frame.winfo_exists():
            for widget in self._initial_frame.winfo_children(): widget.destroy()
            self._initial_frame.destroy()
        self._initial_frame = ctk.CTkFrame(self, fg_color="transparent"); self._initial_frame.grid(row=0, column=0, sticky="nsew")
        self._initial_frame.grid_rowconfigure(0, weight=1); self._initial_frame.grid_columnconfigure(0, weight=1)
        center_frame = ctk.CTkFrame(self._initial_frame, corner_radius=10); center_frame.grid(row=0, column=0, sticky="", padx=30, pady=30)
        ctk_label(center_frame, text="Welcome to NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk_label(center_frame, text="Create a New Master Password:").pack(padx=30, pady=(10, 0))
        entry_frame1 = ctk.CTkFrame(center_frame, fg_color="transparent"); entry_frame1.pack(padx=30, pady=5)
        self.setup_entry1 = ctk.CTkEntry(entry_frame1, show="*", width=200); self.setup_entry1.pack(side="left")
        show_icon = self.btn_images.get("eye-show"); use_icons = bool(show_icon)
        toggle_btn1 = ctk.CTkButton(entry_frame1, image=show_icon if use_icons else None, text="👁️" if not use_icons else "", width=28, anchor="center", command=lambda: self._toggle_setup_password_visibility(self.setup_entry1, toggle_btn1)); toggle_btn1.pack(side="left", padx=(5, 0))
        ctk_label(center_frame, text="Confirm Master Password:").pack(padx=30, pady=(10, 0))
        entry_frame2 = ctk.CTkFrame(center_frame, fg_color="transparent"); entry_frame2.pack(padx=30, pady=5)
        self.setup_entry2 = ctk.CTkEntry(entry_frame2, show="*", width=200); self.setup_entry2.pack(side="left")
        self.setup_entry2.bind("<Return>", self._on_setup_confirm)
        toggle_btn2 = ctk.CTkButton(entry_frame2, image=show_icon if use_icons else None, text="👁️" if not use_icons else "", width=28, anchor="center", command=lambda: self._toggle_setup_password_visibility(self.setup_entry2, toggle_btn2)); toggle_btn2.pack(side="left", padx=(5, 0))
        ctk_label(center_frame, text="Allowed: A-Z a-z 0-9 Space !@#$%^&*()_+-=[]{}|;:,.<>?", font=ctk.CTkFont(size=10), wraplength=250).pack(padx=30, pady=5)
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
        
        err = _validate_new_password(password, password2)
        if err:
            self.show_error("Setup Error", err)
            return
            
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
            ctk_label(center_frame, text="NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
            ctk_label(center_frame, text="Enter Master Password:").pack(padx=30, pady=(10, 0))
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
            ctk_label(center_frame, text="Welcome to NerazimNet!", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=30)
            ctk_label(center_frame, text="Initial setup required.").pack(padx=30, pady=10)
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
        if self.is_unlocked and not self.is_locked: return
        lockout_remaining = self._unlock_lockout_until - time.monotonic()
        if lockout_remaining > 0:
            ErrorDialog(self, title="Too Many Attempts",
                        message=f"Wait {int(lockout_remaining) + 1}s before trying again.")
            return
        if self._focus_after_id:
            try: self.after_cancel(self._focus_after_id)
            except Exception: pass
            self._focus_after_id = None
        unlocked, message_or_recovery_key = self.config_manager.unlock_with_password(password)
        if unlocked:
            self._unlock_failures = 0
            self.is_unlocked = True
            try: self.focus_set() 
            except Exception: pass
            if hasattr(self, '_initial_frame') and self._initial_frame and self._initial_frame.winfo_exists(): self._initial_frame.destroy()
            self._initial_frame = None; self.password_entry = None
            logging.debug("Initial UI frame destroyed.")
            if self.is_locked:
                # Relock path — backends are already running; rebuild the UI.
                self.is_locked = False
                self._build_main_ui()
                self.show_frame("DashboardView")
                self._load_auto_lock_setting()
            else:
                self._show_loading_screen()
                self._start_backend_services_threaded(callback=lambda rk=message_or_recovery_key: self._on_loading_complete(rk)) 
        else:
            self._unlock_failures += 1
            delay = min(2 ** self._unlock_failures, 60)
            self._unlock_lockout_until = time.monotonic() + delay
            msg = message_or_recovery_key or "Incorrect password."
            ErrorDialog(self, title="Unlock Failed",
                        message=f"{msg}\nTry again in {delay}s.")
            if hasattr(self, 'password_entry') and self.password_entry:
                entry = self.password_entry
                entry.delete(0, 'end')
                entry.configure(state="disabled")
                self.after(delay * 1000, self._reenable_unlock_entry, entry)
                
    def _reenable_unlock_entry(self, entry):
        try:
            if entry and entry.winfo_exists():
                entry.configure(state="normal")
                self._safe_focus(entry)
        except Exception:
            pass

    def _load_auto_lock_setting(self):
        try:
            self._auto_lock_minutes = int(self.get_setting('auto_lock_minutes', 0) or 0)
        except (TypeError, ValueError):
            self._auto_lock_minutes = 0
        self._start_auto_lock_timer()
        self._start_config_reload_timer()

    # --- Periodic remote-change reload ---
    # load_configuration re-decrypts every patch of every object (PBKDF2
    # per file — seconds of CPU), so it must never run on a save or on the
    # UI thread. Commits update memory incrementally; this poll merges
    # edits synced in from other devices, only when the dir signature
    # actually changed, always in a worker thread.
    def _start_config_reload_timer(self):
        if self._config_reload_job is None:
            self._config_reload_job = self.after(45_000, self._config_reload_tick)

    def _config_reload_tick(self):
        self._config_reload_job = self.after(45_000, self._config_reload_tick)
        if (self.is_shutting_down or not self.is_unlocked or self.is_locked
                or self._config_reload_running):
            return
        cm = self.config_manager
        if cm._sync_signature() == cm._last_sync_sig:
            return  # nothing changed on disk — skip the expensive replay
        cm._last_sync_sig = cm._sync_signature()
        self._config_reload_running = True

        def worker():
            try:
                cm.load_configuration()
            except Exception as e:
                logging.error(f"Periodic config reload failed: {e}", exc_info=True)
            finally:
                self._config_reload_running = False
                self.after(0, self._on_config_reloaded)

        threading.Thread(target=worker, daemon=True).start()

    def _on_config_reloaded(self):
        self.refresh_dashboard()
        view = self.frames.get("ServersView") if self.frames else None
        if view and view.winfo_exists() and hasattr(view, 'load_servers'):
            view.load_servers()
        self._refresh_devices_view()

    def _show_loading_screen(self):
        """Displays a loading screen UI."""
        logging.debug("Showing loading screen...")
        self._loading_frame = ctk.CTkFrame(self, fg_color="transparent"); self._loading_frame.grid(row=0, column=0, sticky="nsew")
        self._loading_frame.grid_rowconfigure(0, weight=1); self._loading_frame.grid_columnconfigure(0, weight=1)
        center_frame = ctk.CTkFrame(self._loading_frame, corner_radius=10); center_frame.grid(row=0, column=0, sticky="")
        ctk_label(center_frame, text="NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk_label(center_frame, text="Initializing services...").pack(padx=30, pady=10)
        progressbar = ctk.CTkProgressBar(center_frame, mode="indeterminate"); progressbar.pack(padx=30, pady=10, fill="x"); progressbar.start()
        ctk_label(center_frame, text="Please wait...", text_color=tk_color("fg_dim")).pack(padx=30, pady=(0, 20))
        # update_idletasks repaints without pumping the event queue —
        # a full update() here could reenter input callbacks mid-setup.
        self.update_idletasks()
        
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

            try:
                self.config_manager.ensure_ssh_key_encrypted()
            except Exception as e:
                logging.error(f"SSH key encryption check failed: {e}", exc_info=True)

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

        self._load_auto_lock_setting()
        try:
            saved_mode = self.get_setting('appearance_mode', 'system')
            if saved_mode:
                self.set_appearance_mode(str(saved_mode))
        except Exception as e:
            logging.debug(f"Failed to restore appearance mode: {e}")
        try:
            saved_scale = self.get_setting('ui_scale', 100)
            if saved_scale and int(saved_scale) != 100:
                self.set_ui_scale(int(saved_scale))
        except (TypeError, ValueError) as e:
            logging.debug(f"Failed to restore ui_scale: {e}")

        if recovery_key:
             self.after(200, lambda: self.view_recovery_key(recovery_key))

        # Quiet startup update check — fills the Settings > Updates label
        # without the user having to click "Check for Updates".
        self.after(5000, self._startup_update_check)

    def _startup_update_check(self):
        """One-shot background update check after unlock; results surface
        in Settings > Updates (and the log) without bothering the user."""
        def _report(text):
            try:
                logging.info(f"Update check: {text.splitlines()[0]}")
                view = self.frames.get("SettingsView") if self.frames else None
                label = getattr(view, "update_status_label", None)
                if label and label.winfo_exists():
                    label.configure(text=text, text_color=status_text_color(text))
            except Exception:
                pass
        try:
            self.check_for_updates(_report)
        except Exception as e:
            logging.debug(f"Startup update check skipped: {e}")

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
        """Recovery flow: recovery key -> unwrap master password -> re-encrypt."""
        logging.info("'Forgot Password' clicked.")
        dialog = RecoveryDialog(self)
        result = dialog.get_input()
        if not result:
            return
        recovery_key, new_password = result['key'], result['new']

        master_password = self.config_manager.recover_with_recovery_key(recovery_key)
        if not master_password:
            self.show_error("Recovery Failed",
                            "That recovery key didn't match. Check it and try again.")
            return
        # Re-encryption rewrites every encrypted file (2 PBKDF2 runs each) —
        # seconds of work. Run it off the UI thread behind a modal spinner
        # whose grab also blocks concurrent edits to the config.
        busy = WorkingDialog(self, title="Recovering Password",
                             message="Re-encrypting your configuration…")

        def worker():
            try:
                ok = self.config_manager.re_encrypt_with_new_password(
                    master_password, new_password, recovery_key=recovery_key)
            except Exception as e:
                logging.error(f"Recovery re-encryption failed: {e}", exc_info=True)
                ok = False
            self.after(0, finish, ok)

        def finish(ok):
            if busy.winfo_exists(): busy.destroy()
            if not ok:
                self.show_error("Recovery Failed",
                                "Password could not be re-encrypted. Check logs.")
                return
            logging.info("Password recovered via recovery key; unlocking.")
            self.attempt_unlock(new_password)

        threading.Thread(target=worker, daemon=True).start()

    def change_master_password(self):
        """Change the master password while unlocked."""
        logging.debug("Change master password requested.")
        if not self.is_unlocked:
            self.show_error("Error", "Must be unlocked to change password.")
            return
        dialog = PasswordChangeDialog(self)
        result = dialog.get_input()
        if not result:
            return

        busy = WorkingDialog(self, title="Changing Password",
                             message="Re-encrypting your configuration…")

        def worker():
            try:
                ok = self.config_manager.re_encrypt_with_new_password(
                    result['old'], result['new'])
            except Exception as e:
                logging.error(f"Password re-encryption failed: {e}", exc_info=True)
                ok = False
            self.after(0, finish, ok)

        def finish(ok):
            if busy.winfo_exists(): busy.destroy()
            if ok:
                self.show_error("Password Changed",
                                "Master password updated. Your recovery key is unchanged.")
            else:
                self.show_error("Change Failed",
                                "Re-encryption failed — is the current password correct?")

        threading.Thread(target=worker, daemon=True).start()

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
        
    def notify_config_divergence(self, names):
        """Called by ConfigManager when synced patch history diverges.

        Concurrent offline edits on two devices produce patches that can't
        apply to each other's base; ConfigManager truncates at the fork
        (deterministically, so all devices reconverge). This warns the user
        that some remote edits were dropped.
        """
        listing = ", ".join(names[:5]) + ("..." if len(names) > 5 else "")
        self.show_error(
            "Sync Conflict",
            f"Concurrent edits from another device could not be merged "
            f"for: {listing}.\n\nThe last consistent version was kept; "
            f"re-apply the conflicting changes manually if needed.")

    def set_appearance_mode(self, mode: str):
        """Sets the app's appearance mode (Light/Dark/System) and repaints ttk."""
        logging.info(f"Setting appearance mode to: {mode}")
        ctk.set_appearance_mode(mode.lower())
        try:
            from views import tree_style
            tree_style.refresh_theme()
        except Exception as e:
            logging.debug(f"ttk theme refresh failed: {e}")
        for frame in getattr(self, 'frames', {}).values():
            cb = getattr(frame, 'on_theme_changed', None)
            if cb:
                try:
                    cb()
                except Exception as e:
                    logging.debug(f"on_theme_changed failed: {e}")
        if self.is_unlocked:
            try:
                self.config_manager.set_setting('appearance_mode', str(mode).lower())
            except Exception as e:
                logging.debug(f"Failed to persist appearance mode: {e}")

    def set_ui_scale(self, percent):
        """Scales all text/widgets — Tk 'scaling' for ttk/fonts and CTk's
        widget scaling for its own widgets. percent: 100-200."""
        try:
            factor = max(0.75, min(3.0, float(percent) / 100.0))
        except (TypeError, ValueError):
            factor = 1.0
        logging.info(f"Setting UI scale to {factor:.2f}x")
        try:
            ctk.set_widget_scaling(factor)
        except Exception as e:
            logging.debug(f"ctk widget scaling failed: {e}")
        try:
            if not hasattr(self, '_base_tk_scaling'):
                self._base_tk_scaling = float(self.tk.call('tk', 'scaling'))
            self.tk.call('tk', 'scaling', self._base_tk_scaling * factor)
        except Exception as e:
            logging.debug(f"tk scaling failed: {e}")
        try:
            from views import tree_style
            tree_style.set_scale(factor)
        except Exception as e:
            logging.debug(f"ttk scale apply failed: {e}")
        # Re-create per-item tag fonts (they cache metrics) so scaled text
        # reaches rows that set fonts via tag_configure.
        for frame in getattr(self, 'frames', {}).values():
            cb = getattr(frame, 'on_theme_changed', None)
            if cb:
                try:
                    cb()
                except Exception as e:
                    logging.debug(f"scale tag refresh failed: {e}")
        if self.is_unlocked:
            try:
                self.config_manager.set_setting('ui_scale', int(percent))
            except Exception as e:
                logging.debug(f"Failed to persist ui_scale: {e}")
        
    def provision_server(self, server: dict, admin_pass: str = "", certbot_email: str = ""): # Made args optional
        """Starts the provisioning process for a server in a new thread."""
        logging.info(f"Starting provisioning for server: {server.get('name')}")
        pub_key = self.get_automation_public_key()
        if not pub_key:
            # No key configured yet — generate one automatically
            logging.info("No automation key found; generating a new SSH key pair.")
            gen_result = self.generate_ssh_key_pair()
            if gen_result:
                self.save_automation_credentials(gen_result[0], gen_result[1], gen_result[2])
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
                    admin_key_passphrase=creds.get('ssh_private_key_passphrase'),
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
    def save_automation_credentials(self, private_key_path: str, public_key_path: str, passphrase: str = None): self.config_manager.save_or_update_automation_credentials(private_key_path, public_key_path, passphrase)
    def get_or_create_frp_token(self) -> str | None: return self.config_manager.get_or_create_frp_token() if self.is_unlocked else None
    def generate_ssh_key_pair(self) -> tuple[str, str, str | None] | None:
        """Generates an encrypted key pair; returns (priv, pub, passphrase)."""
        try:
            return self.crypto_manager.generate_ssh_key_pair(
                passphrase=secrets.token_urlsafe(18))
        except Exception as e:
            logging.error(f"SSH key generation failed: {e}", exc_info=True); return None
    def get_my_device_id(self) -> str | None: return self.syncthing_manager.my_device_id
    def get_my_device_name(self) -> str: return os.getenv('COMPUTERNAME') or platform.node() or 'My Device'
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
        if not dialog.get_input():
            logging.info(f"Remove device {client_id} cancelled.")
            return

        # The Syncthing API call blocks — run it in a worker, finish on main.
        def worker():
            try:
                self.remove_syncthing_device(client_id)
            except Exception as e:
                logging.error(f"Failed to remove device {client_id}: {e}", exc_info=True)
                self.after(0, self.show_error, "Remove Failed",
                           f"Could not remove device:\n{e}")
                return
            self.after(0, self._finish_remove_client, client_id)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_remove_client(self, client_id: str):
        """Main-thread cleanup once the Syncthing device removal completes."""
        client_to_delete = None
        for obj_id, obj_data in self.config_manager.get_all_objects_for_debug().items():
            if obj_data.get('type') == 'client' and obj_data.get('syncthing_id') == client_id:
                client_to_delete = obj_id; break
        if client_to_delete: self.delete_object(client_to_delete)
        self._refresh_devices_view()

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
                self.tunnel_manager.drop_tunnel_state(tunnel_id)
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

    def accept_device_invite(self):
        """Prompt for an invite string from another device and join its share."""
        logging.info("Accept device invite requested.")
        if not self.is_unlocked: return

        invite = AcceptInviteDialog(self, controller=self).get_input()
        if not invite:
            logging.info("Accept invite cancelled.")
            return

        # API calls block for seconds — keep them off the main thread.
        def worker():
            try:
                ok = self.accept_syncthing_invite(invite)
            except Exception as e:
                logging.error(f"Accept invite failed: {e}", exc_info=True)
                self.after(0, self.show_error, "Invite Failed",
                           f"Could not accept the invite:\n{e}")
                return
            if ok:
                logging.info("Invite accepted — device added to the share.")
                self.after(0, self._refresh_devices_view)
            else:
                self.after(0, self.show_error, "Invite Failed",
                           "Could not accept the invite. Check that the "
                           "string is complete and that Syncthing is running.")

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_devices_view(self):
        view = self.frames.get("SettingsView") if self.frames else None
        if view and view.winfo_exists():
            view._load_devices_data()
            