import customtkinter as ctk
import logging
import tkinter as tk
from tkinter import ttk, Menu

from views.tree_style import apply_tree_style, menu_kwargs, update_menu, palette, tk_color, hover_color, ctk_label, BOLD_FONT, TOOLTIP_FONT, TREE_FONT


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
        self.grid_rowconfigure(1, weight=1)  # tree
        self.grid_rowconfigure(2, weight=0)  # legend

        # --- Top Control Frame ---
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        btn_font = ctk.CTkFont(size=16, weight="bold")
        self._role_button(self.control_frame, "+  Add Tunnel", "info",
                          self.controller.add_new_tunnel, width=170,
                          font=btn_font).pack(side="left", padx=5, pady=5)
        start_stop_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        start_stop_frame.pack(side="right", padx=5, pady=5)
        self._role_button(start_stop_frame, "▶  Start All", "success",
                          self.controller.start_all_tunnels, width=140,
                          font=btn_font).pack(side="left", padx=5)
        self._role_button(start_stop_frame, "⏹  Stop All", "danger",
                          self.controller.stop_all_tunnels, width=140,
                          font=btn_font).pack(side="left", padx=5)

        # --- Treeview ---
        tree_frame = ctk.CTkFrame(self, fg_color=tk_color("bg"))
        tree_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("status", "route", "actions"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Server / Tunnel", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("route", text="Route", anchor="w")
        self.tree.heading("actions", text="Actions", anchor="center")
        self.tree.column("#0", width=300, minwidth=160, stretch=True)
        self.tree.column("status", width=300, minwidth=180, stretch=False)
        self.tree.column("route", width=300, minwidth=160, stretch=True)
        self.tree.column("actions", width=170, minwidth=140, stretch=False,
                         anchor="center")
        self._apply_tree_tags()

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<ButtonRelease-1>", self._on_click_release, add="+")
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda _e: self._hide_tooltip())
        self.tree.bind("<<TreeviewOpen>>", lambda _e: self._sync_arrows())
        self.tree.bind("<<TreeviewClose>>", lambda _e: self._sync_arrows())

        # --- Context menu ---
        self._ctx_menu = Menu(self.tree, tearoff=0, **menu_kwargs())

        self._ctx_menu.add_command(label="Start / Stop", command=self._act_start_stop)
        self._ctx_menu.add_command(label="View Logs", command=self._act_logs)
        self._ctx_menu.add_command(label="Copy Public Address", command=self._act_copy_address)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="Edit", command=self._act_edit)
        self._ctx_menu.add_command(label="Delete", command=self._act_delete)

        # --- Legend ---
        self.legend_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.legend_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        ctk_label(self.legend_frame, text="Legend:").pack(side="left", padx=(5, 10))
        # Tk renders emoji monochrome on Windows — use ● tinted via
        # text_color so the legend shows real colors like the row tags.
        legend_items = [
            ("status_ok", "Running"), ("status_warn", "Starting"),
            ("status_err", "Error"), ("fg", "Stopped"),
            ("info", "Managed Elsewhere"),
        ]
        for color_key, text in legend_items:
            item_frame = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
            item_frame.pack(side="left", padx=(8, 4))
            # Fixed-width icon slot — emoji vary in width, so free-packing
            # leaves the labels jagged.
            ctk_label(item_frame, text="●", font=ctk.CTkFont(size=13),
                      text_color=tk_color(color_key), width=18,
                      ).pack(side="left")
            ctk_label(item_frame, text=text, font=ctk.CTkFont(size=12)).pack(side="left", padx=(2, 0))

        self.status_check_timer = None
        self._server_iids = {}   # server_id -> parent iid
        self._server_names = {}  # parent iid -> display name
        self._tunnel_iids = set()
        self._tooltip = None
        self._tooltip_key = None

    def _role_button(self, parent, text, role, command, **kwargs):
        """Colored CTkButton. CTk draws its text on an internal tk.Label —
        and ttkbootstrap's _theme_walk repaints EVERY legacy tk widget on
        theme_use (update_label_style → bg=theme bg), which is the dark
        box behind the button text that hover temporarily masks. Opt the
        button's internals out via ttkbootstrap's own _tb_no_autostyle
        escape hatch, and fix any repaint that slipped in first."""
        btn = ctk.CTkButton(
            parent, text=text, command=command,
            height=48, corner_radius=8,
            fg_color=tk_color(role), hover_color=hover_color(role),
            **kwargs)

        def _protect(tries=25):
            lbl = getattr(btn, '_text_label', None)
            if lbl is None:
                if tries:
                    self.after(50, lambda: _protect(tries - 1))
                return
            for w in (btn, getattr(btn, '_canvas', None), lbl,
                      getattr(btn, '_image_label', None)):
                if w is not None:
                    try:
                        w._tb_no_autostyle = True
                    except Exception:
                        pass
            try:
                lbl.configure(bg=btn._apply_appearance_mode(btn._fg_color))
            except Exception:
                pass
        _protect()  # internals exist right after construction
        return btn

    def _on_click_release(self, event):
        """Click release: dispatch per-row action icons, or toggle a
        server group's open/collapsed state."""
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if col == '#3' and iid in self._tunnel_iids:
            self._dispatch_action(event, iid)
        elif iid in self._server_names:
            self.tree.item(iid, open=not self.tree.item(iid, "open"))

    _ACTION_ORDER = ("toggle", "logs", "edit", "delete")

    def _dispatch_action(self, event, iid):
        """Divide the actions cell into equal zones — one per icon — and
        run the action under the click. Equal zones are more forgiving
        than font-measuring glyphs (emoji metrics vary by platform)."""
        bbox = self.tree.bbox(iid, '#3')
        if not bbox:
            return
        bx, _by, bw, _bh = bbox
        # The cell is center-anchored: measure the icon string so clicks in
        # the cell's empty padding don't fire edge actions (e.g. Delete).
        try:
            import tkinter.font as tkfont
            tw = tkfont.nametofont(TREE_FONT).measure(
                self.tree.set(iid, 'actions'))
        except Exception:
            tw = 0
        if 0 < tw < bw:
            start = bx + (bw - tw) / 2
            if event.x < start - 4 or event.x > start + tw + 4:
                return
            zone = int((event.x - start) / (tw / len(self._ACTION_ORDER)))
        else:
            zone = int((event.x - bx) / (bw / len(self._ACTION_ORDER)))
        zone = max(0, min(len(self._ACTION_ORDER) - 1, zone))
        action = self._ACTION_ORDER[zone]
        self.tree.selection_set(iid)
        if action == "toggle":
            self._act_start_stop(iid)
        elif action == "logs":
            self._act_logs(iid)
        elif action == "edit":
            self._act_edit(iid)
        elif action == "delete":
            self._act_delete(iid)

    def _sync_arrows(self):
        """Refresh the ▾/▸ prefix on server rows to match open state."""
        for iid, name in self._server_names.items():
            if self.tree.exists(iid):
                arrow = "▾" if self.tree.item(iid, "open") else "▸"
                self.tree.item(iid, text=f"{arrow} {name}")

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

    def _status_key(self, tunnel_id: str) -> str:
        """Effective status for coloring: running/starting/error/
        managed/stopped. Transitional messages ride on the stopped key."""
        obj = self.current_statuses.get(tunnel_id, {})
        key = obj.get('status', 'stopped')
        if key in ('running', 'error'):
            return key
        if key == 'disabled':
            return 'managed'
        if key == 'starting' or (
                key == 'stopped' and obj.get('message') not in (None, 'Stopped')):
            return 'starting'
        return 'stopped'

    def _status_text(self, tunnel_id: str) -> str:
        obj = self.current_statuses.get(tunnel_id, {'status': 'stopped', 'message': 'Stopped'})
        msg = obj.get('message', 'Stopped')
        # ● is tinted by the row's status tag — Tk emoji are monochrome.
        return f"● {msg}"

    def _actions_text(self, tunnel_id: str) -> str:
        """Per-row action icons: start/stop toggle, logs, edit, delete.
        Tunnels managed elsewhere get no start/stop icon."""
        obj = self.current_statuses.get(tunnel_id, {})
        toggle = "·" if obj.get('status') == 'disabled' else (
            "⏹" if obj.get('status') == 'running' else "▶")
        return f"{toggle}     📋     ✎     ✖"

    def _route_text(self, tunnel: dict) -> str:
        if tunnel.get('route_type', 'tunnel') == 'local':
            return f"VPS service :{tunnel.get('remote_port', '?')}"
        dest = tunnel.get('local_destination', '?')
        port = tunnel.get('remote_port')
        base = f"{port} → {dest}" if port else dest
        n = len(self._extra_ports(tunnel))
        return f"{base}  (+{n} extra)" if n else base

    @staticmethod
    def _extra_ports(tunnel: dict) -> list:
        return [e.strip() for e in (tunnel.get('extra_ports') or '').split(',')
                if e.strip()]

    # --- Cell hover tooltip (shows extra_ports details on the route column) ---
    def _hide_tooltip(self):
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None
        self._tooltip_key = None

    def _on_tree_motion(self, event):
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        # Hand cursor over the actions cell — hints the icons are clickable
        cursor = "hand2" if (col == '#3' and iid in self._tunnel_iids) else ""
        if self.tree.cget("cursor") != cursor:
            self.tree.configure(cursor=cursor)
        extras = []
        if col == '#2' and iid in self._tunnel_iids:
            tunnel = self.controller.get_object_by_id(iid) or {}
            extras = self._extra_ports(tunnel)
        if not extras:
            self._hide_tooltip()
            return
        detail = "Extra ports:\n" + "\n".join(extras)
        if self._tooltip_key == iid:
            if self._tooltip is not None:
                self._tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 12}")
            return
        self._tooltip_key = iid
        if self._tooltip is None:
            self._tooltip = tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            pal = palette()
            self._tooltip_lbl = tk.Label(
                tw, justify="left", bg=pal["bg_header"], fg=pal["fg"],
                padx=10, pady=6, font=TOOLTIP_FONT,
                highlightthickness=1, highlightbackground=pal["border"])
            self._tooltip_lbl.pack()
        self._tooltip_lbl.configure(text=detail)
        self._tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 12}")

    def _rebuild_tree(self, tunnels_config: list):
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._server_iids.clear()
        self._server_names.clear()
        self._tunnel_iids.clear()

        if not tunnels_config:
            self.tree.insert("", "end", iid="__empty__", text="No tunnels configured…",
                             values=("", "", ""), tags=("placeholder",))
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
                self.tree.insert("", "end", iid=parent, text=f"▾ {server_name}",
                                 values=("", "", ""), tags=("server",), open=True)
                self._server_iids[sid] = parent
                self._server_names[parent] = server_name

            tags = []
            status_key = self._status_key(tid)
            if status_key != 'stopped':
                tags.append(f"st_{status_key}")
            if len(self.tree.get_children(self._server_iids[sid])) % 2 == 1:
                tags.append("stripe")
            self.tree.insert(
                self._server_iids[sid], "end", iid=tid,
                text=tunnel.get('hostname', tid),
                values=(self._status_text(tid), self._route_text(tunnel),
                        self._actions_text(tid)),
                tags=tuple(tags),
            )
            self._tunnel_iids.add(tid)

        # Server group rows show their tunnel count.
        for parent, name in self._server_names.items():
            n = len(self.tree.get_children(parent))
            self._server_names[parent] = f"{name} ({n})"
            self.tree.item(parent, text=f"▾ {name} ({n})")

    def sync_tunnel_list(self):
        """Full rebuild on config change (enter view, add/edit/delete)."""
        try:
            tunnels_config = self.controller.get_tunnels()
            self.current_statuses = self.controller.get_tunnel_statuses()
            self._rebuild_tree(tunnels_config)
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
                tags = {t for t in self.tree.item(tid, 'tags')
                        if not t.startswith('st_')}
                status_key = self._status_key(tid)
                if status_key != 'stopped':
                    tags.add(f"st_{status_key}")
                self.tree.item(tid, values=(self._status_text(tid),
                                            self._route_text(tunnel),
                                            self._actions_text(tid)),
                               tags=tuple(tags))
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

    def _on_double_click(self, event):
        # Ignore double-clicks on the actions cell — single click already
        # fired the action there.
        if self.tree.identify_column(event.x) == '#3':
            return
        if self._selected_tunnel():
            self._act_start_stop()

    def _apply_tree_tags(self):
        pal = palette()
        self.tree.tag_configure("server", background=pal["bg_header"],
                                foreground=pal["fg"], font=BOLD_FONT)
        self.tree.tag_configure("st_running", foreground=pal["status_ok"])
        self.tree.tag_configure("st_starting", foreground=pal["status_warn"])
        self.tree.tag_configure("st_error", foreground=pal["status_err"])
        self.tree.tag_configure("st_managed", foreground=pal["info"])
        self.tree.tag_configure("placeholder", foreground=pal["fg_dim"])
        self.tree.tag_configure("stripe", background=pal["bg_alt"])

    def on_theme_changed(self):
        self._apply_tree_tags()
        update_menu(self._ctx_menu)
        self._hide_tooltip()

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid in self._tunnel_iids:
            self.tree.selection_set(iid)
            self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _act_start_stop(self, tid=None):
        tid = tid or self._selected_tunnel()
        if not tid:
            return
        status = self.current_statuses.get(tid, {}).get('status', 'stopped')
        if status == 'disabled':
            return  # managed elsewhere — the · slot is a no-op
        if status == 'running':
            self.controller.stop_tunnel(tid)
        else:
            self.controller.start_tunnel(tid)
        self.refresh_tunnel_statuses()

    def _act_logs(self, tid=None):
        tid = tid or self._selected_tunnel()
        if tid:
            self.controller.view_tunnel_log(tid)

    def _act_copy_address(self, tid=None):
        tid = tid or self._selected_tunnel()
        if tid:
            self.controller.copy_tunnel_address(tid)

    def _act_edit(self, tid=None):
        tid = tid or self._selected_tunnel()
        if tid:
            self.controller.edit_tunnel(tid)
            self.sync_tunnel_list()

    def _act_delete(self, tid=None):
        tid = tid or self._selected_tunnel()
        if tid:
            self.controller.delete_tunnel(tid)
            self.sync_tunnel_list()
