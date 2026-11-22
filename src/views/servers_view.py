import customtkinter as ctk
import logging
from tkinter import ttk, Menu
from views.tree_style import apply_tree_style, menu_kwargs, menu_icon, update_menu, palette, tk_color


class ServersView(ctk.CTkFrame):
    """Server list as a native virtualized ttk.Treeview.

    Rows are servers; actions (Provision / Sync Routes / Edit / Delete)
    run against the selected row from the toolbar or right-click menu.
    """

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller

        apply_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # control frame
        self.grid_rowconfigure(1, weight=0)  # action toolbar
        self.grid_rowconfigure(2, weight=1)  # tree

        # --- Top Control Frame ---
        self.control_frame = ctk.CTkFrame(self)
        self.control_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        ttk.Button(self.control_frame, text="Add Server", style="primary.TButton",
                   command=self.controller.add_new_server).pack(side="left", padx=5, pady=5)

        # --- Action toolbar ---
        self.action_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.action_frame.grid(row=1, column=0, padx=10, pady=(6, 0), sticky="ew")
        self.sel_label = ctk.CTkLabel(self.action_frame, text="Select a server",
                                      text_color=tk_color("fg_dim"), font=ctk.CTkFont(size=14))
        self.sel_label.pack(side="left", padx=(5, 10))
        act_btn_frame = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        act_btn_frame.pack(side="right", padx=5)
        self.provision_btn = ttk.Button(act_btn_frame, text="Provision", style="primary.TButton",
                                        command=self._act_provision, state="disabled")
        self.provision_btn.pack(side="left", padx=3)
        self.sync_btn = ttk.Button(act_btn_frame, text="Sync Routes", style="info.TButton",
                                   command=self._act_sync, state="disabled")
        self.sync_btn.pack(side="left", padx=3)
        self.edit_btn = ttk.Button(act_btn_frame, text="Edit", style="secondary.TButton",
                                   command=self._act_edit, state="disabled")
        self.edit_btn.pack(side="left", padx=3)
        self.delete_btn = ttk.Button(act_btn_frame, text="Delete", style="danger.TButton",
                                     command=self._act_delete, state="disabled")
        self.delete_btn.pack(side="left", padx=3)

        # --- Treeview ---
        tree_frame = ctk.CTkFrame(self, fg_color=tk_color("bg"))
        tree_frame.grid(row=2, column=0, padx=10, pady=10, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("status", "ip", "admin"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("ip", text="Name / Address", anchor="w")
        self.tree.heading("admin", text="Admin User", anchor="w")
        self.tree.column("status", width=140, minwidth=110, stretch=False)
        self.tree.column("ip", width=380, minwidth=200, stretch=True)
        self.tree.column("admin", width=140, minwidth=100, stretch=False)
        self._apply_tree_tags()

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)

        self._ctx_menu = Menu(self.tree, tearoff=0, **menu_kwargs())
        def _item(label, command, icon):
            fg = palette()["fg"]
            img = menu_icon(icon, color=fg)
            kw = {"image": img, "compound": "left"} if img else {}
            self._ctx_menu.add_command(label=f"  {label}   ", command=command, **kw)
        _item("Provision / Re-run Setup", self._act_provision, "setup")
        _item("Sync Routes", self._act_sync, "sync")
        self._ctx_menu.add_separator()
        _item("Edit", self._act_edit, "edit")
        _item("Delete", self._act_delete, "delete")

        self._server_iids = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self.load_servers()

    def on_leave(self):
        pass

    # ------------------------------------------------------------------
    # Data -> tree
    # ------------------------------------------------------------------

    def load_servers(self):
        try:
            for iid in self.tree.get_children(""):
                self.tree.delete(iid)
            self._server_iids.clear()

            servers = self.controller.get_servers()
            if not servers:
                self.tree.insert("", "end", iid="__empty__",
                                 values=("", "No servers registered yet. Click 'Add Server' to begin.", ""),
                                 tags=("placeholder",))
                self._update_action_state()
                return

            for server in sorted(servers, key=lambda s: s.get('name', '')):
                sid = server.get('id')
                if not sid:
                    continue
                is_ready = server.get('is_provisioned', False)
                status = "✅ Ready" if is_ready else "⚠️ Setup Needed"
                tag = "ready" if is_ready else "pending"
                name_ip = f"{server.get('name', 'Unnamed')}  ({server.get('ip_address', 'No IP')})"
                self.tree.insert("", "end", iid=sid,
                                 values=(status, name_ip, server.get('admin_user', '')),
                                 tags=(tag,))
                self._server_iids.add(sid)

            self._update_action_state()
        except Exception as e:
            logging.error(f"Critical error during load_servers: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Selection + actions
    # ------------------------------------------------------------------

    def _selected_server(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0] if sel[0] in self._server_iids else None

    def _on_select(self, _event=None):
        self._update_action_state()

    def _update_action_state(self):
        sid = self._selected_server()
        if sid is None:
            self.sel_label.configure(text="Select a server")
            for btn in (self.provision_btn, self.sync_btn, self.edit_btn, self.delete_btn):
                btn.configure(state="disabled")
            return
        server = self.controller.get_object_by_id(sid)
        name = server.get('name', sid) if server else sid
        self.sel_label.configure(text=name)
        self.provision_btn.configure(state="normal")
        self.sync_btn.configure(state="normal")
        self.edit_btn.configure(state="normal")
        self.delete_btn.configure(state="normal")

    def _apply_tree_tags(self):
        pal = palette()
        self.tree.tag_configure("ready", foreground=pal["status_ok"])
        self.tree.tag_configure("pending", foreground=pal["status_warn"])
        self.tree.tag_configure("placeholder", foreground=pal["fg_dim"])

    def on_theme_changed(self):
        self._apply_tree_tags()
        update_menu(self._ctx_menu)

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid in self._server_iids:
            self.tree.selection_set(iid)
            self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _act_provision(self):
        sid = self._selected_server()
        if not sid:
            return
        server = self.controller.get_object_by_id(sid)
        if server:
            self.controller.provision_server(server)

    def _act_sync(self):
        sid = self._selected_server()
        if sid:
            self.controller.sync_server_routes(sid)

    def _act_edit(self):
        sid = self._selected_server()
        if sid:
            self.controller.edit_server(sid)
            self.load_servers()

    def _act_delete(self):
        sid = self._selected_server()
        if sid:
            self.controller.delete_server(sid)
            self.load_servers()
