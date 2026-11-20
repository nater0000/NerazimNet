import customtkinter as ctk
import json
import logging
from tkinter import ttk

from utils.version import get_version
from views.tree_style import apply_tree_style, palette, tk_color


class DebugView(ctk.CTkFrame):
    """
    Developer view for inspecting raw in-memory configuration state.
    Object list is a native ttk.Treeview; JSON detail stays in a CTkTextbox.
    """

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self._objects = {}

        apply_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.version_label = ctk.CTkLabel(
            self, text=f"NerazimNet v{get_version()}",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        self.version_label.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")

        self._paned = ttk.PanedWindow(self, orient="horizontal")
        self._paned.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")

        # --- Left: object list (Treeview) ---
        list_frame = ctk.CTkFrame(self._paned, fg_color=tk_color("bg"))
        self._paned.add(list_frame, weight=1)
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(list_frame, text="Config Objects",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=(8, 4), sticky="w")

        self.object_tree = ttk.Treeview(list_frame, show="tree", selectmode="browse")
        self.object_tree.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
        self.object_tree.bind("<<TreeviewSelect>>", self._on_select)
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.object_tree.yview)
        self.object_tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns")

        # --- Right: JSON display ---
        self.json_display = ctk.CTkTextbox(self._paned, wrap="none", font=("Courier New", 14))
        self._paned.add(self.json_display, weight=3)
        self.json_display.configure(state="disabled")

    def on_theme_changed(self):
        self.object_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])

    def on_enter(self):
        logging.info("Entering Debug View.")
        for iid in self.object_tree.get_children(""):
            self.object_tree.delete(iid)
        self._clear_json_display()

        try:
            self._objects = self.controller.get_all_objects_for_debug()

            if not self._objects:
                self.object_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])
                self.object_tree.insert("", "end", iid="__empty__",
                                        text="No objects in memory.",
                                        tags=("placeholder",))
                return

            sorted_items = sorted(
                self._objects.items(),
                key=lambda item: (
                    item[1].get('type', 'Unknown'),
                    item[1].get('hostname', item[1].get('name', item[0]))))

            for obj_id, obj_data in sorted_items:
                obj_type = obj_data.get('type', 'Unknown')
                name = obj_data.get('hostname') or obj_data.get('name') or obj_id[:8]
                self.object_tree.insert("", "end", iid=obj_id,
                                        text=f"{obj_type.title()}: {name}")
        except Exception as e:
            logging.error(f"Error populating debug object list: {e}", exc_info=True)

    def _clear_json_display(self):
        if self.json_display.winfo_exists():
            self.json_display.configure(state="normal")
            self.json_display.delete("1.0", "end")
            self.json_display.configure(state="disabled")

    def _on_select(self, _event=None):
        sel = self.object_tree.selection()
        if not sel or sel[0] == "__empty__":
            return
        obj = self._objects.get(sel[0])
        if obj:
            self._show_object_details(obj)

    def _show_object_details(self, obj_data: dict):
        try:
            self.json_display.configure(state="normal")
            self.json_display.delete("1.0", "end")
            self.json_display.insert("1.0", json.dumps(obj_data, indent=2, sort_keys=True))
            self.json_display.configure(state="disabled")
        except Exception as e:
            logging.error(f"Error displaying object details: {e}", exc_info=True)
            self.json_display.configure(state="normal")
            self.json_display.delete("1.0", "end")
            self.json_display.insert("1.0", f"Error displaying JSON:\n{e}")
            self.json_display.configure(state="disabled")
