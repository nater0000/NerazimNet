import customtkinter as ctk
import qrcode
from PIL import Image
import logging
import re
import os
import io
import tkinter # Added for winfo_exists checks

class ToolTip(ctk.CTkToplevel):
    """
    A shared tooltip window that manages its own show/hide delays.
    """
    def __init__(self, parent, show_delay_ms=500, hide_delay_ms=100):
        super().__init__(parent)
        self._parent = parent
        self.show_delay = show_delay_ms
        self.hide_delay = hide_delay_ms
        
        self.withdraw() # Start hidden
        self.overrideredirect(True) # No window decorations
        self.wm_attributes("-topmost", True) # Keep on top

        self._label = ctk.CTkLabel(self, text="", corner_radius=5,
                                  fg_color=("#3D3D3D", "#4D4D4D"),
                                  text_color=("white", "white"),
                                  wraplength=250, # Max width
                                  justify="left")
        self._label.pack(ipadx=5, ipady=3)
        
        self._show_id = None
        self._hide_id = None
        self._event = None
        self._text = ""

    def schedule_show(self, event, text: str):
        """Schedules the tooltip to appear after the show delay."""
        if self._hide_id:
            self.after_cancel(self._hide_id)
            self._hide_id = None
        
        if self._show_id:
            self.after_cancel(self._show_id)
            self._show_id = None

        self._event = event
        self._text = text
        
        self._show_id = self.after(self.show_delay, self._show)

    def schedule_hide(self, event=None):
        """Schedules the tooltip to hide after the hide delay."""
        if self._show_id:
            self.after_cancel(self._show_id)
            self._show_id = None
            
        if not self._hide_id:
            self._hide_id = self.after(self.hide_delay, self._hide)

    def _show(self):
        """Internal method to display the tooltip."""
        if not self._text or not self._event:
            return
            
        try:
            if not self.winfo_exists(): return
        except Exception: return

        self._label.configure(text=self._text)
        
        x = self._event.x_root + 15 
        y = self._event.y_root + 10 

        self.update_idletasks()
        tip_height = self.winfo_reqheight()
        tip_width = self.winfo_reqwidth()

        screen_height = self.winfo_screenheight()
        if y + tip_height > screen_height:
            y = self._event.y_root - tip_height - 5 

        screen_width = self.winfo_screenwidth()
        if x + tip_width > screen_width:
             x = screen_width - tip_width - 5

        x = max(0, x); y = max(0, y)

        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.lift()
        self._show_id = None

    def _hide(self):
        """Internal method to hide the window."""
        self._show_id = None
        self._hide_id = None
        try:
            if self.winfo_exists():
                self.withdraw()
        except Exception:
            pass

class BaseDialog(ctk.CTkToplevel):
    """
    Base class for modal dialogs.
    Creates a toplevel window, grabs focus, and waits for a result.
    """
    def __init__(self, parent, title="Dialog"):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.title(title)
        
        self.result = None # Stores the dialog result
        self._parent = parent # Store parent for centering

        # Main content frame
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=15, pady=15)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # CTkEntry placeholders don't paint until the widget's first
        # focus/redraw — force one once the window is actually mapped
        self.bind("<Map>", lambda _e: self.after_idle(self._repaint_placeholders), add="+")

    def _repaint_placeholders(self, widget=None):
        for child in (widget or self).winfo_children():
            if isinstance(child, ctk.CTkEntry):
                ph = child.cget("placeholder_text")
                if ph:
                    child.configure(placeholder_text=ph)  # triggers _draw()
            elif child.winfo_children():
                self._repaint_placeholders(child)

    def _center_window(self):
        """Centers the dialog over its parent window."""
        try:
            self.update_idletasks()
            parent_x = self._parent.winfo_x()
            parent_y = self._parent.winfo_y()
            parent_width = self._parent.winfo_width()
            parent_height = self._parent.winfo_height()
            
            dialog_width = self.winfo_width()
            dialog_height = self.winfo_height()
            
            x = parent_x + (parent_width // 2) - (dialog_width // 2)
            y = parent_y + (parent_height // 2) - (dialog_height // 2)
            
            self.geometry(f"+{x}+{y}")
        except Exception as e:
            logging.warning(f"Error centering dialog: {e}")

    def _on_ok(self, event=None):
        """Handles OK button click or Enter key press."""
        self.result = True # Mark as successful
        self.grab_release()
        self.destroy()

    def _on_cancel(self, event=None):
        """Handles Cancel button click or window close."""
        self.result = None # Mark as cancelled
        self.grab_release()
        self.destroy()

    def get_input(self):
        """Waits for the dialog to be destroyed and returns the result."""
        self.resizable(False, False)
        self._center_window() # Center *after* all widgets are created
        self.wait_window(self)
        return self.result

class UnlockDialog(BaseDialog):
    """Dialog for entering master password or setting it on first run."""
    def __init__(self, parent, first_run: bool = False, controller=None, title=None):
        # Ensure controller exists before proceeding
        if not controller:
            logging.critical("UnlockDialog requires a controller instance!")
            # Cannot proceed, maybe raise an error or handle differently
            super().__init__(parent, title="Error") # Init base to allow destroy
            ctk.CTkLabel(self, text="Internal Error: Controller missing.").pack(padx=20, pady=20)
            self.after(100, self.destroy)
            return

        self.controller = controller
        self.first_run = first_run
        title = title or ("Create Master Password" if first_run else "Unlock NerazimNet")
        super().__init__(parent, title=title) # Call BaseDialog init

        # --- Load images via controller ---
        self.show_icon = None
        self.hide_icon = None
        self.bg_image = None
        self.use_image_icons = False
        placeholder_img = None # Define placeholder ref
        try:
            if hasattr(self.controller, 'images') and self.controller.images:
                self.show_icon = self.controller.btn_images.get("eye-show")
                self.hide_icon = self.controller.btn_images.get("eye-hide")
                self.bg_image = self.controller.images.get("bg_gradient")

                # Define placeholder image for comparison
                placeholder_img = ctk.CTkImage(Image.new('RGB', (20,20), color='red'), size=(20,20))._light_image

                # Check if essential icons were loaded (not red squares)
                if self.show_icon and self.show_icon._light_image != placeholder_img and \
                   self.hide_icon and self.hide_icon._light_image != placeholder_img:
                    self.use_image_icons = True
                else:
                     raise ValueError("Eye icons loaded as placeholders.")
            else:
                raise ValueError("Controller images dictionary is missing or empty.")

        except Exception as e:
            logging.warning(f"UnlockDialog icons failed ({e}). Using text fallback.")
            self.show_icon = "👁️" # Text fallback
            self.hide_icon = "🔒" # Text fallback
            self.use_image_icons = False
            self.bg_image = None # Ensure no background if icons failed

        # --- UI Setup ---
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Background Image (Optional)
        bg_frame_fg = "transparent" # Default background
        # Check if bg_image loaded correctly
        if self.bg_image and placeholder_img and self.bg_image._light_image != placeholder_img:
            self.bg_label = ctk.CTkLabel(self, text="", image=self.bg_image)
            self.bg_label.grid(row=0, column=0, sticky="nsew")
            # Determine fg_color based on appearance mode for better contrast on background
            bg_frame_fg = ctk.ThemeManager.theme["CTkFrame"]["fg_color"]
        # else: use default transparent background

        # Centered Content Frame
        self.main_frame = ctk.CTkFrame(self, corner_radius=10, fg_color=bg_frame_fg)
        self.main_frame.grid(row=0, column=0, padx=30, pady=30, sticky="")

        self.geometry("400x300") # Fixed size
        self.resizable(False, False)

        # Create specific UI elements (unlock or setup)
        if self.first_run:
            self._create_password_setup_ui()
            self.after(100, lambda: self.entry1.focus_set() if self.winfo_exists() else None)
        else:
            self._create_unlock_ui()
            self.after(100, lambda: self.entry1.focus_set() if self.winfo_exists() else None)

    def _toggle_password_visibility(self, entry, button):
        if not entry or not button: return
        try:
            current_show = entry.cget("show")
            if current_show == "*":
                entry.configure(show="")
                button.configure(image=self.hide_icon if self.use_image_icons else None,
                                 text=self.hide_icon if not self.use_image_icons else "")
            else:
                entry.configure(show="*")
                button.configure(image=self.show_icon if self.use_image_icons else None,
                                 text=self.show_icon if not self.use_image_icons else "")
        except Exception as e:
             logging.warning(f"Error toggling password visibility: {e}")


    def _create_unlock_ui(self):
        ctk.CTkLabel(self.main_frame, text="NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk.CTkLabel(self.main_frame, text="Enter Master Password:").pack(padx=30, pady=(10, 0))

        entry_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        entry_frame.pack(padx=30, pady=10)

        self.entry1 = ctk.CTkEntry(entry_frame, show="*", width=200)
        self.entry1.pack(side="left")
        self.entry1.bind("<Return>", self._on_ok)
        # Focus is set by app.py using after(150, ...)

        toggle_btn1 = ctk.CTkButton(entry_frame, image=self.show_icon if self.use_image_icons else None,
                                    text=self.show_icon if not self.use_image_icons else "",
                                    width=28, anchor="center", # Center icon/text
                                    command=lambda: self._toggle_password_visibility(self.entry1, toggle_btn1))
        toggle_btn1.pack(side="left", padx=(5, 0))

        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(padx=30, pady=(10, 20))

        ctk.CTkButton(button_frame, text="Unlock", command=self._on_ok, width=110).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="Forgot Password?", command=self._on_forgot, width=110, fg_color="transparent", border_width=1, text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"]).pack(side="left", padx=5)

    def _create_password_setup_ui(self):
        ctk.CTkLabel(self.main_frame, text="Welcome to NerazimNet", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=30, pady=(30, 10))
        ctk.CTkLabel(self.main_frame, text="Create a New Master Password:").pack(padx=30, pady=(10, 0))

        entry_frame1 = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        entry_frame1.pack(padx=30, pady=5)
        self.entry1 = ctk.CTkEntry(entry_frame1, show="*", width=200)
        self.entry1.pack(side="left")
        # Focus is set by app.py using after(150, ...)
        toggle_btn1 = ctk.CTkButton(entry_frame1, image=self.show_icon if self.use_image_icons else None, text=self.show_icon if not self.use_image_icons else "", width=28, anchor="center", command=lambda: self._toggle_password_visibility(self.entry1, toggle_btn1))
        toggle_btn1.pack(side="left", padx=(5, 0))

        ctk.CTkLabel(self.main_frame, text="Confirm Master Password:").pack(padx=30, pady=(10, 0))
        entry_frame2 = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        entry_frame2.pack(padx=30, pady=5)
        self.entry2 = ctk.CTkEntry(entry_frame2, show="*", width=200)
        self.entry2.pack(side="left")
        self.entry2.bind("<Return>", self._on_ok)
        toggle_btn2 = ctk.CTkButton(entry_frame2, image=self.show_icon if self.use_image_icons else None, text=self.show_icon if not self.use_image_icons else "", width=28, anchor="center", command=lambda: self._toggle_password_visibility(self.entry2, toggle_btn2))
        toggle_btn2.pack(side="left", padx=(5, 0))

        ctk.CTkLabel(self.main_frame, text="Allowed: A-Z a-z 0-9 Space !@#$%^&*()_+-=[]{}|;:,.<>?", font=("", 10), wraplength=250).pack(padx=30, pady=5)
        ctk.CTkButton(self.main_frame, text="Create", command=self._on_ok, width=230).pack(padx=30, pady=20)

    def _on_ok(self, event=None):
        # Validate password input
        password = self.entry1.get() # Get password from first entry
        if self.first_run:
            password2 = self.entry2.get()
            # Regex includes space now
            allowed_chars = r"^[A-Za-z0-9 !@#$%^&*()_+\-=\[\]{}|;:,.<>?]*$"
            if password != password2:
                ErrorDialog(self, message="Passwords do not match.")
                return # Keep dialog open
            if not password: # Check if empty
                ErrorDialog(self, message="Password cannot be empty.")
                return
            if not re.fullmatch(allowed_chars, password):
                ErrorDialog(self, message="Password contains invalid characters.\nAllowed: A-Z a-z 0-9 Space !@#$%^&*()_+-=[]{}|;:,.<>?")
                return

        # If validation passes (or not first run), set result and close
        self.result = password
        super()._on_ok() # Calls BaseDialog._on_ok -> BaseDialog.destroy

    def _on_forgot(self):
        self.result = None # Ensure result is None if flow continues elsewhere
        if self.controller:
            logging.info("Forgot password button clicked.")
            # Destroy *this* dialog first to avoid multiple modals
            self.destroy()
            # Use 'after' to schedule the next step after this dialog is gone
            self.controller.after(50, self.controller.forgot_password)
        else:
             logging.warning("Forgot password clicked but no controller found.")
             self.destroy() # Just close


class LoadingDialog(BaseDialog):
    """Modal dialog showing an indeterminate progress bar."""
    def __init__(self, parent, title="Loading..."):
        super().__init__(parent, title=title)
        self.geometry("400x300")
        self.resizable(False, False)

        # Center content using grid
        self.grid_rowconfigure(0, weight=1) # Space above
        self.grid_rowconfigure(3, weight=1) # Space below
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Initializing NerazimNet...", font=ctk.CTkFont(size=16)).grid(row=0, column=0, padx=40, pady=(40, 10), sticky="s")

        self.progressbar = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progressbar.grid(row=1, column=0, padx=40, pady=10, sticky="ew")
        self.progressbar.start() # Start animation

        ctk.CTkLabel(self, text="Please wait...", text_color="gray60").grid(row=2, column=0, padx=40, pady=(0, 40), sticky="n")

        self.protocol("WM_DELETE_WINDOW", lambda: None) # Prevent user from closing

        # BaseDialog already schedules centering

    def destroy(self):
        """Override destroy to stop the progress bar animation."""
        if hasattr(self, 'progressbar'):
            try:
                # Check if progressbar still exists before stopping
                if self.progressbar.winfo_exists():
                    self.progressbar.stop()
            except Exception as e:
                 logging.warning(f"Error stopping progress bar: {e}")
        super().destroy() # Call BaseDialog's safe destroy


class ProvisionDialog(BaseDialog):
    """Asks for credentials needed for server provisioning."""
    def __init__(self, parent, server_name: str, server_ip: str, title="Server Credentials"):
        super().__init__(parent, title=title)
        
        self.result = None # Stores dict on OK

        ctk.CTkLabel(self.main_frame, text=f"Enter credentials for: {server_name} ({server_ip})",
                       font=ctk.CTkFont(weight="bold")).pack(pady=(0, 10))
        ctk.CTkLabel(self.main_frame, text="These are used once for setup and are NOT saved.",
                       font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 15))

        grid_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        grid_frame.pack(fill="x")
        grid_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(grid_frame, text="Admin User:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.user_entry = ctk.CTkEntry(grid_frame, placeholder_text="e.g., root")
        self.user_entry.grid(row=0, column=1, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(grid_frame, text="Admin Password:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.pass_entry = ctk.CTkEntry(grid_frame, show="*")
        self.pass_entry.grid(row=1, column=1, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(grid_frame, text="Certbot Email:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.email_entry = ctk.CTkEntry(grid_frame, placeholder_text="For Let's Encrypt SSL alerts")
        self.email_entry.grid(row=2, column=1, padx=10, pady=5, sticky="ew")

        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=20)
        
        self.ok_button = ctk.CTkButton(button_frame, text="Start Provisioning", command=self._on_ok)
        self.ok_button.pack(side="left", padx=10)
        
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self._on_cancel,
                                           fg_color="transparent", border_width=1,
                                           text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        self.cancel_button.pack(side="left", padx=10)
        
        self.user_entry.focus_set()

    def _on_ok(self, event=None):
        user = self.user_entry.get().strip()
        password = self.pass_entry.get() # Don't strip password
        email = self.email_entry.get().strip()
        
        if not user:
             ErrorDialog(self, title="Input Error", message="Admin User cannot be empty.")
             return
        if not password:
             ErrorDialog(self, title="Input Error", message="Admin Password cannot be empty.")
             return
        if not email or "@" not in email or "." not in email:
             ErrorDialog(self, title="Input Error", message="Please enter a valid email for Certbot.")
             return
             
        self.result = {"user": user, "password": password, "email": email}
        self.grab_release()
        self.destroy()


class LogViewerDialog(BaseDialog):
    """A non-modal dialog to display log content."""
    def __init__(self, parent, log_content: str, title="View Logs"):
        # Override BaseDialog __init__ for non-modal behavior
        super(BaseDialog, self).__init__(parent) # Call CTkToplevel init
        
        self.transient(parent)
        # self.grab_set() # --- DO NOT GRAB ---
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.title(title)
        
        self.result = None
        self._parent = parent

        self.main_frame = ctk.CTkFrame(self) # Use self, not self.main_frame
        self.main_frame.pack(fill="both", expand=True)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(self.main_frame, wrap="none", font=("Courier New", 12))
        self.textbox.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.textbox.insert("1.0", log_content or "No log content available.")
        self.textbox.configure(state="disabled")
        
        # Auto-scroll to end
        self.textbox.see("end")

        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.grid(row=1, column=0, pady=(0, 10))

        # Add a refresh button? (Maybe later)
        
        self.ok_button = ctk.CTkButton(button_frame, text="Close", command=self._on_cancel, width=100)
        self.ok_button.pack()
        
        # --- Center and show (since get_input isn't called) ---
        self.resizable(True, True)
        self.geometry("700x500") # Start with a good size for logs
        self._center_window()
        self.ok_button.focus_set()

    def get_input(self):
        """Override to just show the window, not wait."""
        # This dialog is non-modal, so get_input shouldn't be called.
        # If it is, just log it.
        logging.warning("LogViewerDialog.get_input() called, but it's non-modal.")
        return None
    
class ProvisioningLogDialog(LogViewerDialog):
    """A log viewer that can be updated and shows a complete/failed status."""
    def __init__(self, parent, server_name: str, action: str = "Provisioning"):
        super().__init__(parent, log_content=f"Starting {action.lower()}...\n\n", title=f"{action}: {server_name}")

        self.action = action
        self.progressbar = ctk.CTkProgressBar(self.main_frame, mode="indeterminate")
        self.progressbar.grid(row=2, column=0, padx=10, pady=5, sticky="ew")
        self.progressbar.start()

        self.ok_button.configure(state="disabled") # Can't close until done
        self.all_logs = [f"Starting {action.lower()}...\n"]

    def update_log(self, log_lines: list):
        """Appends new lines to the log."""
        if not self.textbox or not self.textbox.winfo_exists(): return
        
        self.textbox.configure(state="normal")
        for line in log_lines:
            self.all_logs.append(line)
            self.textbox.insert("end", line + "\n")
        self.textbox.configure(state="disabled")
        self.textbox.see("end")

    def complete(self, success: bool):
        """Marks the provisioning as complete."""
        if not self.textbox or not self.textbox.winfo_exists(): return
        
        self.progressbar.stop()
        self.progressbar.grid_remove()
        
        self.textbox.configure(state="normal")
        action_upper = self.action.upper()
        if success:
            self.textbox.insert("end", f"\n--- {action_upper} COMPLETE (SUCCESS) ---\n")
            self.title(f"{self.action} Succeeded: {self.title().split(': ')[1]}")
        else:
            self.textbox.insert("end", f"\n--- {action_upper} FAILED ---\n")
            self.title(f"{self.action} FAILED: {self.title().split(': ')[1]}")
        
        self.textbox.configure(state="disabled")
        self.textbox.see("end")
        self.ok_button.configure(state="normal") # Enable close button

class ServerDialog(BaseDialog):
    """Dialog to add or edit a Server configuration."""
    def __init__(self, parent, controller, title="Add Server", initial_data=None):
        super().__init__(parent, title=title)
        
        self.controller = controller
        self.tooltip = controller.tooltip if hasattr(controller, 'tooltip') else None

        self.initial_data = initial_data or {}
        self.result = None # Will be a dict on OK
        
        # --- Form Frame ---
        form_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        form_frame.pack(fill="x", expand=True)
        form_frame.grid_columnconfigure(1, weight=1)
        
        row = 0
        ctk.CTkLabel(form_frame, text="Server Name:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.name_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 'My VPS (Linode)'")
        self.name_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        
        row += 1
        ctk.CTkLabel(form_frame, text="IP Address / Host:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.ip_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., '123.45.67.89' or 'server.example.com'")
        self.ip_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        row += 1
        ctk.CTkLabel(form_frame, text="Admin User:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.admin_user_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 'root' or 'ubuntu'")
        self.admin_user_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        if self.tooltip:
             tooltip_text = "Sudo-capable user with your automation SSH key installed.\nSet automatically by provisioning; needed for route sync."
             self.admin_user_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
             self.admin_user_entry.bind("<Leave>", self.tooltip.schedule_hide)

        row += 1
        ctk.CTkLabel(form_frame, text="Certbot Email:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.certbot_email_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 'you@example.com'")
        self.certbot_email_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        if self.tooltip:
             tooltip_text = "Email used for Let's Encrypt certificate registration during route sync."
             self.certbot_email_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
             self.certbot_email_entry.bind("<Leave>", self.tooltip.schedule_hide)

        # --- *** UPDATED "MANUALLY CONFIGURED" CHECKBOX *** ---
        row += 1
        self.is_provisioned_var = ctk.BooleanVar() # Use a BooleanVar
        self.manual_config_checkbox = ctk.CTkCheckBox(
            form_frame, 
            text="I have manually configured this server (Ready ✅)",
            variable=self.is_provisioned_var,
            onvalue=True,
            offvalue=False
        )
        self.manual_config_checkbox.grid(row=row, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        # --- *** END CHANGE *** ---

        # --- Load initial data ---
        self.name_entry.insert(0, self.initial_data.get("name", ""))
        self.ip_entry.insert(0, self.initial_data.get("ip_address", ""))
        self.admin_user_entry.insert(0, self.initial_data.get("admin_user", ""))
        self.certbot_email_entry.insert(0, self.initial_data.get("certbot_email", ""))
        
        # --- *** SET CHECKBOX STATE FROM LOADED DATA *** ---
        self.is_provisioned_var.set(self.initial_data.get("is_provisioned", False))

        # --- Button Frame ---
        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=20)
        
        self.ok_button = ctk.CTkButton(button_frame, text="Save", command=self._on_ok)
        self.ok_button.pack(side="left", padx=10)
        
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self._on_cancel,
                                           fg_color="transparent", border_width=1,
                                           text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        self.cancel_button.pack(side="left", padx=10)
        
        self.name_entry.focus_set()
        self.bind("<Return>", self._on_ok)

    def _on_ok(self, event=None):
        name = self.name_entry.get().strip()
        ip_address = self.ip_entry.get().strip()

        if not name:
             ErrorDialog(self, title="Input Error", message="Server Name cannot be empty.")
             return
        if not ip_address:
             ErrorDialog(self, title="Input Error", message="IP Address / Host cannot be empty.")
             return
             
        # Merge new data with initial data (to preserve 'id')
        self.result = self.initial_data.copy()
        
        # --- *** EXPLICITLY REMOVE BOTH PASSWORD KEYS *** ---
        if "sudo_password" in self.result:
            logging.debug("Removing deprecated 'sudo_password' from server config.")
            self.result.pop("sudo_password", None)
            
        if "password" in self.result: # <-- ADDED THIS BLOCK
            logging.debug("Removing deprecated 'password' from server config.")
            self.result.pop("password", None)
        # --- *** END CHANGE *** ---

        self.result.update({
            "name": name,
            "ip_address": ip_address,
            "admin_user": self.admin_user_entry.get().strip(),
            "certbot_email": self.certbot_email_entry.get().strip(),
            "obj_type": "server"
        })
        
        # Set 'is_provisioned' from checkbox
        self.result['is_provisioned'] = self.is_provisioned_var.get()
        
        self.grab_release()
        self.destroy()

class TunnelDialog(BaseDialog):
    """Dialog to add or edit a Tunnel configuration."""
    def __init__(self, parent, controller, title="Add Tunnel", initial_data=None):
        super().__init__(parent, title=title)
        
        self.controller = controller
        # --- FIX: Get shared tooltip instance ---
        self.tooltip = controller.tooltip if hasattr(controller, 'tooltip') else None

        self.initial_data = initial_data or {}
        self.result = None # Will be a dict on OK

        # --- Get data for dropdowns ---
        self.client_map, self.client_names = self.controller.get_clients_for_dropdown()
        self.servers_map = {s.get('name', 'N/A'): s.get('id', 'N/A') for s in self.controller.get_servers()}
        self.server_names = sorted(self.servers_map.keys())

        # --- Form Frame ---
        form_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        form_frame.pack(fill="x", expand=True)
        form_frame.grid_columnconfigure(1, weight=1)
        
        # --- NEW: Route Type Toggle ---
        row = 0
        ctk.CTkLabel(form_frame, text="Route Type:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        
        # Determine initial type
        initial_type = self.initial_data.get("route_type", "tunnel")
        self.route_type_var = ctk.StringVar(value=initial_type)
        
        self.type_switch = ctk.CTkSegmentedButton(
            form_frame, 
            values=["Tunnel to Device", "Local VPS Service"],
            variable=self.route_type_var,
            command=self._on_type_change
        )
        self.type_switch.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        self.type_switch.set("Local VPS Service" if initial_type == "local" else "Tunnel to Device")


        row += 1
        ctk.CTkLabel(form_frame, text="Hostname:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.hostname_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 'app.example.com'")
        self.hostname_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        if self.tooltip:
             tooltip_text = "The public-facing domain name (e.g., 'sms.mydomain.com')."
             self.hostname_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
             self.hostname_entry.bind("<Leave>", self.tooltip.schedule_hide)

        row += 1
        ctk.CTkLabel(form_frame, text="Server:").grid(row=row, column=0, padx=10, pady=5, sticky="w")
        self.server_menu = ctk.CTkOptionMenu(form_frame, values=self.server_names, command=self._on_server_select)
        if not self.server_names:
            self.server_menu.configure(values=["No servers configured"], state="disabled")
            self.server_menu.set("No servers configured")
        self.server_menu.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        # --- Dynamic Label for Port ---
        row += 1
        self.port_label = ctk.CTkLabel(form_frame, text="Remote Port:") # Will be updated by toggle
        self.port_label.grid(row=row, column=0, padx=10, pady=5, sticky="w")
        
        self.remote_port_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 8080 or 5000")
        self.remote_port_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        # --- Client Device (Hidden for Local) ---
        row += 1
        self.client_label = ctk.CTkLabel(form_frame, text="Client Device:")
        self.client_label.grid(row=row, column=0, padx=10, pady=5, sticky="w")
        
        self.client_menu = ctk.CTkOptionMenu(form_frame, values=self.client_names, command=self._on_client_select)
        if not self.client_names:
             self.client_menu.configure(values=["No devices available"], state="disabled")
             self.client_menu.set("No devices available")
        self.client_menu.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        # --- Local Destination (Hidden for Local) ---
        row += 1
        self.local_dest_label = ctk.CTkLabel(form_frame, text="Local Destination:")
        self.local_dest_label.grid(row=row, column=0, padx=10, pady=5, sticky="w")
        
        self.local_dest_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., 'localhost:8080'")
        self.local_dest_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")

        # --- Extra Service Ports (e.g. LiveKit WebSocket) ---
        row += 1
        self.extra_ports_label = ctk.CTkLabel(form_frame, text="Extra Service Ports:")
        self.extra_ports_label.grid(row=row, column=0, padx=10, pady=5, sticky="w")

        self.extra_ports_entry = ctk.CTkEntry(form_frame, placeholder_text="e.g., '7880:localhost:7880, raw:7881:localhost:7881'")
        self.extra_ports_entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
        if self.tooltip:
             tooltip_text = ("Optional extra public:local port pairs.\n"
                             "Default (http/wss): e.g., '7880:localhost:7880'\n"
                             "Raw TCP (Nginx stream/L4): 'raw:7881:localhost:7881'\n"
                             "UDP port range (LiveKit/WebRTC): 'udp:50000-50020:localhost:50000-50020'")
             self.extra_ports_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
             self.extra_ports_entry.bind("<Leave>", self.tooltip.schedule_hide)

        # --- Auto Start ---
        row += 1
        self.auto_start_var = ctk.StringVar(value="on")
        self.auto_start_check = ctk.CTkCheckBox(form_frame, text="Auto-start on this device?",
                                                variable=self.auto_start_var, onvalue="on", offvalue="off")
        self.auto_start_check.grid(row=row, column=1, padx=10, pady=10, sticky="w")


        # --- Load initial data ---
        self.hostname_entry.insert(0, self.initial_data.get("hostname", ""))
        # Auto-suggest the next free port for tunnels only — for 'local'
        # routes this field is the app's own port on the VPS, not unique.
        default_port = "" if initial_type == "local" else self._default_remote_port()
        self.remote_port_entry.insert(0, self.initial_data.get("remote_port") or default_port)
        self.local_dest_entry.insert(0, self.initial_data.get("local_destination", ""))
        self.extra_ports_entry.insert(0, self.initial_data.get("extra_ports", ""))
        
        # Set server dropdown
        initial_server_id = self.initial_data.get("server_id")
        for name, server_id in self.servers_map.items():
            if server_id == initial_server_id:
                self.server_menu.set(name); break
        
        # Set client dropdown
        initial_client_id = self.initial_data.get("client_device_id")
        for name, client_id in self.client_map.items():
            if client_id == initial_client_id:
                self.client_menu.set(name); break
        
        # Set auto-start (Handle 'auto_start_on_device_ids' logic)
        my_device_id = self.controller.get_my_device_id()
        auto_start_list = self.initial_data.get("auto_start_on_device_ids", [])
        if my_device_id in auto_start_list:
             self.auto_start_var.set("on")
        else:
             self.auto_start_var.set("off")
        
        # Trigger UI update based on initial type
        self._on_type_change(self.type_switch.get())

        # --- Button Frame ---
        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=20)
        
        self.ok_button = ctk.CTkButton(button_frame, text="Save", command=self._on_ok)
        self.ok_button.pack(side="left", padx=10)
        
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self._on_cancel,
                                           fg_color="transparent", border_width=1,
                                           text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        self.cancel_button.pack(side="left", padx=10)
        
        self.hostname_entry.focus_set()
        self.bind("<Return>", self._on_ok)

    def _default_remote_port(self) -> str:
        """Next free remote port: 10000 when no tunnels exist, else max(used)+1.

        Counts each tunnel's remote_port plus the remote side of every
        extra_ports spec (range upper bounds included) so the suggestion
        can't collide with a port already bound on the VPS.
        """
        used = []
        for t in self.controller.get_tunnels():
            try:
                used.append(int(t.get('remote_port') or 0))
            except (TypeError, ValueError):
                pass
            for spec in (t.get('extra_ports') or '').split(','):
                m = re.fullmatch(
                    r'\s*(?:(?:raw|tcp|udp|http|wss):)?(\d+)(?:-(\d+))?:.*',
                    spec.strip())
                if m:
                    used.append(int(m.group(2) or m.group(1)))
        return str(max(used, default=9999) + 1)

    def _on_type_change(self, value):
        """Updates UI elements based on selected route type."""
        is_local = (value == "Local VPS Service")
        
        if is_local:
            # Local Route Mode
            self.port_label.configure(text="App Port (VPS):")
            if self.tooltip:
                tooltip_text = "The port your service is listening on localhost (e.g., 5000)."
                self.remote_port_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))
            
            # Hide Tunnel-specific fields
            self.client_label.grid_remove()
            self.client_menu.grid_remove()
            self.local_dest_label.grid_remove()
            self.local_dest_entry.grid_remove()
            
            # Auto-start logic changes for local: always enabled for "this" device to apply config?
            # Or perhaps we treat "this device" as the controller for the local route.
            self.auto_start_check.configure(state="normal") 

        else:
            # Tunnel Mode
            self.port_label.configure(text="Remote Port:")
            if self.tooltip:
                 tooltip_text = "The port the *server* will listen on. Must be unique."
                 self.remote_port_entry.bind("<Enter>", lambda e, text=tooltip_text: self.tooltip.schedule_show(e, text))

            # Show Tunnel-specific fields
            self.client_label.grid()
            self.client_menu.grid()
            self.local_dest_label.grid()
            self.local_dest_entry.grid()
            
            # Re-apply client select logic
            self._on_client_select(self.client_menu.get())

    def _on_server_select(self, server_name: str):
        pass 

    def _on_client_select(self, client_name: str):
        """Enables/Disables the auto-start checkbox based on client selection."""
        if self.route_type_var.get() == "local": return
        
        selected_client_id = self.client_map.get(client_name)
        my_device_id = self.controller.get_my_device_id()
        
        if selected_client_id == my_device_id:
             self.auto_start_check.configure(state="normal")
        else:
             self.auto_start_check.configure(state="disabled")
             self.auto_start_var.set("off")

    def _on_ok(self, event=None):
        hostname = self.hostname_entry.get().strip()
        remote_port = self.remote_port_entry.get().strip()
        server_name = self.server_menu.get()
        auto_start = self.auto_start_var.get() == "on"
        
        route_mode = "local" if self.type_switch.get() == "Local VPS Service" else "tunnel"

        # --- Validation ---
        if not hostname:
             ErrorDialog(self, title="Input Error", message="Hostname cannot be empty.")
             return
        if not server_name or server_name == "No servers configured":
             ErrorDialog(self, title="Input Error", message="A server must be selected.")
             return
        if not remote_port.isdigit():
             ErrorDialog(self, title="Input Error", message="Port must be a number.")
             return
        
        server_id = self.servers_map.get(server_name)
        if not server_id:
             ErrorDialog(self, title="Internal Error", message="Could not map server name to an ID.")
             return

        # Prepare Result Dict
        self.result = self.initial_data.copy()
        
        if route_mode == "tunnel":
            local_dest = self.local_dest_entry.get().strip()
            client_name = self.client_menu.get()
            
            if not client_name or client_name == "No devices available":
                 ErrorDialog(self, title="Input Error", message="A client device must be selected.")
                 return
            if not local_dest:
                 ErrorDialog(self, title="Input Error", message="Local Destination cannot be empty.")
                 return
            
            client_device_id = self.client_map.get(client_name)
            self.result.update({
                "local_destination": local_dest,
                "client_device_id": client_device_id,
            })
        else:
            # Local Mode: Clear tunnel-specific fields to avoid confusion
            self.result.update({
                "local_destination": "",
                "client_device_id": self.controller.get_my_device_id(), # Assign to self so we can control it
            })

        # --- Handle auto-start list ---
        my_device_id = self.controller.get_my_device_id()
        auto_start_list = self.initial_data.get("auto_start_on_device_ids", []).copy()
        
        if auto_start: 
            if my_device_id not in auto_start_list:
                 auto_start_list.append(my_device_id)
        else: 
            if my_device_id in auto_start_list:
                 auto_start_list.remove(my_device_id)

        self.result.update({
            "hostname": hostname,
            "server_id": server_id,
            "remote_port": remote_port, # In local mode, this is the App Port
            "auto_start_on_device_ids": auto_start_list,
            "obj_type": "tunnel",
            "extra_ports": self.extra_ports_entry.get().strip(), # public:local pairs (comma separated)
            "route_type": route_mode # New Field
        })
        
        self.grab_release()
        self.destroy()

class InviteDialog(BaseDialog):
    """Displays a Syncthing invite QR code and text."""
    def __init__(self, parent, invite_string: str, title="Invite Device"):
        super().__init__(parent, title=title)
        
        self.invite_string = invite_string
        
        ctk.CTkLabel(self.main_frame, text="Scan this QR code or copy the string to sync another device:").pack(pady=(0, 10))
        
        # --- Generate QR Code ---
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(self.invite_string)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert PIL image to CTkImage
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            qr_image = ctk.CTkImage(Image.open(img_byte_arr), size=(250, 250))
            
            qr_label = ctk.CTkLabel(self.main_frame, image=qr_image, text="")
            qr_label.pack(pady=10)
            
        except Exception as e:
            logging.error(f"Failed to generate QR code: {e}")
            ctk.CTkLabel(self.main_frame, text="Failed to generate QR code.").pack(pady=10)
        
        # --- Invite String Entry ---
        entry_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        entry_frame.pack(fill="x", padx=10, pady=10)
        entry_frame.grid_columnconfigure(0, weight=1)
        
        self.invite_entry = ctk.CTkEntry(entry_frame, width=300)
        self.invite_entry.insert(0, self.invite_string)
        self.invite_entry.configure(state="readonly")
        self.invite_entry.grid(row=0, column=0, sticky="ew")
        
        copy_button = ctk.CTkButton(entry_frame, text="Copy", width=60, command=self._copy_invite)
        copy_button.grid(row=0, column=1, padx=(5, 0))
        self.copy_button = copy_button # Save ref
        
        # --- Close Button ---
        ok_button = ctk.CTkButton(self.main_frame, text="Close", command=self._on_ok)
        ok_button.pack(pady=10)
        ok_button.focus_set()

    def _copy_invite(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.invite_string)
            self.copy_button.configure(text="Copied!")
            self.after(2000, lambda: self.copy_button.configure(text="Copy"))
        except Exception as e:
            logging.error(f"Failed to copy invite string to clipboard: {e}")
            self.copy_button.configure(text="Failed")

class ConfirmationDialog(BaseDialog):
    """A modal dialog to ask for Yes/No confirmation."""
    def __init__(self, parent, title="Confirm?", message="Are you sure?"):
        super().__init__(parent, title=title)
        
        ctk.CTkLabel(self.main_frame, text=message, wraplength=350, justify="left").pack(pady=(0, 20), fill="x")
        
        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=10)
        
        self.yes_button = ctk.CTkButton(button_frame, text="Yes", command=self._on_ok, width=100)
        self.yes_button.pack(side="left", padx=10)
        
        self.no_button = ctk.CTkButton(button_frame, text="No", command=self._on_cancel, width=100,
                                       fg_color="transparent", border_width=1,
                                       text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        self.no_button.pack(side="left", padx=10)
        
        self.bind("<Return>", self._on_ok)
        self.bind("<Escape>", self._on_cancel)
        
        self.yes_button.focus_set()

class RecoveryKeyDialog(BaseDialog):
    """Displays the recovery key and a copy button."""
    def __init__(self, parent, recovery_key: str, title="Recovery Key"):
        super().__init__(parent, title=title)
        
        ctk.CTkLabel(self.main_frame, text="Save this key somewhere safe!\nIt's the only way to recover your account.",
                       wraplength=400, justify="center").pack(pady=(0, 10))
                       
        key_frame = ctk.CTkFrame(self.main_frame, fg_color=("gray90", "gray20"))
        key_frame.pack(fill="x", padx=10, pady=10)
        
        self.key_label = ctk.CTkLabel(key_frame, text=recovery_key, font=("Courier New", 14), wraplength=380, justify="center")
        self.key_label.pack(padx=15, pady=15)
        
        self.copy_button = ctk.CTkButton(self.main_frame, text="Copy to Clipboard", command=self._copy_key)
        self.copy_button.pack(pady=10)
        
        self.ok_button = ctk.CTkButton(self.main_frame, text="OK", command=self._on_ok,
                                        fg_color="transparent", border_width=1,
                                        text_color=ctk.ThemeManager.theme["CTkLabel"]["text_color"])
        self.ok_button.pack(pady=(0, 10))
        
        self.ok_button.focus_set()

    def _copy_key(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.key_label.cget("text"))
            self.copy_button.configure(text="Copied!")
            self.after(2000, lambda: self.copy_button.configure(text="Copy to Clipboard"))
        except Exception as e:
            logging.error(f"Failed to copy recovery key to clipboard: {e}")
            self.copy_button.configure(text="Copy Failed")

class ErrorDialog(BaseDialog):
    """A simple modal dialog to show an error message."""
    def __init__(self, parent, title="Error", message="An error occurred."):
        super().__init__(parent, title=title)
        
        ctk.CTkLabel(self.main_frame, text=message, wraplength=350, justify="left").pack(pady=(0, 20), fill="x")
        
        ok_button = ctk.CTkButton(self.main_frame, text="OK", command=self._on_ok, width=100)
        ok_button.pack(pady=10)
        
        self.bind("<Return>", self._on_ok)
        self.bind("<Escape>", self._on_ok)
        
        ok_button.focus_set()
        