"""Short-lived KDE portal session for explicitly confirmed Kate edits."""
import ctypes as C
import json
import os
import select
import subprocess
import time
import uuid

from .files import TaskError

BUS = "org.freedesktop.portal.Desktop"
OBJECT = "/org/freedesktop/portal/desktop"
REMOTE = BUS.replace(".Desktop", ".RemoteDesktop")
CAST = BUS.replace(".Desktop", ".ScreenCast")
KEYBOARD = 4


class Eis:
    def __init__(self, fd):
        self.ctx = None
        try:
            lib = C.CDLL("libei.so.1")
            pointer, integer = C.c_void_p, C.c_int
            for name, result, args in [
                ("new_sender", pointer, [pointer]), ("unref", pointer, [pointer]),
                ("setup_backend_fd", integer, [pointer, integer]), ("get_fd", integer, [pointer]),
                ("dispatch", None, [pointer]), ("get_event", pointer, [pointer]),
                ("event_get_type", integer, [pointer]), ("event_unref", pointer, [pointer]),
                ("event_get_seat", pointer, [pointer]), ("event_get_device", pointer, [pointer]),
                ("seat_bind_capabilities", None, [pointer]),
                ("device_has_capability", C.c_bool, [pointer, integer]),
                ("device_ref", pointer, [pointer]), ("device_unref", pointer, [pointer]),
                ("device_start_emulating", None, [pointer, C.c_uint32]),
                ("device_stop_emulating", None, [pointer]),
                ("device_keyboard_key", None, [pointer, C.c_uint32, C.c_bool]),
                ("device_frame", None, [pointer, C.c_uint64]),
                ("now", C.c_uint64, [pointer]),
            ]:
                fn = getattr(lib, "ei_" + name)
                fn.restype, fn.argtypes = result, args
                setattr(self, name, fn)
            self.device = None
            self.disconnected = False
            self.ctx = self.new_sender(None)
            if not self.ctx or self.setup_backend_fd(self.ctx, fd) != 0:
                raise TaskError("Could not connect to the portal EIS device.")
        except BaseException:
            if self.ctx:
                self.unref(self.ctx)
            else:
                os.close(fd)
            raise

    def update(self, wait=0):
        if select.select([self.get_fd(self.ctx)], [], [], wait)[0]:
            self.dispatch(self.ctx)
        while event := self.get_event(self.ctx):
            try:
                kind = self.event_get_type(event)
                if kind == 2:
                    self.disconnected = True
                elif kind == 3:
                    self.seat_bind_capabilities(self.event_get_seat(event), C.c_int(KEYBOARD), C.c_void_p())
                elif kind in (6, 7, 8):
                    device = self.event_get_device(event)
                    if kind == 8 and self.device is None and self.device_has_capability(device, KEYBOARD):
                        self.device = self.device_ref(device)
                        self.device_start_emulating(self.device, 1)
                    elif kind in (6, 7) and device == self.device:
                        self.device_unref(device)
                        self.device = None
            finally:
                self.event_unref(event)

    def key(self, code, down):
        self.device_keyboard_key(self.device, code, down)
        self.device_frame(self.device, self.now(self.ctx))

    def close(self):
        if self.device:
            self.device_stop_emulating(self.device)
            self.device_unref(self.device)
        self.unref(self.ctx)


class Portal:
    def __init__(self, stop):
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gio, GLib, Gst
        self.Gio, self.GLib, self.Gst = Gio, GLib, Gst
        Gst.init(None)
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.stop = stop
        self.session = self.eis = self.pipe = None
        self.revoked = False
        self.deadline = time.monotonic() + 90
        self.sub = None
        self.bus = None
        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            self.bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        except BaseException:
            self.context.pop_thread_default()
            raise

    def check(self):
        while self.context.pending():
            self.context.iteration(False)
        if self.stop.is_set() or self.revoked:
            raise TaskError("Desktop session stopped or revoked.")
        if time.monotonic() >= self.deadline:
            raise TaskError("Desktop permission or input timed out.")

    def variant(self, kind, value):
        return self.GLib.Variant(kind, value)

    def call(self, path, interface, name, kind, values, fd=False):
        args = (BUS, path, interface, name, self.variant(kind, values), None,
                self.Gio.DBusCallFlags.NONE, 2000)
        if fd:
            value, fds = self.bus.call_with_unix_fd_list_sync(*args, None, None)
            return fds.get(value.unpack()[0])
        return self.bus.call_sync(*args, None).unpack()

    def request(self, interface, name, kind, *args, **opts):
        token = "phoenix" + uuid.uuid4().hex
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        result = []
        signal = self.bus.signal_subscribe(BUS, BUS.replace(".Desktop", ".Request"), "Response",
                                           path, None, self.Gio.DBusSignalFlags.NONE,
                                           lambda *event: result.append(event[-1].unpack()))
        try:
            actual, = self.call(OBJECT, interface, name, kind,
                                (*args, {**opts, "handle_token": self.variant("s", token)}))
            if actual != path:
                raise TaskError("The portal returned an unexpected request handle.")
            while not result:
                self.check()
                time.sleep(.03)
            code, data = result[0]
            if code:
                raise TaskError("KDE desktop permission was denied or cancelled.")
            return data
        finally:
            self.bus.signal_unsubscribe(signal)
            if not result:
                try:
                    self.call(path, BUS.replace(".Desktop", ".Request"), "Close", "()", ())
                except self.GLib.Error:
                    pass

    def __enter__(self):
        return self

    def start(self):
        variant = self.variant
        self.session = self.request(REMOTE, "CreateSession", "(a{sv})",
                                    session_handle_token=variant("s", "phoenix" + uuid.uuid4().hex))["session_handle"]
        self.sub = self.bus.signal_subscribe(BUS, BUS.replace(".Desktop", ".Session"), "Closed",
                                            self.session, None, self.Gio.DBusSignalFlags.NONE,
                                            lambda *_: setattr(self, "revoked", True))
        self.request(REMOTE, "SelectDevices", "(oa{sv})", self.session,
                     types=variant("u", 1), persist_mode=variant("u", 0))
        self.request(CAST, "SelectSources", "(oa{sv})", self.session, types=variant("u", 3),
                     multiple=variant("b", False))
        result = self.request(REMOTE, "Start", "(osa{sv})", self.session, "")
        if not result.get("devices", 0) & 1 or len(result.get("streams", [])) != 1:
            raise TaskError("Grant keyboard control and select exactly one capture source.")
        self.check()
        self.eis = Eis(self.call(OBJECT, REMOTE, "ConnectToEIS", "(oa{sv})", (self.session, {}), fd=True))
        fd = self.call(OBJECT, CAST, "OpenPipeWireRemote", "(oa{sv})", (self.session, {}), fd=True)
        self.capture_fd = fd
        node = result["streams"][0][0]
        selector = f"path={node}"
        try:
            graph = json.loads(subprocess.check_output(["pw-dump", str(node)], timeout=2))
            serial = next(item["info"]["props"]["object.serial"] for item in graph
                          if item["id"] == node and item["type"] == "PipeWire:Interface:Node")
            selector = f"target-object={int(serial)}"
        except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
            pass
        self.pipe = self.Gst.parse_launch(
            f"pipewiresrc fd={fd} {selector} ! video/x-raw ! "
            "appsink name=frames max-buffers=1 drop=true sync=false")
        self.pipe.set_state(self.Gst.State.PLAYING)
        while not self.eis.device:
            self.check()
            self.eis.update(.05)
            if self.eis.disconnected:
                raise TaskError("KDE disconnected the EIS keyboard.")

    def frame(self):
        self.check()
        sink = self.pipe.get_by_name("frames")
        while True:
            self.check()
            event = self.pipe.get_bus().pop_filtered(self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS)
            if event:
                detail = event.parse_error() if event.type == self.Gst.MessageType.ERROR else ("EOS", "")
                raise TaskError(f"The PipeWire capture stream stopped: {detail[0]} ({detail[1]})")
            sample = sink.emit("try-pull-sample", 100 * self.Gst.MSECOND)
            if sample:
                structure = sample.get_caps().get_structure(0)
                return structure.get_value("width"), structure.get_value("height")

    def __exit__(self, *_):
        try:
            if self.eis:
                self.eis.close()
            if self.session and not self.revoked:
                try:
                    self.call(self.session, BUS.replace(".Desktop", ".Session"), "Close", "()", ())
                except self.GLib.Error:
                    pass
        finally:
            try:
                self.release_capture()
            finally:
                self.close_bus()

    def release_capture(self):
        try:
            if self.pipe:
                self.pipe.set_state(self.Gst.State.NULL)
        finally:
            if hasattr(self, "capture_fd"):
                os.close(self.capture_fd)

    def close_bus(self):
        try:
            if self.sub:
                self.bus.signal_unsubscribe(self.sub)
            self.bus.close_sync(None)
        finally:
            self.context.pop_thread_default()


def active_kate(pid, filename):
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    desktop = Atspi.get_desktop(0)
    for app in desktop:
        if app.get_process_id() != pid:
            continue
        for window in app:
            if (window.get_state_set().contains(Atspi.StateType.ACTIVE)
                    and window.get_name().split(" — ", 1)[0].rstrip(" *") == filename):
                return True
    return False


# Linux evdev keycodes for the US/QWERTY layout. Verify edited bytes after Save.
KEYS = {" ": 57, **dict(zip("qwertyuiop", range(16, 26))),
        **dict(zip("asdfghjkl", range(30, 39))), **dict(zip("zxcvbnm", range(44, 51)))}


def append_kate(files, grant, path, line, stop, status, portal_factory=Portal, active=active_kate):
    if not line or len(line) > 80 or any(letter not in KEYS for letter in line):
        raise TaskError("Use up to 80 lowercase US keyboard letters and spaces.")
    before = files.read(grant, path)
    if len(before) > 65536 or b"\r" in before or b"\x00" in before:
        raise TaskError("Choose a small UTF-8 text document with Unix line endings.")
    before.decode("utf-8")
    if stop.is_set():
        raise TaskError("Stopped before opening Kate.")
    target = files.local_path(grant, path)
    env = {key: value for key, value in os.environ.items()
           if key not in {"PHOENIX_API_KEY", "LLAMA_API_KEY"}}
    env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
    editor = subprocess.Popen(["kate", "--new", str(target)], env=env)
    status("KDE permission pending. Grant keyboard control and select a screen or Kate window.")
    with portal_factory(stop) as portal:
        portal.start()
        width, height = portal.frame()
        status(f"Captured a {width}×{height} PipeWire frame. Focus {target.name} in the new Kate window.")
        until = time.monotonic() + 12
        while not active(editor.pid, target.name):
            portal.check()
            if time.monotonic() >= until:
                raise TaskError("The expected Kate window is not active or accessible.")
            time.sleep(.08)
        if before != files.read(grant, path):
            raise TaskError("The document changed before input began.")
        def chord(code, modifier=None):
            portal.check()
            portal.eis.update()
            if not portal.eis.device or portal.eis.disconnected:
                raise TaskError("The desktop input device was revoked.")
            if not active(editor.pid, target.name):
                raise TaskError("Window target changed. Inspect Kate for unsaved input.")
            if modifier is not None:
                portal.eis.key(modifier, True)
            portal.eis.key(code, True)
            portal.eis.key(code, False)
            if modifier is not None:
                portal.eis.key(modifier, False)
        chord(107, 29)  # Ctrl+End
        if before and not before.endswith(b"\n"):
            chord(28)
        for letter in line:
            chord(KEYS[letter])
        chord(28)
        if before != files.read(grant, path):
            raise TaskError("The document on disk changed while typing. Inspect Kate for unsaved input.")
        chord(31, 29)  # Ctrl+S
        expected = before + (b"\n" if before and not before.endswith(b"\n") else b"") + line.encode() + b"\n"
        for _ in range(30):
            if files.read(grant, path) == expected:
                return f"Appended and verified the line in {path}."
            portal.check()
            time.sleep(.1)
        raise TaskError("Save could not be verified. Inspect the document in Kate.")
