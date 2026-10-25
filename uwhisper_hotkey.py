"""Global shortcut using Windows' own hotkey system (RegisterHotKey).

Unlike a keyboard hook, Windows can't silently drop it when the app is busy,
and it works no matter which window is in front (even admin windows).
"""
import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "windows": 0x8}
MOD_NOREPEAT = 0x4000   # holding the keys down doesn't fire again and again
WM_HOTKEY = 0x0312
WM_REBIND = 0x8001      # our own message: "apply the new shortcut"
WM_QUIT = 0x0012
HOTKEY_ID = 1

NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "backspace": 0x08, "insert": 0x2D,
    "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "pause": 0x13,
    "print screen": 0x2C, "scroll lock": 0x91,
}


def parse(combo):
    """'ctrl+shift+r' -> (modifier flags, virtual-key code), or None if unknown."""
    mods, vk = 0, None
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in MODS:
            mods |= MODS[part]
        elif len(part) == 1:
            code = user32.VkKeyScanW(ord(part))
            if code == -1:
                return None
            vk = code & 0xFF
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part in NAMED_KEYS:
            vk = NAMED_KEYS[part]
        else:
            return None
    return (mods, vk) if vk else None


class SystemHotkey:
    """Runs a tiny message loop on its own thread that owns the hotkey."""

    def __init__(self, on_press):
        self.on_press = on_press
        self._want = None
        self._result = (False, "not started")
        self._done = threading.Event()
        self._ready = threading.Event()
        self._registered = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkey")
        self._thread.start()
        self._ready.wait(5)

    def set(self, combo):
        """Register `combo` (or None to clear). Returns (ok, error_message)."""
        self._want = combo
        self._done.clear()
        user32.PostThreadMessageW(self._tid, WM_REBIND, 0, 0)
        self._done.wait(5)
        return self._result

    def stop(self):
        user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _apply(self):
        if self._registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False
        if not self._want:
            self._result = (True, "")
        else:
            parsed = parse(self._want)
            if not parsed:
                self._result = (False, f"don't know the key in {self._want!r}")
            elif user32.RegisterHotKey(None, HOTKEY_ID, parsed[0] | MOD_NOREPEAT, parsed[1]):
                self._registered = True
                self._result = (True, "")
            else:
                self._result = (False, "already used by another app")
        self._done.set()

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create this thread's queue
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                try:
                    self.on_press()
                except Exception:
                    pass
            elif msg.message == WM_REBIND:
                self._apply()
        if self._registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
