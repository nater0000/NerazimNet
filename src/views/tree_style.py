"""Shared colors + ttk.Treeview styling for NerazimNet list views.

Colors are tuned to match the app's dark slate palette so native ttk
widgets blend with the surrounding CustomTkinter chrome.
"""

from tkinter import ttk

_BG = "#242424"
_BG_ALT = "#2e2e2e"
_BG_HEADER = "#31505A"
_FG = "#EAF5F0"
_FG_DIM = "#9fb8bd"
_ACCENT = "#527078"

_applied = False


def apply_tree_style():
    """Configure ttk.Treeview colors once to match the app's dark theme."""
    global _applied
    if _applied:
        return
    _applied = True
    style = ttk.Style()
    try:
        style.theme_use("clam")  # clam honors background/fieldbackground
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
