import customtkinter as ctk
import logging
from tkinter import ttk
from views.tree_style import apply_tree_style, palette, tk_color


class HistoryView(ctk.CTkFrame):
    """
    Version history browser: file list -> version timeline -> content viewer.
    All three panes are native widgets (ttk.Treeview + CTkTextbox for content).
    """

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller

        apply_tree_style()

        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._version_timestamps = []  # iid -> timestamp map for timeline rows

        # --- File browser (left) ---
        files_frame = ctk.CTkFrame(self, fg_color=tk_color("bg"))
        files_frame.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="nsew")
        files_frame.grid_rowconfigure(1, weight=1)
        files_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(files_frame, text="Configuration Files",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=(8, 4), sticky="w")
        self.file_tree = ttk.Treeview(files_frame, show="tree", selectmode="browse")
        self.file_tree.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_select)
        file_vsb = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_vsb.set)
        file_vsb.grid(row=1, column=1, sticky="ns")

        # --- Version timeline (middle) ---
        timeline_frame = ctk.CTkFrame(self, fg_color=tk_color("bg"))
        timeline_frame.grid(row=0, column=1, padx=5, pady=10, sticky="nsew")
        timeline_frame.grid_rowconfigure(1, weight=1)
        timeline_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(timeline_frame, text="File History",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=8, pady=(8, 4), sticky="w")
        self.version_tree = ttk.Treeview(timeline_frame, show="tree", selectmode="browse")
        self.version_tree.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
        self.version_tree.bind("<<TreeviewSelect>>", self._on_version_select)
        ver_vsb = ttk.Scrollbar(timeline_frame, orient="vertical", command=self.version_tree.yview)
        self.version_tree.configure(yscrollcommand=ver_vsb.set)
        ver_vsb.grid(row=1, column=1, sticky="ns")

        # --- Content viewer (right, initially hidden) ---
        self.content_viewer = ctk.CTkTextbox(self, state="disabled", wrap="none",
                                             font=("Courier New", 14))
        # gridded in _on_version_select

        self._selected_file_id = None

    def on_theme_changed(self):
        self.file_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])
        self.version_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])

    def on_enter(self):
        self._populate_file_browser()
        self._clear_timeline()
        self._clear_content_viewer()

    # ------------------------------------------------------------------
    # File browser
    # ------------------------------------------------------------------

    def _populate_file_browser(self):
        for iid in self.file_tree.get_children(""):
            self.file_tree.delete(iid)
        try:
            file_index = self.controller.get_history_file_index()
            if not file_index:
                self.file_tree.insert("", "end", text="No configuration history found.",
                                      tags=("placeholder",))
                self.file_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])
                return

            for file_item in sorted(file_index, key=lambda x: x.get('name', '')):
                file_id = file_item.get('id')
                if not file_id:
                    logging.warning(f"Skipping file item with no ID: {file_item}")
                    continue
                self.file_tree.insert("", "end", iid=file_id,
                                      text=file_item.get('name', 'Unnamed File'))
        except Exception as e:
            logging.error(f"Error populating file browser: {e}", exc_info=True)

    def _clear_timeline(self):
        for iid in self.version_tree.get_children(""):
            self.version_tree.delete(iid)
        self._version_timestamps = []

    def _clear_content_viewer(self):
        if self.content_viewer.winfo_exists():
            self.content_viewer.configure(state="normal")
            self.content_viewer.delete("1.0", "end")
            self.content_viewer.configure(state="disabled")
            self.content_viewer.grid_forget()

    # ------------------------------------------------------------------
    # Timeline + content
    # ------------------------------------------------------------------

    def _on_file_select(self, _event=None):
        sel = self.file_tree.selection()
        if not sel or sel[0] == "__empty__":
            return
        file_id = sel[0]
        self._selected_file_id = file_id
        self._clear_timeline()
        self._clear_content_viewer()

        try:
            version_history = self.controller.get_file_version_history(file_id)
            if not version_history:
                self.version_tree.insert("", "end", text="No versions found.",
                                         tags=("placeholder",))
                self.version_tree.tag_configure("placeholder", foreground=palette()["fg_dim"])
                return

            # Most recent first; iid indexes into _version_timestamps
            for idx, version in enumerate(reversed(version_history)):
                timestamp = version.get('timestamp')
                action = version.get('action', 'Modified')
                if not timestamp:
                    continue
                self._version_timestamps.append(timestamp)
                self.version_tree.insert(
                    "", "end", iid=str(idx),
                    text=f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}  ({action})")
        except Exception as e:
            logging.error(f"Error loading version history for {file_id}: {e}", exc_info=True)

    def _on_version_select(self, _event=None):
        sel = self.version_tree.selection()
        if not sel or self._selected_file_id is None:
            return
        try:
            idx = int(sel[0])
            timestamp = self._version_timestamps[idx]
        except (ValueError, IndexError):
            return

        try:
            content = self.controller.get_file_content_at_version(
                self._selected_file_id, timestamp)
            content = content or "[No content found for this version]"

            self.content_viewer.configure(state="normal")
            self.content_viewer.delete("1.0", "end")
            self.content_viewer.insert("1.0", content)
            self.content_viewer.configure(state="disabled")
            self.content_viewer.grid(row=0, column=2, padx=(5, 10), pady=10, sticky="nsew")
        except Exception as e:
            logging.error(f"Error loading content: {e}", exc_info=True)
            self.content_viewer.configure(state="normal")
            self.content_viewer.delete("1.0", "end")
            self.content_viewer.insert("1.0", f"Error loading content:\n{e}")
            self.content_viewer.configure(state="disabled")
