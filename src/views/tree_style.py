"""Shared colors + ttk styling for NerazimNet's native views.

Primary path: ttkbootstrap with custom "nerazim" (dark) and
"nerazim-light" themes built from the app's slate/teal palette —
professional flat ttk look (trees, scrollbars, themed buttons like
"danger.TButton").

Fallback (ttkbootstrap not installed): equivalent hand-tuned clam styles,
including the same named button styles, so the views work either way.

Views call apply_tree_style() once at construction and refresh_theme()
when CustomTkinter's appearance mode changes; tk.Menu and Treeview tags
capture colors at construction, so views also get on_theme_changed()
hooks that re-apply menu_kwargs()/tag colors from palette().
"""

from tkinter import ttk

_DARK = {
    "bg": "#242424", "bg_alt": "#2e2e2e", "bg_header": "#31505A",
    "surface": "#101C21",  # CTk window/dialog surface (nerazim.json)
    "fg": "#EAF5F0", "fg_dim": "#9fb8bd", "accent": "#527078",
    "success": "#2E7D32", "danger": "#D32F2F", "info": "#4a90a4",
    "secondary": "#4a5560", "warning": "#D9A70C",
    "status_ok": "#7CDBA7", "status_warn": "#FFC107",
    "status_err": "#FF8A80",
    "border": "#3a3a3a", "selectfg": "#FFFFFF",
}

_LIGHT = {
    "bg": "#f3f5f6", "bg_alt": "#ffffff", "bg_header": "#c9dade",
    "surface": "#E2F0EB",  # CTk window/dialog surface (nerazim.json)
    "fg": "#1d2b2e", "fg_dim": "#5f7a80", "accent": "#527078",
    "success": "#1E7B34", "danger": "#B9312C", "info": "#1f7f96",
    "secondary": "#5c6b74", "warning": "#9a6a00",
    "status_ok": "#1E7B34", "status_warn": "#8a6200",
    "status_err": "#C62828",
    "border": "#c9d1d4", "selectfg": "#FFFFFF",
}

# Legacy aliases (dark defaults) — kept for any missed call sites.
_BG = _DARK["bg"]
_BG_ALT = _DARK["bg_alt"]
_BG_HEADER = _DARK["bg_header"]
_FG = _DARK["fg"]
_FG_DIM = _DARK["fg_dim"]
_ACCENT = _DARK["accent"]

_applied = False
has_bootstrap = False
_scale = 1.0
_named_font_bases: dict = {}

# Named fonts for custom elements. Anonymous fonts like ("Segoe UI", 12)
# never re-derive when 'tk scaling' changes — only NAMED fonts propagate
# re-realization to every widget using them, which is what makes the tree
# text itself scale (and un-scale cleanly) instead of just the padding.
TREE_FONT = "NerazimTree"
TAB_FONT = "NerazimTab"
BOLD_FONT = "NerazimTreeBold"
HEADING_FONT = "NerazimHeading"
BTN_FONT = "NerazimButton"
INPUT_FONT = "NerazimInput"
TOOLTIP_FONT = "NerazimTooltip"
_TREE_SIZE = 20
_TAB_SIZE = 14
_HEADING_SIZE = 16
_BTN_SIZE = 14
_INPUT_SIZE = 14
_TOOLTIP_SIZE = 16


_kept_fonts: dict = {}


def _ensure_named_fonts():
    """Create the named fonts, keeping references — Font.__del__ deletes the
    Tcl font when the Python object is GC'd, so unreferenced creation is
    instantly undone."""
    import tkinter.font as tkfont
    for name, size, weight in ((TREE_FONT, _TREE_SIZE, "normal"),
                             (TAB_FONT, _TAB_SIZE, "normal"),
                             (BOLD_FONT, _TREE_SIZE, "bold"),
                             (HEADING_FONT, _HEADING_SIZE, "bold"),
                             (BTN_FONT, _BTN_SIZE, "normal"),
                             (INPUT_FONT, _INPUT_SIZE, "normal"),
                             (TOOLTIP_FONT, _TOOLTIP_SIZE, "normal")):
        try:
            _kept_fonts[name] = tkfont.nametofont(name)
        except Exception:
            try:
                _kept_fonts[name] = tkfont.Font(
                    name=name, family="Segoe UI", size=size, weight=weight)
            except Exception:
                pass
    # Re-point the base Tk fonts so anything left unstyled (menus, stray
    # tk.Labels, listboxes, combobox popdowns) renders at input size
    # instead of the ~9pt stock default. ttk.Entry/Combobox have a real
    # -font widget option defaulting to TkTextFont — the ttk style font
    # never reaches them. (tk scaling multiplies points at realization.)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkTooltipFont"):
        try:
            f = tkfont.nametofont(name)
            f.configure(size=_INPUT_SIZE)
            _kept_fonts[name] = f
            _named_font_bases[name] = _INPUT_SIZE
        except Exception:
            pass


def _scale_named_fonts():
    """Re-realize named Tk fonts at the CURRENT 'tk scaling'.

    Named fonts don't re-derive metrics when 'tk scaling' changes, so
    widgets using them stay frozen. Reconfiguring forces re-realization at
    the new scaling — the point size stays untouched (setting it larger
    double-scales, since scaling multiplies point sizes on realization).
    """
    import tkinter.font as tkfont
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont",
                 "TkTooltipFont", "TkFixedFont",
                 TREE_FONT, TAB_FONT, BOLD_FONT, HEADING_FONT, BTN_FONT,
                 INPUT_FONT, TOOLTIP_FONT):
        try:
            f = tkfont.nametofont(name)
            if name not in _named_font_bases:
                _named_font_bases[name] = f.cget("size")
            f.configure(size=_named_font_bases[name])
        except Exception:
            pass


def _clamp_scale(factor) -> float:
    try:
        return max(0.75, min(3.0, float(factor or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _configure_sizes(style, scale: float):
    """Size-sensitive style options. Fonts are NAMED fonts re-realized at
    'tk scaling' by set_scale(); pixel metrics need the factor directly."""
    f = _clamp_scale(scale)
    style.configure("Treeview", rowheight=int(52 * f), font=TREE_FONT,
                    indent=14)
    style.configure("Treeview.Heading", font=HEADING_FONT)
    style.configure("TNotebook.Tab", font=TAB_FONT,
                    padding=(int(20 * f), int(8 * f)))
    style.configure("TButton", font=BTN_FONT)
    for role in ("primary", "secondary", "success", "info",
                 "warning", "danger"):
        style.configure(f"{role}.TButton", font=BTN_FONT)
    style.configure("TCombobox", font=INPUT_FONT)
    style.configure("TEntry", font=INPUT_FONT)
    style.configure("TCheckbutton", font=INPUT_FONT)
    style.configure("TRadiobutton", font=INPUT_FONT)
    # Label-type widgets sit directly on the CTk window surface (#101C21),
    # not on the ttk panel bg — match it or their rects box out visibly.
    surface = palette()["surface"]
    for st in ("TRadiobutton", "TCheckbutton", "TLabel", "TFrame"):
        style.configure(st, background=surface)
    try:
        # Combobox readonly field bg matches entries (bg_alt).
        style.map("TCombobox",
                  fieldbackground=[("readonly", palette()["bg_alt"])],
                  background=[("readonly", palette()["bg_alt"])])
        style.configure("Sash", sashthickness=int(6 * f))
    except Exception:
        pass
    # Drop the theme-drawn expand/collapse indicator — views draw their own
    # arrow characters in the item text instead, matching the named fonts.
    try:
        style.layout('Treeview.Item', [
            ('Treeitem.padding', {'sticky': 'nswe', 'children': [
                ('Treeitem.image', {'side': 'left', 'sticky': ''}),
                ('Treeitem.text', {'sticky': 'we'}),
            ]})
        ])
    except Exception:
        pass


def set_scale(factor):
    """Re-render styled ttk widgets at a new UI scale.

    'tk scaling' only affects font metric lookups made AFTER it changes —
    existing ttk widgets keep cached sizes — so we reconfigure the styles to
    force font re-realization at the new scaling, and scale pixel metrics.
    """
    global _scale
    _scale = _clamp_scale(factor)
    _scale_named_fonts()
    if has_bootstrap:
        try:
            from ttkbootstrap import Style
            _configure_sizes(Style(), _scale)
            return
        except Exception:
            pass
    if _applied:
        _configure_sizes(ttk.Style(), _scale)


def current_mode() -> str:
    """Effective appearance mode ('dark'/'light'), resolving 'system'."""
    try:
        import customtkinter as ctk
        mode = ctk.get_appearance_mode()
        if mode == "System":
            try:
                from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import (
                    AppearanceModeTracker)
                mode = AppearanceModeTracker.detect_appearance_mode()
            except Exception:
                mode = "Dark"
        return str(mode).lower()
    except Exception:
        return "dark"


def palette() -> dict:
    return _LIGHT if current_mode() == "light" else _DARK


def tk_color(key: str):
    """CTk (light, dark) color tuple for CTk widget fg_color/text_color."""
    return (_LIGHT[key], _DARK[key])


def apply_tree_style():
    """Register both nerazim themes once and use the current mode."""
    global _applied, has_bootstrap
    _ensure_named_fonts()
    mode = current_mode()
    try:
        from ttkbootstrap import Style
        from ttkbootstrap.style.theme import ThemeDefinition
        style = Style()
        if not _applied:
            style.register_theme(ThemeDefinition("nerazim", {
                "primary": _DARK["accent"], "secondary": _DARK["secondary"],
                "success": _DARK["success"], "info": _DARK["info"],
                "warning": _DARK["warning"], "danger": _DARK["danger"],
                "light": "#3a3a3a", "dark": _DARK["bg"],
                "bg": _DARK["bg"], "fg": _DARK["fg"],
                "selectbg": _DARK["accent"], "selectfg": "#FFFFFF",
                "border": _DARK["border"],
                "inputfg": _DARK["fg"], "inputbg": _DARK["bg_alt"],
                "active": _DARK["bg_header"],
            }, mode="dark"))
            style.register_theme(ThemeDefinition("nerazim-light", {
                "primary": _LIGHT["accent"], "secondary": _LIGHT["secondary"],
                "success": _LIGHT["success"], "info": _LIGHT["info"],
                "warning": _LIGHT["warning"], "danger": _LIGHT["danger"],
                "light": "#f8f9fa", "dark": "#212529",
                "bg": _LIGHT["bg"], "fg": _LIGHT["fg"],
                "selectbg": _LIGHT["accent"], "selectfg": "#FFFFFF",
                "border": _LIGHT["border"],
                "inputfg": _LIGHT["fg"], "inputbg": _LIGHT["bg_alt"],
                "active": _LIGHT["bg_header"],
            }, mode="light"))
        style.theme_use("nerazim-light" if mode == "light" else "nerazim")
        _configure_sizes(style, _scale)
        _applied = True
        has_bootstrap = True
        _guard_ctk_from_tk_repaint()
        import logging
        logging.info(
            f"ttk theme '{style.theme_use()}' applied "
            f"(rowheight={style.lookup('Treeview', 'rowheight')}, "
            f"font={style.lookup('Treeview', 'font')})")
        return
    except ImportError:
        pass
    _manual_style(palette())
    _applied = True
    import logging
    st = ttk.Style()
    logging.info(
        f"ttk theme '{st.theme_use()}' applied via manual fallback "
        f"(rowheight={st.lookup('Treeview', 'rowheight')}, "
        f"font={st.lookup('Treeview', 'font')})")


def _guard_ctk_from_tk_repaint():
    """ttkbootstrap's _theme_walk repaints every legacy tk widget on
    theme_use — including CustomTkinter internals (tk.Label text labels,
    Frames, Canvases). That wipes CTk text_color/fg and left the dark box
    behind button text. Skip any widget inside a customtkinter subtree —
    CTk owns those colors."""
    try:
        from ttkbootstrap.style.bootstyle import Bootstyle
        if getattr(Bootstyle, '_ctk_guarded', False):
            return
        orig = Bootstyle.update_tk_widget_style

        def guarded(widget):
            w = widget
            while w is not None:
                if type(w).__module__.startswith('customtkinter'):
                    return
                w = getattr(w, 'master', None)
            return orig(widget)

        Bootstyle.update_tk_widget_style = guarded
        Bootstyle._ctk_guarded = True
    except Exception:
        pass


def refresh_theme():
    """Re-apply the active theme after an appearance-mode change."""
    global _applied
    if not _applied:
        apply_tree_style()
        return
    if has_bootstrap:
        try:
            from ttkbootstrap import Style
            style = Style()
            mode = current_mode()
            style.theme_use("nerazim-light" if mode == "light" else "nerazim")
            _configure_sizes(style, _scale)
        except Exception as e:
            import logging
            logging.debug(f"refresh_theme failed: {e}")
    else:
        _manual_style(palette())


def _manual_style(pal: dict):
    """Fallback: equivalent look without ttkbootstrap (clam honors colors)."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Treeview",
        background=pal["bg"], fieldbackground=pal["bg"],
        foreground=pal["fg"], rowheight=int(52 * _scale),
        font=TREE_FONT, borderwidth=0, indent=14,
    )
    try:
        style.configure("Treeview", indicatorcolor=pal["fg_dim"])
        style.layout('Treeview.Item', [
            ('Treeitem.padding', {'sticky': 'nswe', 'children': [
                ('Treeitem.image', {'side': 'left', 'sticky': ''}),
                ('Treeitem.text', {'sticky': 'we'}),
            ]})
        ])
    except Exception:
        pass
    style.configure("Treeview.Heading",
                    background=pal["bg_alt"], foreground=pal["fg_dim"],
                    font=HEADING_FONT, borderwidth=0)
    style.configure("TButton", font=BTN_FONT)
    style.configure("TCombobox", font=INPUT_FONT)
    style.configure("TEntry", font=INPUT_FONT)
    style.configure("TCheckbutton", font=INPUT_FONT)
    style.configure("TRadiobutton", font=INPUT_FONT)
    for st in ("TRadiobutton", "TCheckbutton", "TLabel", "TFrame"):
        style.configure(st, background=pal["surface"], foreground=pal["fg"])
    style.map("TCombobox",
              fieldbackground=[("readonly", pal["bg_alt"])],
              background=[("readonly", pal["bg_alt"])])
    style.map("Treeview",
              background=[("selected", pal["accent"])],
              foreground=[("selected", "#FFFFFF")])
    style.configure("TNotebook.Tab", font=TAB_FONT,
                    padding=(int(20 * _scale), int(8 * _scale)))
    # Named role styles so ttk.Button(style="danger.TButton") works the same
    # as under ttkbootstrap.
    for name, bg in (("primary", pal["accent"]), ("secondary", pal["secondary"]),
                     ("success", pal["success"]), ("info", pal["info"]),
                     ("warning", pal["warning"]), ("danger", pal["danger"])):
        style.configure(f"{name}.TButton", background=bg, foreground="#FFFFFF",
                        font=BTN_FONT, padding=(10, 7), borderwidth=0)
        style.map(f"{name}.TButton",
                  background=[("active", pal["bg_header"]),
                              ("disabled", pal["bg_alt"])],
                  foreground=[("disabled", pal["fg_dim"])])


_FORM_FONT_CTK = None


def tk_colors(key: str) -> tuple:
    """(light, dark) pair for a palette color — for CTk widgets that need
    both appearance variants."""
    return (_LIGHT[key], _DARK[key])


def shade(color: str, factor: float = 0.78) -> str:
    """Darken a '#rrggbb' color — for CTk button hover_color."""
    color = color.lstrip('#')
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return '#%02x%02x%02x' % tuple(
        max(0, min(255, int(c * factor))) for c in (r, g, b))


def hover_color(key: str) -> tuple:
    """(light, dark) hover pair for a palette role color."""
    return (shade(_LIGHT[key]), shade(_DARK[key]))


def status_text_color(text: str) -> tuple:
    """(light, dark) text color for update/status messages."""
    if text.startswith("Up to date"):
        return ("#2da44e", "#3fb950")
    if text.startswith("Update available"):
        return ("#9a6a00", "#e0a030")
    return ("#c93c37", "#f08080")


def ctk_label(*args, **kwargs):
    """CTkLabel with a shared 14pt form font (scales with widget scaling).
    Explicit font= still wins."""
    global _FORM_FONT_CTK
    import customtkinter as ctk
    if _FORM_FONT_CTK is None:
        _FORM_FONT_CTK = ctk.CTkFont(size=14)
    kwargs.setdefault("font", _FORM_FONT_CTK)
    kwargs.setdefault("bg_color", "transparent")
    return ctk.CTkLabel(*args, **kwargs)


def menu_kwargs() -> dict:
    """Colors + font for tk.Menu context menus to match the active theme.
    INPUT_FONT is a named font, so it re-realizes with tk scaling like
    every other UI font."""
    _ensure_named_fonts()
    pal = palette()
    return {"bg": pal["bg_alt"], "fg": pal["fg"],
            "activebackground": pal["accent"], "activeforeground": "#FFFFFF",
            "font": INPUT_FONT}


_menu_icon_cache: dict = {}


def menu_icon(name: str, size: int = 16):
    """Small PNG icon for tk.Menu entries (image= + compound='left'
    gives a real aligned icon column — glyph prefixes never line up).
    Results are cached; returns None on any failure so callers degrade
    to plain text."""
    key = (name, size)
    if key in _menu_icon_cache:
        return _menu_icon_cache[key]
    try:
        import sys, os
        from PIL import Image, ImageTk
        base = (getattr(sys, '_MEIPASS', None)
                or os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))))
        path = os.path.join(base, 'resources', 'images', f'{name}.png')
        img = ImageTk.PhotoImage(
            Image.open(path).resize((size, size), Image.LANCZOS))
        _menu_icon_cache[key] = img
        return img
    except Exception:
        return None


def update_menu(menu):
    """Re-apply theme colors to a tk.Menu after an appearance change."""
    try:
        menu.configure(**menu_kwargs())
    except Exception:
        pass
