"""Smoke test: construct DashboardView under a real CTk root, exercise
sync/refresh/menu paths, then exit. Catches construction-time crashes
the pytest suite can't see (it never builds widgets)."""
import sys, os, traceback, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

import customtkinter as ctk


class StubController:
    def __init__(self):
        # Real CTkImages so the smoke run exercises the icon paths too.
        try:
            from PIL import Image
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'resources', 'images')
            self.images = {}
            for name in ('add', 'start', 'stop', 'menu', 'logs', 'edit',
                         'delete', 'copy'):
                p = os.path.join(base, f'{name}.png')
                if os.path.exists(p):
                    im = Image.open(p)
                    self.images[name] = ctk.CTkImage(
                        light_image=im, dark_image=im)
            self.btn_images = dict(self.images)
            self.solid_images = dict(self.images)
        except Exception:
            self.images = self.btn_images = self.solid_images = {}
        self.objects = {
            'srv1': {'id': 'srv1', '_type': 'server', 'name': 'ionos-01'},
            'tun1': {'id': 'tun1', '_type': 'tunnel', 'server_id': 'srv1',
                     'route_type': 'tunnel', 'remote_port': '8443',
                     'local_destination': 'localhost:3000', 'extra_ports': '9090,9091',
                     'hostname': 'app.example.com', 'client_device_id': 'dev'},
            'tun2': {'id': 'tun2', '_type': 'tunnel', 'server_id': 'srv1',
                     'route_type': 'local', 'remote_port': '22',
                     'local_destination': '', 'extra_ports': '',
                     'hostname': 'ssh.example.com', 'client_device_id': 'dev'},
        }
        self.calls = []

    def get_tunnels(self):
        return [o for o in self.objects.values() if o.get('_type') == 'tunnel']

    def get_object_by_id(self, oid):
        return self.objects.get(oid)

    def get_server_name(self, sid):
        s = self.objects.get(sid)
        return s['name'] if s else sid

    def get_tunnel_statuses(self):
        return {'tun1': {'status': 'running', 'message': 'Live via tcp',
                         'short': 'Live via tcp'},
                'tun2': {'status': 'error', 'message': 'Unreachable :7000',
                         'short': 'Unreachable :7000'}}

    def add_new_tunnel(self): self.calls.append('add')
    def start_all_tunnels(self): self.calls.append('start_all')
    def stop_all_tunnels(self): self.calls.append('stop_all')
    def start_tunnel(self, t): self.calls.append(('start', t))
    def stop_tunnel(self, t): self.calls.append(('stop', t))
    def view_tunnel_log(self, t): self.calls.append(('logs', t))
    def copy_tunnel_address(self, t): self.calls.append(('copy', t))
    def edit_tunnel(self, t): self.calls.append(('edit', t))
    def delete_tunnel(self, t): self.calls.append(('delete', t))


def main():
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.geometry("1100x640")
    from views.dashboard_view import DashboardView
    view = DashboardView(root, StubController())
    view.pack(fill="both", expand=True)
    steps = []

    def step(fn, label):
        fn()
        steps.append(label)

    def go():
        try:
            ev = types.SimpleNamespace(x_root=500, y_root=300, delta=120)
            step(view.on_enter, "on_enter")
            step(view.refresh_tunnel_statuses, "refresh")
            step(lambda: view._sort_by("status"), "sort status")
            step(lambda: view._sort_by("route"), "sort route")
            step(lambda: view._sort_by("name"), "sort name")
            step(lambda: view._filter_var.set("app"), "filter")
            step(lambda: view._filter_var.set(""), "unfilter")
            step(lambda: view._toggle_group('srv1'), "collapse")
            step(lambda: view._toggle_group('srv1'), "expand")
            step(lambda: view._select('tun1'), "select")
            step(lambda: view._row_enter('tun1'), "row hover")
            step(lambda: view._row_leave(ev, 'tun1'), "row unhover")
            step(lambda: view._grip_press(ev, 1), "grip press")
            step(lambda: view._grip_drag(ev), "grip drag")
            step(lambda: view._grip_release(ev), "grip release")
            step(lambda: view._update_ctx_toggle('tun1'), "ctx running")
            step(lambda: view._update_ctx_toggle('tun2'), "ctx error")
            step(lambda: view._act_start_stop('tun1'), "toggle stop")
            step(lambda: view._act_start_stop('tun2'), "toggle start")
            step(view.on_theme_changed, "theme")
            step(lambda: view._set_density(1), "compact")
            step(lambda: view._set_density(0), "icon")
            step(lambda: view._set_density(2), "wide")
            step(view.on_leave, "on_leave")
            root.after(300, root.destroy)
        except Exception:
            traceback.print_exc()
            root.destroy()
            sys.exit(1)

    root.after(200, go)
    root.mainloop()
    print("SMOKE OK:", ", ".join(steps))


if __name__ == "__main__":
    main()
