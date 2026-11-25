"""Screenshot helper: build DashboardView in a real CTk window, lift it,
capture with PIL, save PNG for visual inspection."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
import customtkinter as ctk
from _smoke_view import StubController


def main():
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.geometry("1080x640+80+60")
    from views.dashboard_view import DashboardView
    view = DashboardView(root, StubController())
    view.pack(fill="both", expand=True)
    view.on_enter()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.6)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    img.save("_shot_dashboard.png")
    print("saved _shot_dashboard.png", img.size)
    root.destroy()


if __name__ == "__main__":
    main()
