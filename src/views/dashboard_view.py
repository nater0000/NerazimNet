import customtkinter as ctk
import logging
import tkinter as tk
from tkinter import ttk, Menu

from views.tree_style import apply_tree_style, menu_kwargs, menu_icon, update_menu, palette, tk_color, hover_color, ctk_label, BOLD_FONT, TOOLTIP_FONT


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
        self._btn_font_lg = ctk.CTkFont(size=16, weight="bold")
        self._btn_font_sm = ctk.CTkFont(size=13, weight="bold")
        # (btn, wide_text, compact_text, icon_text, wide_w, compact_w, icon_w)
        self._toolbar_btns = []
        self._tier = None  # 2=wide, 1=compact, 0=icon-only
        self._legend_word_labels = []
        self._legend_title = None
        self._hint_lbl = None

        add_btn = self._role_button(
            self.control_frame, "+  Add Tunnel", "info",
            self.controller.add_new_tunnel, width=170,
            font=self._btn_font_lg)
        add_btn.pack(side="left", padx=5, pady=5)
        self._toolbar_btns.append((add_btn, "+  Add Tunnel", "+ Add", "+", 170, 95, 46))

        # Pack order decides who starves when the window shrinks: the
        # action buttons claim space first, the summary next, and the
        # filter box (packed last with expand) absorbs all shrinkage.
        # _on_resize_compact re-tiers the controls deterministically.
        start_stop_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        start_stop_frame.pack(side="right", padx=5, pady=5)
        start_btn = self._role_button(
            start_stop_frame, "▶  Start All", "success",
            self.controller.start_all_tunnels, width=140,
            font=self._btn_font_lg)
        start_btn.pack(side="left", padx=5)
        stop_btn = self._role_button(
            start_stop_frame, "⏹  Stop All", "danger",
            self.controller.stop_all_tunnels, width=140,
            font=self._btn_font_lg)
        stop_btn.pack(side="left", padx=5)
        self._toolbar_btns.append((start_btn, "▶  Start All", "▶ Start", "▶", 140, 90, 46))
        self._toolbar_btns.append((stop_btn, "⏹  Stop All", "⏹ Stop", "⏹", 140, 85, 46))

        self._summary_lbl = ctk_label(
            self.control_frame, text="", font=ctk.CTkFont(size=13))
        self._summary_lbl.pack(side="right", padx=10)

        # Live filter — narrows the tree as you type. The spacer (not the
        # entry) absorbs slack and shrinkage, so the box keeps a usable
        # size at every width instead of ballooning or vanishing.
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self.sync_tunnel_list())
        self._search = ctk.CTkEntry(
            self.control_frame, textvariable=self._filter_var,
            placeholder_text="Filter tunnels…", width=240, height=40)
        self._search.pack(side="left", padx=(20, 10))
        self._toolbar_spacer = ctk.CTkFrame(
            self.control_frame, fg_color="transparent", height=1, width=1)
        self._toolbar_spacer.pack(side="left", fill="x", expand=True)

        self.bind("<Configure>", self._on_resize_compact)

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
        # Column-sort state; heading clicks toggle direction.
        self._sort_col = "name"
        self._sort_desc = False
        self.tree.heading("#0", text="Server / Tunnel", anchor="w",
                          command=lambda: self._sort_by("name"))
        self.tree.heading("status", text="Status", anchor="w",
                          command=lambda: self._sort_by("status"))
        self.tree.heading("route", text="Route", anchor="w",
                          command=lambda: self._sort_by("route"))
        self.tree.heading("actions", text="", anchor="center")
        self.tree.column("#0", width=340, minwidth=160, stretch=True)
        self.tree.column("status", width=300, minwidth=180, stretch=False)
        self.tree.column("route", width=380, minwidth=160, stretch=True)
        self.tree.column("actions", width=120, minwidth=100,
                         stretch=False, anchor="center")
        self._apply_tree_tags()
        self._sync_sort_headings()

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

        # --- Context menu (row actions live here, colored for scannability) ---
        self._ctx_menu = Menu(self.tree, tearoff=0, **menu_kwargs())
        self._ctx_fg = {}
        self._ctx_item("Start / Stop", self._act_start_stop, "status_ok", "start")
        self._ctx_item("View Logs", self._act_logs, "info", "logs")
        self._ctx_item("Copy Public Address", self._act_copy_address, "info", "copy")
        self._ctx_menu.add_separator()
        self._ctx_item("Edit", self._act_edit, None, "edit")
        self._ctx_item("Delete", self._act_delete, "status_err", "delete")

        # --- Legend ---
        self.legend_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.legend_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky="ew")
        self._legend_title = ctk_label(self.legend_frame, text="Legend:")
        self._legend_title.pack(side="left", padx=(5, 10))
        # Tk renders emoji monochrome on Windows — use ● tinted via
        # text_color so the legend shows real colors like the row tags.
        self._legend_word_labels = []
        legend_items = [
            ("status_ok", "Running", "Run"), ("status_warn", "Starting", "Start"),
            ("status_err", "Error", "Err"), ("fg", "Stopped", "Stop"),
            ("info", "Managed Elsewhere", "Managed"),
        ]
        for color_key, text, short in legend_items:
            item_frame = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
            item_frame.pack(side="left", padx=(8, 4))
            # Fixed-width icon slot — emoji vary in width, so free-packing
            # leaves the labels jagged.
            ctk_label(item_frame, text="●", font=ctk.CTkFont(size=13),
                      text_color=tk_color(color_key), width=18,
                      ).pack(side="left")
            lbl = ctk_label(item_frame, text=text, font=ctk.CTkFont(size=12))
            lbl.pack(side="left", padx=(2, 0))
            self._legend_word_labels.append((lbl, text, short))

        # Discoverability hint for the now-hidden row actions.
        self._hint_lbl = ctk_label(
            self.legend_frame,
            text="Double-click toggles  ·  Right-click for actions",
            font=ctk.CTkFont(size=11),
            text_color=tk_color("fg_dim"))
        self._hint_lbl.pack(side="right", padx=10)

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
            text_color=("#FFFFFF", "#FFFFFF"),
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

    def _ctx_item(self, label, command, color_key, icon=None):
        """Menu entry with a real icon column (image+compound aligns
        text — glyph prefixes have variable widths) and per-item color."""
        idx = self._ctx_menu.index("end")
        idx = 0 if idx is None else idx + 1
        fg = palette()[color_key] if color_key else palette()["fg"]
        kwargs = {}
        img = menu_icon(icon, color=fg) if icon else None
        if img is not None:
            kwargs.update(image=img, compound="left")
        self._ctx_menu.add_command(label=f"  {label}   ", command=command,
                                   foreground=fg, **kwargs)
        if color_key:
            self._ctx_fg[idx] = color_key

    _SORT_HEADINGS = {"name": "#0", "status": "status", "route": "route"}
    _SORT_LABELS = {"name": "Server / Tunnel", "status": "Status",
                    "route": "Route"}
    _STATUS_RANK = {"running": 0, "starting": 1, "error": 2,
                    "managed": 3, "stopped": 4}

    def _sort_by(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self._sync_sort_headings()
        self.sync_tunnel_list()

    def _sync_sort_headings(self):
        for col, cid in self._SORT_HEADINGS.items():
            arrow = ("  ▼" if self._sort_desc else "  ▲") if col == self._sort_col else ""
            self.tree.heading(cid, text=self._SORT_LABELS[col] + arrow)

    def _sort_key(self, tunnel):
        """Sort tunnels within their server group; groups stay alpha."""
        server = self.controller.get_server_name(tunnel.get('server_id', ''))
        if self._sort_col == "status":
            rank = self._STATUS_RANK.get(self._status_key(tunnel['id']), 5)
            return (server, rank, tunnel.get('hostname', ''))
        if self._sort_col == "route":
            try:
                port = int(tunnel.get('remote_port') or 0)
            except (TypeError, ValueError):
                port = 0
            return (server, port, tunnel.get('hostname', ''))
        return (server, tunnel.get('hostname', '').lower())

    def _on_click_release(self, event):
        """Click release: action glyphs dispatch; server rows toggle."""
        iid = self.tree.identify_row(event.y)
        if (iid in self._tunnel_iids
                and self.tree.identify_column(event.x) == '#3'):
            self._act_hit(iid, event.x)
            return
        if iid in self._server_names:
            self.tree.item(iid, open=not self.tree.item(iid, "open"))

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
        if not self.winfo_exists():
            self.status_check_timer = None
            return
        # Keep the chain armed while unmapped: on_enter runs before
        # tkraise(), so the first tick would otherwise see
        # winfo_viewable()==0, nil the timer and never poll again —
        # leaving the tree frozen on its initial paint. Skipping only
        # the refresh lets polling resume as soon as the frame maps.
        if self.winfo_viewable():
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
        # 'short' is the cell-friendly form of a long diagnostic — the
        # full 'message' stays available on the status-column tooltip.
        msg = obj.get('short') or obj.get('message', 'Stopped')
        # ● is tinted by the row's status tag — Tk emoji are monochrome.
        return f"● {msg}"

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

    # Per-row action affordance — the Treeview can't host buttons, so the
    # actions column renders fixed glyph slots and clicks are hit-tested
    # by bbox quarter (see _act_hit). Slot order matches the context
    # menu: toggle, logs, edit, delete.
    _ACTION_SLOTS = 4
    _ACTION_TEXT = "{toggle}    ≡    ✎    ✕"

    def _actions_text(self, tunnel_id: str) -> str:
        key = self._status_key(tunnel_id)
        toggle = {'managed': '·', 'running': '⏹', 'starting': '⏹'
                  }.get(key, '▶')
        return self._ACTION_TEXT.format(toggle=toggle)

    def _act_hit(self, iid, x):
        """Dispatch a click on the actions cell to the right handler."""
        bbox = self.tree.bbox(iid, "actions")
        slot = 0
        if bbox:
            slot = max(0, min(self._ACTION_SLOTS - 1,
                              int((x - bbox[0]) / (bbox[2] / self._ACTION_SLOTS))))
        (self._act_start_stop, self._act_logs,
         self._act_edit, self._act_delete)[slot](iid)

    # --- Cell hover tooltip (shows extra_ports details on the route column) ---
    def _hide_tooltip(self):
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None
        self._tooltip_key = None

    def _on_tree_motion(self, event):
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        detail = None
        # Hand cursor over the actions cell sells it as clickable.
        self.tree.configure(cursor=(
            "hand2" if iid in self._tunnel_iids and col == '#3' else ""))
        if iid in self._tunnel_iids:
            if col == '#3':
                detail = "▶/⏹ Start·Stop   ≡ Logs   ✎ Edit   ✕ Delete"
            elif col == '#1':
                # Status column: the cell shows 'short'; surface the full
                # diagnostic message here when one exists.
                obj = self.current_statuses.get(iid, {})
                full = obj.get('message')
                if full and full != obj.get('short'):
                    detail = full
            elif col == '#2':
                tunnel = self.controller.get_object_by_id(iid) or {}
                extras = self._extra_ports(tunnel)
                if extras:
                    detail = "Extra ports:\n" + "\n".join(extras)
        if not detail:
            self._hide_tooltip()
            return
        tip_key = (iid, col)
        if self._tooltip_key == tip_key:
            if self._tooltip is not None:
                self._tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 12}")
            return
        self._tooltip_key = tip_key
        if self._tooltip is None:
            self._tooltip = tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            pal = palette()
            self._tooltip_lbl = tk.Label(
                tw, justify="left", bg=pal["bg_header"], fg=pal["fg"],
                padx=10, pady=6, font=TOOLTIP_FONT, wraplength=320,
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

        sorted_tunnels = sorted(tunnels_config, key=self._sort_key,
                                reverse=self._sort_desc)

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

    def _filtered(self, tunnels_config: list) -> list:
        """Substring match on hostname, server name, ports, destination."""
        flt = self._filter_var.get().strip().lower()
        if not flt:
            return tunnels_config
        def hit(t):
            server = self.controller.get_server_name(t.get('server_id', ''))
            return any(flt in s for s in (
                str(t.get('hostname', '')).lower(),
                str(server).lower(),
                str(t.get('remote_port', '')),
                str(t.get('local_destination', '')).lower(),
                str(t.get('extra_ports', '') or '').lower(),
            ))
        return [t for t in tunnels_config if hit(t)]

    # Tier thresholds in logical px — multiplied by the CTk widget scaling
    # because Configure/winfo report device px (at 175% scaling every
    # measurement is ~1.75x). Deterministic: recomputed from event.width
    # on every resize, so shrink-to and grow-to the same width agree.
    _WIDE_NEED = 950    # logical px: all buttons at full label size
    _COMPACT_NEED = 620  # logical px: short labels; below this icon-only

    def _on_resize_compact(self, event=None):
        """Pick the densest toolbar tier that still fits. Uses
        event.width — child winfo_width() lags one resize behind (it
        reads the pre-relayout size), which made the tier depend on
        which direction the window was dragged."""
        w = (event.width if event is not None else self.winfo_width()) - 20
        if w < 10:  # not yet mapped
            return
        try:
            s = ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            s = 1.0
        tier = (2 if w >= self._WIDE_NEED * s
                else 1 if w >= self._COMPACT_NEED * s else 0)
        if tier != self._tier:
            self._set_density(tier)

    def _set_density(self, tier):
        self._tier = tier
        for btn, wide_t, sm_t, icon_t, wide_w, sm_w, icon_w in self._toolbar_btns:
            if tier == 2:
                btn.configure(text=wide_t, width=wide_w, height=48,
                              font=self._btn_font_lg)
            elif tier == 1:
                btn.configure(text=sm_t, width=sm_w, height=36,
                              font=self._btn_font_sm)
            else:
                btn.configure(text=icon_t, width=icon_w, height=36,
                              font=self._btn_font_lg)
        self._search.configure(
            height=40 if tier == 2 else 32,
            width=240 if tier == 2 else 170 if tier == 1 else 100,
            placeholder_text="Filter tunnels…" if tier == 2 else "Filter…")
        for lbl, full, short in self._legend_word_labels:
            lbl.configure(text=short if tier == 0 else full)
        if self._legend_title is not None:
            self._legend_title.configure(text="" if tier == 0 else "Legend:")
        if self._hint_lbl is not None:
            self._hint_lbl.configure(
                text=("Double-click toggles  ·  Right-click for actions" if tier == 2
                      else "Right-click for actions" if tier == 1
                      else "R-click menu"))
        self._update_summary()

    def _update_summary(self):
        """Live status counter next to the Start/Stop controls."""
        counts = {"running": 0, "starting": 0, "error": 0,
                  "managed": 0, "stopped": 0}
        for tid in self._tunnel_iids:
            counts[self._status_key(tid)] += 1
        total = sum(counts.values())
        if not total:
            self._summary_lbl.configure(text="")
            return
        if self._tier == 0:
            text = f"{total} tunnels"
        elif self._tier == 1:
            # Compact: full words, drop the redundant total count.
            parts = [f"{n} {k}" for k, n in counts.items() if n]
            text = " · ".join(parts)
        else:
            parts = [f"{n} {k}" for k, n in counts.items() if n]
            text = "  ·  ".join(parts) + f"  ({total} total)"
        self._summary_lbl.configure(text=text)

    def sync_tunnel_list(self):
        """Full rebuild on config change (enter view, add/edit/delete)."""
        try:
            tunnels_config = self.controller.get_tunnels()
            self.current_statuses = self.controller.get_tunnel_statuses()
            self._rebuild_tree(self._filtered(tunnels_config))
            self._update_summary()
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
            self._update_summary()
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
        # A double-click landing on the actions cell would fire the
        # toggle twice (once per ButtonRelease) — no-op there.
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
        for idx, key in self._ctx_fg.items():
            try:
                self._ctx_menu.entryconfigure(idx, foreground=palette()[key])
            except Exception:
                pass
        self._hide_tooltip()

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid in self._tunnel_iids:
            self.tree.selection_set(iid)
            self._update_ctx_toggle(iid)
            self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _update_ctx_toggle(self, tid):
        """Relabel entry 0 to what it actually does: a stuck 'starting'
        tunnel is still desired, so it offers Stop (cancel); a failed
        (error) tunnel is effectively stopped, so it offers Start
        (retry), which resets the transport chain and reprobes."""
        key = self._status_key(tid)
        pal = palette()
        try:
            if key == 'managed':
                self._ctx_menu.entryconfigure(
                    0, label="  Managed elsewhere   ",
                    foreground=pal["info"], state="disabled", image="")
            elif key in ('stopped', 'error'):
                fg = pal["status_ok"]
                img = menu_icon("start", color=fg)
                kw = dict(image=img, compound="left") if img else {}
                self._ctx_menu.entryconfigure(
                    0, label="  Start   ",
                    foreground=fg, state="normal", **kw)
            else:  # running / starting — desired; Stop cancels
                fg = pal["status_err"]
                img = menu_icon("stop", color=fg)
                kw = dict(image=img, compound="left") if img else {}
                self._ctx_menu.entryconfigure(
                    0, label="  Stop   ",
                    foreground=fg, state="normal", **kw)
        except Exception:
            pass

    def _act_start_stop(self, tid=None):
        tid = tid or self._selected_tunnel()
        if not tid:
            return
        key = self._status_key(tid)
        if key == 'managed':
            return  # managed elsewhere — the · slot is a no-op
        if key in ('stopped', 'error'):
            self.controller.start_tunnel(tid)
        else:  # running / starting — cancel the desired attempt
            self.controller.stop_tunnel(tid)
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
