"""Shared colors + ttk styling for NerazimNet's native list views.

Primary path: ttkbootstrap with a custom "nerazim" theme built from the
app's slate/teal palette — professional flat ttk look (trees, scrollbars,
themed buttons like "danger.TButton").

Fallback (ttkbootstrap not installed): equivalent hand-tuned clam styles,
including the same named button styles, so the views work either way.
"""

from tkinter import ttk

_BG = "#242424"
_BG_ALT = "#2e2e2e"
_BG_HEADER = "#31505A"
_FG = "#EAF5F0"
_FG_DIM = "#9fb8bd"
_ACCENT = "#527078"

_SUCCESS = "#2E7D32"
_DANGER = "#D32F2F"
_INFO = "#4a90a4"
_SECONDARY = "#4a5560"
_WARNING = "#D9A70C"

_applied = False
has_bootstrap = False


def apply_tree_style():
    """Style ttk widgets once to match the app's dark theme."""
    global _applied, has_bootstrap
    if _applied:
        return
    _applied = True
    try:
        from ttkbootstrap import Style
        from ttkbootstrap.style.theme import ThemeDefinition
        style = Style()
        style.register_theme(ThemeDefinition("nerazim", {
            "primary": _ACCENT, "secondary": _SECONDARY,
            "success": _SUCCESS, "info": _INFO,
            "warning": _WARNING, "danger": _DANGER,
            "light": "#3a3a3a", "dark": _BG,
            "bg": _BG, "fg": _FG,
            "selectbg": _ACCENT, "selectfg": "#FFFFFF",
            "border": "#3a3a3a", "inputfg": _FG, "inputbg": _BG_ALT,
            "active": _BG_HEADER,
        }, mode="dark"))
        style.theme_use("nerazim")
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        has_bootstrap = True
        return
    except ImportError:
        pass
    _manual_style()


def _manual_style():
    """Fallback: equivalent look without ttkbootstrap (clam honors colors)."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Treeview",
        background=_BG,
        fieldbackground=_BG,
        foreground=_FG,
        rowheight=30,
        font=("Segoe UI", 10),
        borderwidth=0,
    )
    style.configure("Treeview.Heading",
                    background=_BG_ALT, foreground=_FG_DIM,
                    font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map("Treeview",
              background=[("selected", _ACCENT)],
              foreground=[("selected", "#FFFFFF")])
    # Named role styles so ttk.Button(style="danger.TButton") works the same
    # as under ttkbootstrap.
    for name, bg in (("primary", _ACCENT), ("secondary", _SECONDARY),
                     ("success", _SUCCESS), ("info", _INFO),
                     ("warning", _WARNING), ("danger", _DANGER)):
        style.configure(f"{name}.TButton", background=bg, foreground="#FFFFFF",
                        font=("Segoe UI", 9), padding=(10, 6), borderwidth=0)
        style.map(f"{name}.TButton",
                  background=[("active", _BG_HEADER), ("disabled", _BG_ALT)],
                  foreground=[("disabled", _FG_DIM)])


def menu_kwargs():
    """Colors for tk.Menu context menus to match the theme."""
    return {"bg": _BG_ALT, "fg": _FG, "activebackground": _ACCENT,
            "activeforeground": "#FFFFFF"}
