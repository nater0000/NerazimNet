import customtkinter as ctk
import logging
from tkinter import ttk, Menu

from views.tree_style import apply_tree_style, menu_kwargs, _BG, _BG_HEADER, _FG, _FG_DIM


class DashboardView(ctk.CTkFrame):
    """Tunnel list rendered as a native virtualized ttk.Treeview.

    Server names are parent rows; tunnels are children. The list is rebuilt
    only on config sync (rare); the 2s poll only updates cell text in place —
    no widget churn, which is what made the CTkScrollableFrame lag.
    """

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.current_statuses = {}

        apply_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # control frame
        self.grid_rowconfigure(1, weight=0)  # action toolbar
        self.grid_rowconfigure(2, weight=1)  # tree
        self.grid_rowconfigure(3, weight=0)  # legend

        # --- Top Control Frame ---
        self.control_frame = ctk.CTkFrame(self)
        self.control_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        ttk.Button(self.control_frame, text="Add Tunnel", style="primary.TButton",
                   command=self.controller.add_new_tunnel).pack(side="left", padx=5, pady=5)
        start_stop_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        start_stop_frame.pack(side="right", padx=5, pady=5)
        ttk.Button(start_stop_frame, text="Start All", style="success.TButton",
                   command=self.controller.start_all_tunnels).pack(side="left", padx=5)
        ttk.Button(start_stop_frame, text="Stop All", style="danger.TButton",
                   command=self.controller.stop_all_tunnels).pack(side="left", padx=5)

        # --- Action toolbar (acts on the selected tree row) ---
        self.action_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.action_frame.grid(row=1, column=0, padx=10, pady=(6, 0), sticky="ew")
        self.sel_label = ctk.CTkLabel(self.action_frame, text="Select a tunnel",
                                      text_color=_FG_DIM, font=ctk.CTkFont(size=11))
        self.sel_label.pack(side="left", padx=(5, 10))
        self.start_stop_btn = ttk.Button(self.action_frame, text="Start", style="success.TButton",
                                         command=self._act_start_stop, state="disabled")
        self.start_stop_btn.pack(side="left", padx=3)
        self.logs_btn = ttk.Button(self.action_frame, text="Logs", style="secondary.TButton",
                                   command=self._act_logs, state="disabled")
        self.logs_btn.pack(side="left", padx=3)
        self.edit_btn = ttk.Button(self.action_frame, text="Edit", style="secondary.TButton",
                                   command=self._act_edit, state="disabled")
        self.edit_btn.pack(side="left", padx=3)
        self.delete_btn = ttk.Button(self.action_frame, text="Delete", style="danger.TButton",
                                     command=self._act_delete, state="disabled")
        self.delete_btn.pack(side="left", padx=3)

        # --- Treeview ---
        tree_frame = ctk.CTkFrame(self, fg_color=_BG)
        tree_frame.grid(row=2, column=0, padx=10, pady=5, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("status", "route"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Server / Tunnel", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("route", text="Route", anchor="w")
        self.tree.column("#0", width=260, minwidth=160, stretch=True)
        self.tree.column("status", width=220, minwidth=140, stretch=False)
        self.tree.column("route", width=320, minwidth=160, stretch=True)
        self.tree.tag_configure("server", background=_BG_HEADER,
                                foreground=_FG, font=("Segoe UI", 10, "bold"))
        self.tree.tag_configure("disabled", foreground=_FG_DIM)
        self.tree.tag_configure("placeholder", foreground=_FG_DIM)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        # --- Context menu ---
        self._ctx_menu = Menu(self.tree, tearoff=0, **menu_kwargs())
        self._ctx_menu.add_command(label="Start / Stop", command=self._act_start_stop)
        self._ctx_menu.add_command(label="View Logs", command=self._act_logs)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Edit", command=self._act_edit)
        self._ctx_menu.add_command(label="Delete", command=self._act_delete)

        # --- Legend ---
        self.legend_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.legend_frame.grid(row=3, column=0, padx=10, pady=(0, 5), sticky="ew")
        ctk.CTkLabel(self.legend_frame, text="Legend:").pack(side="left", padx=(5, 10))
        legend_items = [
            ("✅", "Running"), ("⚠️", "Error"),
            ("⚪", "Stopped"), ("🔘", "Managed Elsewhere"),
        ]
        for icon, text in legend_items:
            item_frame = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
            item_frame.pack(side="left", padx=5)
            ctk.CTkLabel(item_frame, text=icon, font=ctk.CTkFont(size=14)).pack(side="left")
            ctk.CTkLabel(item_frame, text=text, font=ctk.CTkFont(size=12)).pack(side="left", padx=(2, 0))

        self.status_check_timer = None
        self._server_iids = {}   # server_id -> parent iid
        self._tunnel_iids = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self.sync_tunnel_list()
        self.poll_statuses()

    def on_leave(self):
        if self.status_check_timer:
            try:
                self.after_cancel(self.status_check_timer)
            except ValueError:
                pass
            self.status_check_timer = None

    def poll_statuses(self):
        """2s tick — updates status cells in place only; no rebuild."""
        if not self.winfo_exists() or not self.winfo_viewable():
            self.status_check_timer = None
            return
        self.refresh_tunnel_statuses()
        self.status_check_timer = self.after(2000, self.poll_statuses)

    # ------------------------------------------------------------------
    # Data -> tree
    # ------------------------------------------------------------------

    def _status_text(self, tunnel_id: str) -> str:
        obj = self.current_statuses.get(tunnel_id, {'status': 'stopped', 'message': 'Stopped'})
        key = obj.get('status', 'stopped')
        msg = obj.get('message', 'Stopped')
        icon = {"running": "✅", "error": "⚠️", "disabled": "🔘"}.get(key, "⚪")
        return f"{icon} {msg}"

    def _route_text(self, tunnel: dict) -> str:
        if tunnel.get('route_type', 'tunnel') == 'local':
            return f"VPS service :{tunnel.get('remote_port', '?')}"
        return f"{tunnel.get('local_destination', '?')}  (extra: {tunnel.get('extra_ports') or '—'})"

    def _rebuild_tree(self, tunnels_config: list):
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._server_iids.clear()
        self._tunnel_iids.clear()

        if not tunnels_config:
            self.tree.insert("", "end", iid="__empty__", text="No tunnels configured…",
                             values=("", ""), tags=("placeholder",))
            return

        sorted_tunnels = sorted(tunnels_config, key=lambda t: (
            self.controller.get_server_name(t.get('server_id', '')),
            t.get('hostname', ''),
        ))

        for tunnel in sorted_tunnels:
            tid = tunnel['id']
            sid = tunnel.get('server_id') or ''
            server_name = self.controller.get_server_name(sid)

            if sid not in self._server_iids:
                parent = f"srv::{sid}"
                self.tree.insert("", "end", iid=parent, text=server_name,
                                 values=("", ""), tags=("server",), open=True)
                self._server_iids[sid] = parent

            tags = ()
            status_obj = self.current_statuses.get(tid, {})
            if status_obj.get('status') == 'disabled':
                tags = ("disabled",)
            self.tree.insert(
                self._server_iids[sid], "end", iid=tid,
                text=tunnel.get('hostname', tid),
                values=(self._status_text(tid), self._route_text(tunnel)),
                tags=tags,
            )
            self._tunnel_iids.add(tid)

    def sync_tunnel_list(self):
        """Full rebuild on config change (enter view, add/edit/delete)."""
        try:
            tunnels_config = self.controller.get_tunnels()
            self.current_statuses = self.controller.get_tunnel_statuses()
            self._rebuild_tree(tunnels_config)
            self._update_action_state()
        except Exception as e:
            logging.error(f"Error during tunnel list sync: {e}", exc_info=True)

    def refresh_tunnel_statuses(self):
        """Lightweight poll update: rewrite status cells, fix tags."""
        try:
            self.current_statuses = self.controller.get_tunnel_statuses()
            for tid in self._tunnel_iids:
                if not self.tree.exists(tid):
                    continue
                tunnel = self.controller.get_object_by_id(tid)
                if not tunnel:
                    continue
                tags = ()
                if self.current_statuses.get(tid, {}).get('status') == 'disabled':
                    tags = ("disabled",)
                self.tree.item(tid, values=(self._status_text(tid),
                                            self._route_text(tunnel)),
                               tags=tags)
            self._update_action_state()
        except Exception as e:
            logging.warning(f"Status refresh failed: {e}")

    # ------------------------------------------------------------------
    # Selection + actions
    # ------------------------------------------------------------------

    def _selected_tunnel(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return iid if iid in self._tunnel_iids else None

    def _on_select(self, _event=None):
        self._update_action_state()

    def _update_action_state(self):
        tid = self._selected_tunnel()
        if tid is None:
            self.sel_label.configure(text="Select a tunnel")
            for btn in (self.start_stop_btn, self.logs_btn, self.edit_btn, self.delete_btn):
                btn.configure(state="disabled")
            return
        tunnel = self.controller.get_object_by_id(tid)
        name = tunnel.get('hostname', tid) if tunnel else tid
        self.sel_label.configure(text=name)
        self.logs_btn.configure(state="normal")
        self.edit_btn.configure(state="normal")
        self.delete_btn.configure(state="normal")
        status = self.current_statuses.get(tid, {}).get('status', 'stopped')
        if status == 'disabled':
            self.start_stop_btn.configure(state="disabled", text="Start", style="success.TButton")
        else:
            self.start_stop_btn.configure(
                state="normal",
                text="Stop" if status == 'running' else "Start",
                style="danger.TButton" if status == 'running' else "success.TButton",
            )

    def _on_double_click(self, _event):
        if self._selected_tunnel():
            self._act_start_stop()

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid in self._tunnel_iids:
            self.tree.selection_set(iid)
            self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _act_start_stop(self):
        tid = self._selected_tunnel()
        if not tid:
            return
        status = self.current_statuses.get(tid, {}).get('status', 'stopped')
        if status == 'running':
            self.controller.stop_tunnel(tid)
        else:
            self.controller.start_tunnel(tid)
        self.refresh_tunnel_statuses()

    def _act_logs(self):
        tid = self._selected_tunnel()
        if tid:
            self.controller.view_tunnel_log(tid)

    def _act_edit(self):
        tid = self._selected_tunnel()
        if tid:
            self.controller.edit_tunnel(tid)
            self.sync_tunnel_list()

    def _act_delete(self):
        tid = self._selected_tunnel()
        if tid:
            self.controller.delete_tunnel(tid)
            self.sync_tunnel_list()
