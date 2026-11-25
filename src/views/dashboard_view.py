import customtkinter as ctk
import logging
import tkinter as tk
from tkinter import Menu

from views.tree_style import apply_tree_style, menu_kwargs, menu_icon, update_menu, palette, tk_color, hover_color, ctk_label, TOOLTIP_FONT


class DashboardView(ctk.CTkFrame):
    """Tunnel list rendered as widget rows — one CTkLabel/CTkButton per
    cell inside a canvas-backed scroll area.

    That gives real per-cell color (neutral bold hostname, tinted status,
    dim route, real buttons) — the thing a Treeview structurally can't do
    (tags apply to the whole row). Rebuilt only on config sync; the 2s
    poll updates label text/color in place — no widget churn.
    """

    # Distinct glyph per state — shape carries meaning even before the
    # tinted text registers.
    _STATUS_GLYPHS = {'running': '✔', 'starting': '●', 'error': '✖',
                      'managed': '◆', 'stopped': '○'}
    _STATUS_PAL = {'running': 'status_ok', 'starting': 'status_warn',
                   'error': 'status_err', 'managed': 'info',
                   'stopped': 'fg_dim'}
    _STATUS_RANK = {"running": 0, "starting": 1, "error": 2,
                    "managed": 3, "stopped": 4}
    _SORT_LABELS = {"name": "Server / Tunnel", "status": "Status",
                    "route": "Route"}

    def __init__(self, parent, controller):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self.current_statuses = {}

        apply_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # control frame
        self.grid_rowconfigure(1, weight=1)  # tunnel list
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
        self._hovered_tid = None
        self._drag = None           # (col, start_x_root, start_w) mid-resize
        self._col_w = {1: 250, 2: 240}  # draggable status/route col widths

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

        # Live filter — narrows the list as you type. The spacer (not the
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

        # --- Column header + scrollable row list ---
        list_frame = ctk.CTkFrame(self, fg_color=tk_color("bg"))
        list_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        self._name_font = ctk.CTkFont(size=15, weight="bold")
        self._cell_font = ctk.CTkFont(size=14)
        self._hdr_font = ctk.CTkFont(size=13, weight="bold")

        # Header shares the rows' column spec (same grid config on the
        # same total width) so columns stay aligned without a Treeview.
        self._header_row = ctk.CTkFrame(
            list_frame, fg_color=tk_color("bg_alt"), corner_radius=0, height=32)
        self._header_row.grid(row=0, column=0, sticky="ew")
        self._header_row.grid_propagate(False)
        self._grid_cols(self._header_row)
        self._hdr_labels = {}
        for c, key, label in ((0, "name", "Server / Tunnel"),
                              (1, "status", "Status"),
                              (2, "route", "Route")):
            cell = ctk.CTkFrame(self._header_row, fg_color="transparent")
            cell.grid(row=0, column=c, sticky="nsew",
                      padx=(10 if c == 0 else 6, 0))
            if c in (1, 2):  # drag grip first so it claims the right edge
                grip = ctk.CTkFrame(
                    cell, fg_color=tk_color("border"), width=4,
                    corner_radius=0, cursor="sb_h_double_arrow")
                grip.pack(side="right", fill="y", pady=6)
                grip.bind("<Button-1>",
                          lambda e, col=c: self._grip_press(e, col))
                grip.bind("<B1-Motion>", self._grip_drag)
                grip.bind("<ButtonRelease-1>", self._grip_release)
            b = ctk.CTkButton(
                cell, text=label, anchor="w",
                font=self._hdr_font, fg_color="transparent",
                hover_color=tk_color("bg_header"),
                text_color=tk_color("fg_dim"), height=28,
                command=lambda k=key: self._sort_by(k))
            b.pack(side="left", fill="both", expand=True)
            self._hdr_labels[key] = b
        ctk.CTkLabel(
            self._header_row, text="Actions", font=self._hdr_font,
            text_color=tk_color("fg_dim"), anchor="w").grid(
            row=0, column=3, columnspan=2, sticky="ew", padx=6)

        self._canvas = tk.Canvas(
            list_frame, highlightthickness=0, bd=0, bg=palette()["bg"])
        self._vscroll = ctk.CTkScrollbar(
            list_frame, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vscroll.set)
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._vscroll.grid(row=1, column=1, sticky="ns")

        self._rows_frame = ctk.CTkFrame(self._canvas, fg_color="transparent")
        self._grid_cols(self._rows_frame)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(
                self._canvas_win, width=e.width))
        # Wheel events land on the widget under the pointer and don't
        # propagate — bind each scrollable surface (CTk forbids bind_all).
        for w in (self._canvas, self._rows_frame):
            w.bind("<MouseWheel>", self._on_wheel)

        # Sort state; heading clicks toggle direction.
        self._sort_col = "name"
        self._sort_desc = False
        self._sync_sort_headings()

        # --- Context menu (row actions live here, colored for scannability) ---
        self._ctx_menu = Menu(self, tearoff=0, **menu_kwargs())
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
        self._legend_word_labels = []
        legend_items = [
            ("status_ok", "✔", "Running", "Run"),
            ("status_warn", "●", "Starting", "Start"),
            ("status_err", "✖", "Error", "Err"),
            ("fg_dim", "○", "Stopped", "Stop"),
            ("info", "◆", "Managed Elsewhere", "Managed"),
        ]
        for color_key, glyph, text, short in legend_items:
            item_frame = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
            item_frame.pack(side="left", padx=(8, 4))
            # Fixed-width icon slot — emoji vary in width, so free-packing
            # leaves the labels jagged.
            ctk_label(item_frame, text=glyph, font=ctk.CTkFont(size=13),
                      text_color=tk_color(color_key), width=18,
                      ).pack(side="left")
            lbl = ctk_label(item_frame, text=text, font=ctk.CTkFont(size=12))
            lbl.pack(side="left", padx=(2, 0))
            self._legend_word_labels.append((lbl, text, short))

        # Discoverability hint for the row actions.
        self._hint_lbl = ctk_label(
            self.legend_frame,
            text="Double-click toggles  ·  ≡ menu or right-click for actions",
            font=ctk.CTkFont(size=11),
            text_color=tk_color("fg_dim"))
        self._hint_lbl.pack(side="right", padx=10)

        self.status_check_timer = None
        self._rows = {}          # tid -> {'cells','status','route','toggle','menu','bg'}
        self._tunnel_iids = set()
        self._collapsed = {}     # server_id -> bool
        self._selected_tid = None
        self._tooltip = None

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

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
            text_color=("#ececec", "#d6d6da"),
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

    @staticmethod
    def _blend(hex_a: str, hex_b: str, t: float) -> str:
        """Linear hex blend — theme-safe stripe/derived shades."""
        a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#{:02x}{:02x}{:02x}".format(
            *[int(x + (y - x) * t) for x, y in zip(a, b)])

    def _grid_cols(self, frame):
        """Column spec shared by the header and every row so they align.
        Hostname is elastic; status/route are fixed-px and draggable via
        the header grips (minsize is the whole width when weight=0)."""
        frame.grid_columnconfigure(0, weight=1, minsize=150)
        frame.grid_columnconfigure(1, weight=0, minsize=self._col_w[1])
        frame.grid_columnconfigure(2, weight=0, minsize=self._col_w[2])
        frame.grid_columnconfigure(3, minsize=44)
        frame.grid_columnconfigure(4, minsize=44)

    def _grip_press(self, event, col):
        self._drag = (col, event.x_root, self._col_w[col])

    def _grip_drag(self, event):
        if not self._drag:
            return
        col, x0, w0 = self._drag
        w = max(80, min(600, w0 + event.x_root - x0))
        if w == self._col_w[col]:
            return
        self._col_w[col] = w
        for f in (self._header_row, self._rows_frame):
            f.grid_columnconfigure(col, minsize=w)

    def _grip_release(self, _event):
        self._drag = None

    def _on_wheel(self, event):
        try:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def _sort_by(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self._sync_sort_headings()
        self.sync_tunnel_list()

    def _sync_sort_headings(self):
        for col, btn in self._hdr_labels.items():
            arrow = ("  ▼" if self._sort_desc else "  ▲") if col == self._sort_col else ""
            btn.configure(text=self._SORT_LABELS[col] + arrow)

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self.sync_tunnel_list()
        self.poll_statuses()

    def on_leave(self):
        self._hide_tooltip()
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
        # leaving the list frozen on its initial paint. Skipping only
        # the refresh lets polling resume as soon as the frame maps.
        if self.winfo_viewable():
            self.refresh_tunnel_statuses()
        self.status_check_timer = self.after(2000, self.poll_statuses)

    # ------------------------------------------------------------------
    # Data -> cells
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
        # full 'message' stays available on the status-cell tooltip.
        msg = obj.get('short') or obj.get('message', 'Stopped')
        glyph = self._STATUS_GLYPHS.get(self._status_key(tunnel_id), '○')
        # Edge spaces pad the pill — CTkLabel has no internal padding.
        return f" {glyph} {msg} "

    def _status_tk(self, tunnel_id: str):
        return tk_color(self._STATUS_PAL.get(self._status_key(tunnel_id), 'fg_dim'))

    def _toggle_glyph(self, tunnel_id: str) -> str:
        key = self._status_key(tunnel_id)
        # '■' not '⏹' — U+23F9 is missing from the stock font and renders
        # as a tofu box; U+25A0 is always present.
        return {'managed': '·', 'running': '■', 'starting': '■'
                }.get(key, '▶')

    def _toggle_role(self, tunnel_id: str) -> str:
        """Semantic button color: green starts, red stops, gray no-ops."""
        key = self._status_key(tunnel_id)
        return {'managed': 'secondary', 'running': 'danger',
                'starting': 'danger'}.get(key, 'success')

    def _pill_bg(self, tunnel_id: str, row_bg: str) -> str:
        """Faint status tint behind the pill — reads as a badge, not a bar."""
        key = self._STATUS_PAL.get(self._status_key(tunnel_id), 'fg_dim')
        return self._blend(row_bg, palette()[key], 0.18)

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

    # ------------------------------------------------------------------
    # Tooltips (per-cell now — real labels, so hover is per-cell too)
    # ------------------------------------------------------------------

    def _hide_tooltip(self):
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None

    def _show_tooltip(self, event, text):
        if self._tooltip is None:
            tip = self._tooltip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.wm_attributes("-topmost", True)
            pal = palette()
            self._tooltip_lbl = tk.Label(
                tip, justify="left", bg=pal["bg_header"], fg=pal["fg"],
                padx=10, pady=6, font=TOOLTIP_FONT, wraplength=320,
                highlightthickness=1, highlightbackground=pal["border"])
            self._tooltip_lbl.pack()
        self._tooltip_lbl.configure(text=text)
        self._tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 12}")

    def _tooltip_move(self, event):
        if self._tooltip is not None:
            self._tooltip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 12}")

    def _hover_status(self, event, tid):
        obj = self.current_statuses.get(tid, {})
        full = obj.get('message')
        if full and full != obj.get('short'):
            self._show_tooltip(event, full)
        else:
            self._hide_tooltip()

    def _hover_route(self, event, tid):
        tunnel = self.controller.get_object_by_id(tid) or {}
        extras = self._extra_ports(tunnel)
        if extras:
            self._show_tooltip(event, "Extra ports:\n" + "\n".join(extras))
        else:
            self._hide_tooltip()

    # ------------------------------------------------------------------
    # Row construction / selection
    # ------------------------------------------------------------------

    def _build_row(self, tid, tunnel, r, row_bg):
        name = ctk.CTkLabel(
            self._rows_frame, text=tunnel.get('hostname', tid),
            font=self._name_font, text_color=tk_color("fg"),
            fg_color=row_bg, anchor="w")
        name.grid(row=r, column=0, sticky="nsew", padx=(10, 0), ipady=9)

        # Status lives in a wrapper so its fg can be a tinted pill (badge
        # look) while the wrapper tracks the row bg behind it.
        status_wrap = ctk.CTkFrame(
            self._rows_frame, fg_color=row_bg, corner_radius=0)
        status_wrap.grid(row=r, column=1, sticky="nsew", padx=(6, 0))
        pill = ctk.CTkLabel(
            status_wrap, text=self._status_text(tid),
            font=self._cell_font, text_color=self._status_tk(tid),
            fg_color=self._pill_bg(tid, row_bg), corner_radius=8,
            anchor="w")
        pill.pack(side="left", padx=4, pady=8, ipady=2)

        route = ctk.CTkLabel(
            self._rows_frame, text=self._route_text(tunnel),
            font=self._cell_font, text_color=tk_color("fg_dim"),
            fg_color=row_bg, anchor="w")
        route.grid(row=r, column=2, sticky="nsew", padx=(6, 0), ipady=9)

        role = self._toggle_role(tid)
        toggle = ctk.CTkButton(
            self._rows_frame, text=self._toggle_glyph(tid),
            width=34, height=28, font=self._cell_font, corner_radius=6,
            fg_color=tk_color(role), hover_color=hover_color(role),
            text_color=("#ececec", "#d6d6da"),
            state=("disabled" if self._status_key(tid) == 'managed'
                   else "normal"),
            command=lambda t=tid: self._act_start_stop(t))
        toggle.grid(row=r, column=3, padx=2, pady=5)
        menu_btn = ctk.CTkButton(
            self._rows_frame, text="≡", width=34, height=28,
            font=self._cell_font, corner_radius=6,
            fg_color=tk_color("bg_header"),
            hover_color=tk_color("secondary"),
            text_color=tk_color("fg_dim"),
            command=lambda t=tid: self._row_menu(t))
        menu_btn.grid(row=r, column=4, padx=(2, 8), pady=5)

        hover_widgets = (name, status_wrap, pill, route)
        for w in hover_widgets + (toggle, menu_btn):
            w.bind("<MouseWheel>", self._on_wheel)
        for w in hover_widgets:
            w.bind("<Button-1>", lambda e, t=tid: self._select(t))
            w.bind("<Double-1>", lambda e, t=tid: self._act_start_stop(t))
            w.bind("<Button-3>", lambda e, t=tid: self._row_menu(t, e))
            w.bind("<Enter>", lambda e, t=tid: self._row_enter(t))
            w.bind("<Leave>", lambda e, t=tid: self._row_leave(e, t))
        # bind() replaces same-sequence bindings, so cells that also need
        # tooltips get combined handlers rather than a second bind.
        for w in (status_wrap, pill):
            w.bind("<Enter>", lambda e, t=tid: (
                self._row_enter(t), self._hover_status(e, t)))
            w.bind("<Motion>", self._tooltip_move)
            w.bind("<Leave>", lambda e, t=tid: (
                self._hide_tooltip(), self._row_leave(e, t)))
        route.bind("<Enter>", lambda e, t=tid: (
            self._row_enter(t), self._hover_route(e, t)))
        route.bind("<Motion>", self._tooltip_move)
        route.bind("<Leave>", lambda e, t=tid: (
            self._hide_tooltip(), self._row_leave(e, t)))

        self._rows[tid] = {
            'name': name, 'status_wrap': status_wrap, 'pill': pill,
            'route': route, 'toggle': toggle, 'menu': menu_btn,
            'bg': row_bg, 'hover_widgets': hover_widgets}
        self._tunnel_iids.add(tid)
        self._apply_row_bg(tid)

    def _rebuild_rows(self, tunnels_config: list):
        for child in self._rows_frame.winfo_children():
            child.destroy()
        self._rows.clear()
        self._tunnel_iids.clear()
        visible_ids = {t['id'] for t in tunnels_config}
        if self._selected_tid not in visible_ids:
            self._selected_tid = None

        if not tunnels_config:
            empty = ("No tunnels match the filter"
                     if self._filter_var.get().strip()
                     else "No tunnels configured…")
            ctk.CTkLabel(
                self._rows_frame, text=empty,
                font=self._cell_font,
                text_color=tk_color("fg_dim")).grid(
                row=0, column=0, columnspan=5, sticky="w",
                padx=10, pady=14)
            return

        pal = palette()
        stripe = self._blend(pal["bg"], pal["bg_header"], 0.30)
        sorted_tunnels = sorted(tunnels_config, key=self._sort_key,
                                reverse=self._sort_desc)

        by_server, order = {}, []
        for t in sorted_tunnels:
            sid = t.get('server_id') or ''
            if sid not in by_server:
                by_server[sid] = []
                order.append(sid)
            by_server[sid].append(t)

        r = 0
        for sid in order:
            kids = by_server[sid]
            name = self.controller.get_server_name(sid)
            band = ctk.CTkFrame(
                self._rows_frame, fg_color=tk_color("bg_header"),
                corner_radius=4, height=34)
            band.grid(row=r, column=0, columnspan=5, sticky="ew",
                      padx=2, pady=(8, 2))
            band.grid_propagate(False)
            arrow = "▸" if self._collapsed.get(sid) else "▾"
            band_lbl = ctk.CTkLabel(
                band, text=f"{arrow} {name} ({len(kids)})",
                font=self._name_font, text_color=tk_color("fg"),
                anchor="w")
            band_lbl.pack(fill="both", expand=True, padx=10)
            for w in (band, band_lbl):
                w.bind("<Button-1>", lambda e, s=sid: self._toggle_group(s))
                w.bind("<MouseWheel>", self._on_wheel)
                try:
                    w.configure(cursor="hand2")
                except Exception:
                    pass
            r += 1
            if self._collapsed.get(sid):
                continue
            for i, tunnel in enumerate(kids):
                row_bg = pal["bg"] if i % 2 == 0 else stripe
                self._build_row(tunnel['id'], tunnel, r, row_bg)
                r += 1

    def _toggle_group(self, sid):
        self._collapsed[sid] = not self._collapsed.get(sid, False)
        self.sync_tunnel_list()

    def _select(self, tid):
        if self._selected_tid == tid:
            return
        old = self._selected_tid
        self._selected_tid = tid
        for t in (old, tid):
            self._apply_row_bg(t)

    def _row_enter(self, tid):
        if self._hovered_tid != tid:
            self._hovered_tid = tid
            self._apply_row_bg(tid)

    def _row_leave(self, event, tid):
        # Moving between cells fires Leave+Enter — don't un-highlight if
        # the pointer is still inside this row's widgets.
        w = self.winfo_containing(event.x_root, event.y_root)
        if w in self._rows.get(tid, {}).get('hover_widgets', ()):
            return
        if self._hovered_tid == tid:
            self._hovered_tid = None
            self._apply_row_bg(tid)

    def _row_effective_bg(self, tid) -> str:
        w = self._rows[tid]
        pal = palette()
        if tid == self._selected_tid:
            return self._blend(pal["bg"], pal["accent"], 0.55)
        if tid == self._hovered_tid:
            return self._blend(w['bg'], pal["bg_header"], 0.5)
        return w['bg']

    def _apply_row_bg(self, tid):
        w = self._rows.get(tid)
        if not w:
            return
        bg = self._row_effective_bg(tid)
        w['name'].configure(fg_color=bg)
        w['route'].configure(fg_color=bg)
        w['status_wrap'].configure(fg_color=bg)
        w['pill'].configure(fg_color=self._pill_bg(tid, bg))

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
                text=("Double-click toggles  ·  ≡ menu or right-click for actions" if tier == 2
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
            self._rebuild_rows(self._filtered(tunnels_config))
            self._update_summary()
        except Exception as e:
            logging.error(f"Error during tunnel list sync: {e}", exc_info=True)

    def refresh_tunnel_statuses(self):
        """Lightweight poll update: rewrite cell text/colors in place."""
        try:
            self.current_statuses = self.controller.get_tunnel_statuses()
            for tid, w in self._rows.items():
                tunnel = self.controller.get_object_by_id(tid)
                if not tunnel:
                    continue
                w['pill'].configure(
                    text=self._status_text(tid),
                    text_color=self._status_tk(tid),
                    fg_color=self._pill_bg(tid, self._row_effective_bg(tid)))
                w['route'].configure(text=self._route_text(tunnel))
                role = self._toggle_role(tid)
                w['toggle'].configure(
                    text=self._toggle_glyph(tid),
                    fg_color=tk_color(role), hover_color=hover_color(role),
                    state=("disabled" if self._status_key(tid) == 'managed'
                           else "normal"))
            self._update_summary()
        except Exception as e:
            logging.warning(f"Status refresh failed: {e}")

    # ------------------------------------------------------------------
    # Selection + actions
    # ------------------------------------------------------------------

    def _selected_tunnel(self):
        return self._selected_tid if self._selected_tid in self._tunnel_iids else None

    def _row_menu(self, tid, event=None):
        """Context menu from right-click or the ≡ button."""
        self._select(tid)
        self._update_ctx_toggle(tid)
        if event is not None:
            x, y = event.x_root, event.y_root
        else:
            b = self._rows[tid]['menu']
            x = b.winfo_rootx() + b.winfo_width() - 4
            y = b.winfo_rooty() + b.winfo_height()
        self._ctx_menu.tk_popup(x, y)

    def on_theme_changed(self):
        update_menu(self._ctx_menu)
        for idx, key in self._ctx_fg.items():
            try:
                self._ctx_menu.entryconfigure(idx, foreground=palette()[key])
            except Exception:
                pass
        try:
            self._canvas.configure(bg=palette()["bg"])
            self._header_row.configure(fg_color=tk_color("bg_alt"))
        except Exception:
            pass
        self._hide_tooltip()
        self.sync_tunnel_list()

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
