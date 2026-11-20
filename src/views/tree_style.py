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
    "fg": "#EAF5F0", "fg_dim": "#9fb8bd", "accent": "#527078",
    "success": "#2E7D32", "danger": "#D32F2F", "info": "#4a90a4",
    "secondary": "#4a5560", "warning": "#D9A70C",
    "status_ok": "#7CDBA7", "status_warn": "#FFC107",
    "border": "#3a3a3a", "selectfg": "#FFFFFF",
}

_LIGHT = {
    "bg": "#f3f5f6", "bg_alt": "#ffffff", "bg_header": "#c9dade",
    "fg": "#1d2b2e", "fg_dim": "#5f7a80", "accent": "#527078",
    "success": "#1E7B34", "danger": "#B9312C", "info": "#1f7f96",
    "secondary": "#5c6b74", "warning": "#9a6a00",
    "status_ok": "#1E7B34", "status_warn": "#8a6200",
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
        style.configure("Treeview", rowheight=40, font=("Segoe UI", 12))
        style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(14, 6))
        _applied = True
        has_bootstrap = True
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
            style.configure("Treeview", rowheight=40, font=("Segoe UI", 12))
            style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(14, 6))
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
        foreground=pal["fg"], rowheight=40,
        font=("Segoe UI", 12), borderwidth=0,
    )
    style.configure("Treeview.Heading",
                    background=pal["bg_alt"], foreground=pal["fg_dim"],
                    font=("Segoe UI", 12, "bold"), borderwidth=0)
    style.map("Treeview",
              background=[("selected", pal["accent"])],
              foreground=[("selected", "#FFFFFF")])
    style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(14, 6))
    # Named role styles so ttk.Button(style="danger.TButton") works the same
    # as under ttkbootstrap.
    for name, bg in (("primary", pal["accent"]), ("secondary", pal["secondary"]),
                     ("success", pal["success"]), ("info", pal["info"]),
                     ("warning", pal["warning"]), ("danger", pal["danger"])):
        style.configure(f"{name}.TButton", background=bg, foreground="#FFFFFF",
                        font=("Segoe UI", 10), padding=(10, 7), borderwidth=0)
        style.map(f"{name}.TButton",
                  background=[("active", pal["bg_header"]),
                              ("disabled", pal["bg_alt"])],
                  foreground=[("disabled", pal["fg_dim"])])


def menu_kwargs() -> dict:
    """Colors for tk.Menu context menus to match the active theme."""
    pal = palette()
    return {"bg": pal["bg_alt"], "fg": pal["fg"],
            "activebackground": pal["accent"], "activeforeground": "#FFFFFF"}


def update_menu(menu):
    """Re-apply theme colors to a tk.Menu after an appearance change."""
    try:
        menu.configure(**menu_kwargs())
    except Exception:
        pass
