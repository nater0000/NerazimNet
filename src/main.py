import os
import sys
import logging

def _load_theme(ctk):
    """Loads the bundled Nerazim theme JSON, falling back to the default blue."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    theme_path = os.path.join(base_path, 'resources', 'themes', 'nerazim.json')
    if os.path.isfile(theme_path):
        ctk.set_default_color_theme(theme_path)
    else:
        logging.warning(f"Theme file not found at {theme_path}; using default theme.")
        ctk.set_default_color_theme("blue")

def main():
    """
    Main function to initialize and run the NerazimNet application.
    """
    from utils.logger import setup_logger
    setup_logger()
    if '--daemon' in sys.argv:
        # Headless supervisor mode — launched by the OS service
        # (Scheduled Task / systemd user / LaunchAgent), owns frpc
        # processes so tunnels persist without the GUI. Keep it
        # dependency-light: importing customtkinter/App under pythonw
        # (no console) is slow and can hang before run() is reached.
        from controllers.daemon_runner import DaemonRunner
        DaemonRunner().run()
        return
    import customtkinter as ctk
    ctk.set_appearance_mode("System")
    _load_theme(ctk)
    from app import App
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C kills the Tk mainloop outright — run the normal quit path
        # so Syncthing (and friends) aren't left orphaned holding the exe.
        logging.info("Interrupted — shutting down cleanly.")
        app.on_closing(force_quit=True)

if __name__ == "__main__":
    main()
