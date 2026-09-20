import customtkinter as ctk
import json
import logging # Added logging

from utils.version import get_version

class DebugView(ctk.CTkFrame):
    """
    A view for developers to inspect the raw in-memory configuration state.
    """
    def __init__(self, parent, controller):
        # Set background to transparent to blend with the main content area
        super().__init__(parent, fg_color="transparent")
        self.controller = controller

        # Configure grid layout: two columns, right one expands
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0) # Version label, fixed height
        self.grid_rowconfigure(1, weight=1) # Row containing list and display expands vertically

        # --- Version label at top ---
        self.version_label = ctk.CTkLabel(self, text=f"NerazimNet v{get_version()}", font=("Arial", 14, "bold"))
        self.version_label.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 0), sticky="w")

        # --- Left Frame for Object List ---
        self.object_list_frame = ctk.CTkScrollableFrame(self, label_text="Config Objects")
        self.object_list_frame.grid(row=1, column=0, padx=(10, 5), pady=10, sticky="nsew") # Added horizontal padding

        # --- Right Frame for JSON Display ---
        self.json_display = ctk.CTkTextbox(self, wrap="none", font=("Courier New", 12)) # Changed wrap to "none"
        self.json_display.grid(row=1, column=1, padx=(5, 10), pady=10, sticky="nsew") # Added horizontal padding
        self.json_display.configure(state="disabled") # Start read-only

    def on_enter(self):
        """Called when the view is shown. Populates the object list."""
        logging.info("Entering Debug View.")
        # Clear previous widgets in the list frame
        for widget in self.object_list_frame.winfo_children():
            widget.destroy()
        # Clear the JSON display
        self._clear_json_display()

        try:
            all_objects = self.controller.get_all_objects_for_debug()

            if not all_objects:
                ctk.CTkLabel(self.object_list_frame, text="No objects in memory.").pack(padx=10, pady=10)
                return

            # Sort objects by type and then name/ID for consistent order
            sorted_items = sorted(all_objects.items(), key=lambda item: (item[1].get('type', 'Unknown'), item[1].get('hostname', item[1].get('name', item[0]))))

            for obj_id, obj_data in sorted_items:
                # Determine a display name for the button
                obj_type = obj_data.get('type', 'Unknown')
                # Prioritize hostname, then name, then fallback to shortened ID
                name = obj_data.get('hostname') or obj_data.get('name') or obj_id[:8]
                display_text = f"{obj_type.title()}: {name}"

                btn = ctk.CTkButton(
                    self.object_list_frame,
                    text=display_text,
                    anchor="w", # Align text to the left
                    command=lambda o=obj_data: self._show_object_details(o)
                )
                btn.pack(fill="x", padx=5, pady=(0, 5)) # Add spacing below buttons

        except Exception as e:
            logging.error(f"Error populating debug object list: {e}", exc_info=True)
            ctk.CTkLabel(self.object_list_frame, text="Error loading objects.", text_color="red").pack(padx=10, pady=10)

    def _clear_json_display(self):
        """Clears the JSON display text box."""
        if self.json_display.winfo_exists():
            self.json_display.configure(state="normal")
            self.json_display.delete("1.0", "end")
            self.json_display.configure(state="disabled")


    def _show_object_details(self, obj_data: dict):
        """Displays the formatted JSON for a selected object."""
        try:
            self.json_display.configure(state="normal") # Enable writing
            self.json_display.delete("1.0", "end") # Clear previous content
            pretty_json = json.dumps(obj_data, indent=2, sort_keys=True) # Sort keys for readability
            self.json_display.insert("1.0", pretty_json)
            self.json_display.configure(state="disabled") # Make read-only again
        except Exception as e:
            logging.error(f"Error displaying object details: {e}", exc_info=True)
            self.json_display.configure(state="normal")
            self.json_display.delete("1.0", "end")
            self.json_display.insert("1.0", f"Error displaying JSON:\n{e}")
            self.json_display.configure(state="disabled")