"""uWhisper - press Ctrl+Shift+R (changeable) anywhere to record, press again (or click the
waveform) to stop. The recording is saved, transcribed on the GPU, pasted into
the window you were typing in, and copied to the clipboard.

Files:  C:\\Users\\<you>\\Documents\\uWhisper\\audio\\<timestamp>.wav   (deleted after 30 days by default)
        C:\\Users\\<you>\\Documents\\uWhisper\\text\\<timestamp>.txt    (kept forever)
        Always on this PC, never in OneDrive.
Settings and log: %USERPROFILE%\\.uwhisper\\
"""
import collections
import math
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import logging
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import winreg

APP = "uWhisper"
HOTKEY = "ctrl+shift+r"
# Bundled files (icon) live next to the script, or inside the exe's _internal folder when packaged.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- one copy only
_mutex = kernel32.CreateMutexW(None, False, "Local\\uWhisperSingleInstance")
if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    user32.MessageBoxW(None, "uWhisper is already running (look in the hidden icons tray).", APP, 0x40)
    sys.exit(0)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
# Our own taskbar identity, so Windows shows uWhisper's icon instead of Python's.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("uWhisper.App")
except Exception:
    pass

# ---------------------------------------------------------------- paths / config
# Not %APPDATA%: Microsoft Store Python silently redirects writes there.
CONF_DIR = os.path.join(os.path.expanduser("~"), ".uwhisper")
os.makedirs(CONF_DIR, exist_ok=True)
CONF_PATH = os.path.join(CONF_DIR, "config.json")

logging.basicConfig(
    filename=os.path.join(CONF_DIR, "uwhisper.log"), level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(APP)
if sys.stdout:  # running with python.exe: also log to the console
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))


FOLDERID_MUSIC = "{4BD8D571-6D19-48D3-BE97-422220080E43}"
FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def known_folder(folder_id, fallback):
    """A real Windows folder (Music, Documents), even if Windows/OneDrive moved it."""
    try:
        import uuid
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(folder_id).bytes_le)
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path)) == 0:
            result = path.value
            ctypes.windll.ole32.CoTaskMemFree(path)
            return result
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), fallback)


import uwhisper_storage as storage  # noqa: E402

# Everything stays on this PC. The local C:\Users\<name>\Documents folder is used on
# purpose, NOT Windows' "Documents" location, which OneDrive often redirects to the cloud.
OUT_DIR = os.path.join(os.path.expanduser("~"), "Documents", APP)
OLD_OUT_DIRS = [  # earlier versions saved here; move anything found into OUT_DIR
    os.path.join(known_folder(FOLDERID_MUSIC, "Music"), APP),
    os.path.join(known_folder(FOLDERID_DOCUMENTS, "Documents"), APP),  # maybe OneDrive
]
AUDIO_DIR, TEXT_DIR = storage.make_folders(OUT_DIR)
for _old in OLD_OUT_DIRS:
    try:
        _moved = storage.migrate(_old, OUT_DIR, log=log.warning)
        if _moved:
            log.info("moved %d old recordings from %s to %s", _moved, _old, OUT_DIR)
    except Exception:
        log.exception("could not move old recordings from %s", _old)


class Config:
    def __init__(self):
        self.device = None      # microphone name, None = Windows default
        self.hotkey = HOTKEY
        self.whisper = {}       # speech-to-text settings (merged with defaults later)
        self.cleanup = storage.normalize_cleanup(None)  # how long audio is kept
        try:
            with open(CONF_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config is not an object")
            self.device = data.get("device")
            self.hotkey = data.get("hotkey") or HOTKEY
            self.whisper = data.get("whisper") or {}
            if not isinstance(self.whisper, dict):
                self.whisper = {}
            self.cleanup = storage.normalize_cleanup(data.get("cleanup"))
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("config unreadable, using defaults (old copy kept as config.json.bad)")
            try:
                shutil.copyfile(CONF_PATH, CONF_PATH + ".bad")
            except OSError:
                pass
        combo = normalize_hotkey(self.hotkey) if isinstance(self.hotkey, str) else None
        self.hotkey = combo or HOTKEY

    def save(self):
        # Write to a temp file first so a crash mid-save can't wipe the settings.
        tmp = CONF_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"device": self.device, "hotkey": self.hotkey,
                       "whisper": self.whisper, "cleanup": self.cleanup}, f, indent=2)
        os.replace(tmp, CONF_PATH)


MODIFIERS = {"ctrl", "shift", "alt", "windows"}


def normalize_hotkey(combo):
    """'left ctrl+Right Shift+R' -> 'ctrl+shift+r'; None if it isn't a usable shortcut."""
    parts = []
    for p in combo.lower().split("+"):
        p = p.strip().replace("left ", "").replace("right ", "")
        p = {"control": "ctrl", "win": "windows"}.get(p, p)
        if p and p not in parts:
            parts.append(p)
    mods = [p for p in parts if p in MODIFIERS]
    keys = [p for p in parts if p not in MODIFIERS]
    if len(keys) != 1 or keys[0] in ("esc", "escape"):
        return None
    is_fkey = keys[0].startswith("f") and keys[0][1:].isdigit() and 1 <= int(keys[0][1:]) <= 24
    if not mods and not is_fkey:
        return None  # a bare letter would hijack normal typing
    order = ["ctrl", "alt", "shift", "windows"]
    combo = "+".join(sorted(mods, key=order.index) + keys)
    from uwhisper_hotkey import parse  # only keep combos Windows can actually register
    return combo if parse(combo) else None


def hotkey_label(combo):
    names = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "windows": "Win"}
    return [names.get(p, p.upper() if len(p) <= 3 else p.title()) for p in combo.split("+")]


# ---------------------------------------------------------------- heavy imports
import keyboard  # noqa: E402
import numpy as np  # noqa: E402
import pyperclip  # noqa: E402
import pystray  # noqa: E402
import sounddevice as sd  # noqa: E402
import soundfile as sf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import uwhisper_engine  # noqa: E402
import uwhisper_gpu as gpu  # noqa: E402
from uwhisper_hotkey import SystemHotkey  # noqa: E402
import uwhisper_theme as T  # noqa: E402
from uwhisper_logo import draw_icon  # noqa: E402
from uwhisper_panel import Panel  # noqa: E402


class TrayIcon(pystray.Icon):
    """Tray icon that opens our own panel on left or right click."""

    def __init__(self, *args, on_click, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_click = on_click

    def _on_notify(self, wparam, lparam):
        if lparam in (0x0202, 0x0205):  # WM_LBUTTONUP, WM_RBUTTONUP
            self._on_click()

# ---------------------------------------------------------------- windows helpers
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]


def work_area():
    r = wt.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def focus_window(hwnd):
    """Bring a window back to the front so the paste lands in it."""
    if not hwnd or user32.GetForegroundWindow() == hwnd:
        return
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(fg_thread, me, True)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(fg_thread, me, False)
    time.sleep(0.08)


def wait_for_modifiers_released(timeout=1.0):
    """If Shift/Alt/Win from the hotkey are still held, Ctrl+V would become
    Ctrl+Shift+V or Ctrl+Alt+V (Paste Special). Wait briefly for them to come up."""
    keys = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, left/right Win
    end = time.time() + timeout
    while time.time() < end:
        if not any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            return
        time.sleep(0.02)


# ---------------------------------------------------------------- autostart
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if FROZEN:  # running as uWhisper.exe
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def autostart_enabled(*_):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ---------------------------------------------------------------- tray icon art
# Drawn at exactly the tray's pixel size for this screen (e.g. 48 px at 300%), so it's sharp.
TRAY_PX = max(16, user32.GetSystemMetrics(49))  # SM_CXSMICON
ICON_IDLE = draw_icon(TRAY_PX)
ICON_REC = draw_icon(TRAY_PX, badge="rec")
ICON_BUSY = draw_icon(TRAY_PX, badge="busy")


# ---------------------------------------------------------------- microphones
def wasapi_index():
    for i, h in enumerate(sd.query_hostapis()):
        if "WASAPI" in h["name"]:
            return i
    return None


def list_mics():
    api = wasapi_index()
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and (api is None or d["hostapi"] == api):
            if d["name"] not in names:
                names.append(d["name"])
    return names


def resolve_mic(name):
    """Return (device index, device info) for a saved mic name, or the default."""
    api = wasapi_index()
    if name:
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0 and (api is None or d["hostapi"] == api):
                return i, d
        log.warning("saved mic %r not found, using default", name)
    idx = sd.query_hostapis(api)["default_input_device"] if api is not None else sd.default.device[0]
    return idx, sd.query_devices(idx)


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self):
        self.level = 0.0  # peak since the overlay last read it
        self.stream = None
        self.path = None
        self.started = 0.0

    def start(self, mic_name, path):
        idx, info = resolve_mic(mic_name)
        if idx is None or idx < 0:
            raise RuntimeError("no microphone found")
        rate = int(info["default_samplerate"])
        channels = min(2, int(info["max_input_channels"]))
        self._q = queue.Queue()
        # Open the mic first: if it fails, nothing is left half-open.
        stream = sd.InputStream(device=idx, samplerate=rate, channels=channels,
                                dtype="float32", callback=self._callback)
        try:
            self._file = sf.SoundFile(path, "w", samplerate=rate, channels=channels, subtype="PCM_16")
        except Exception:
            stream.close()
            raise
        self.path = path
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()
        try:
            stream.start()
        except Exception:
            stream.close()
            self._q.put(None)
            self._writer.join()
            try:
                os.remove(path)  # empty file, nothing worth keeping
            except OSError:
                pass
            raise
        self.stream = stream
        self.started = time.time()
        log.info("recording from %s (%d Hz, %d ch) -> %s", info["name"], rate, channels, path)

    def _callback(self, indata, frames, t, status):
        self._q.put(indata.copy())
        peak = float(np.abs(indata).max()) if frames else 0.0
        if peak > self.level:
            self.level = peak

    def _write_loop(self):
        last_flush = time.time()
        while True:
            block = self._q.get()
            if block is None:
                break
            self._file.write(block)
            if time.time() - last_flush > 3:  # keep the file valid if anything crashes
                self._file.flush()
                last_flush = time.time()
        self._file.close()

    def stop(self):
        """Stop and finish the file. Never raises (an unplugged mic can make stop() fail)."""
        if self.stream is None:
            return 0.0
        stream, self.stream = self.stream, None
        for fn in (stream.stop, stream.close):
            try:
                fn()
            except Exception:
                log.exception("mic stream did not stop cleanly")
        self._q.put(None)
        self._writer.join()
        return time.time() - self.started

    def take_level(self):
        lv, self.level = self.level, 0.0
        return lv


# ---------------------------------------------------------------- overlay
KEY = "#010203"  # transparent color key (gives rounded corners)

# Waveform colors, matching the logo's bars.
BAR_BOTTOM = T.rgb("bar_bottom")  # deep orange-red base
BAR_TOP = T.rgb("bar_top")        # orange tip on quiet bars
BAR_HOT = T.rgb("bar_hot")        # yellow tip on loud bars


def _fire_mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _fire_hex(c):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


class Overlay:
    BARS = 34

    def __init__(self, root, on_click):
        self.root = root
        self.on_click = on_click
        self.scale = root.winfo_fpixels("1i") / 96.0
        S = self.scale
        self.W, self.H = int(250 * S), int(54 * S)
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", KEY)
        self.win.attributes("-alpha", 0.96)
        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bg=KEY,
                            highlightthickness=0, cursor="hand2")
        self.cv.pack()
        self.cv.bind("<Button-1>", lambda e: self.on_click())
        self._place()
        self.win.update_idletasks()
        self.hwnd = int(self.win.wm_frame(), 16)
        # Never take focus: clicking it must not steal focus from Claude.
        GWL_EXSTYLE, NOACTIVATE, TOOLWINDOW, TOPMOST = -20, 0x08000000, 0x80, 0x8
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | NOACTIVATE | TOOLWINDOW | TOPMOST)
        self.visible = True
        self.hide()
        self.levels = collections.deque([0.0] * self.BARS, maxlen=self.BARS)
        self.mode = None
        self.rec = None
        self._hide_job = None
        self._tick()

    def _place(self):
        l, t, r, b = work_area()
        m = int(16 * self.scale)
        self.win.geometry(f"{self.W}x{self.H}+{r - self.W - m}+{b - self.H - m}")

    def show(self):
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if not self.visible:
            self._place()
            user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(self.hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x0013)  # topmost, no move/size/activate
            self.visible = True

    def hide(self):
        if self.visible:
            user32.ShowWindow(self.hwnd, 0)
            self.visible = False
        self.mode = None

    def recording(self, recorder):
        self.rec = recorder
        self.levels.extend([0.0] * self.BARS)
        self.mode = "rec"
        self.show()

    def transcribing(self):
        self.mode = "busy"
        self.busy_start = time.time()
        self.show()

    def message(self, text, color=T.OK_TEXT, seconds=2.2):
        self.mode = "msg"
        self.msg = (text, color)
        self.show()
        self._draw()
        self._hide_job = self.root.after(int(seconds * 1000), self.hide)

    # -- drawing
    def _pill(self):
        S, W, H = self.scale, self.W, self.H
        r = H / 2
        c = T.BG
        self.cv.create_oval(0, 0, H, H, fill=c, outline=c)
        self.cv.create_oval(W - H, 0, W, H, fill=c, outline=c)
        self.cv.create_rectangle(r, 0, W - r, H, fill=c, outline=c)

    def _tick(self):
        if self.mode == "rec" and self.rec:
            self.levels.append(self.rec.take_level())
        if self.mode:
            self._draw()
        self.root.after(33, self._tick)

    def _draw(self):
        cv, S, W, H = self.cv, self.scale, self.W, self.H
        cv.delete("all")
        if not self.mode:
            return
        self._pill()
        font = ("Segoe UI Semibold", 11)
        if self.mode == "rec":
            pulse = 0.55 + 0.45 * abs(((time.time() * 1.6) % 2) - 1)
            red = "#%02x%02x%02x" % (int(255 * pulse), int(70 * pulse), int(80 * pulse))
            cx, cy, rr = 24 * S, H / 2, 6 * S
            cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=red, outline="")
            secs = int(time.time() - self.rec.started)
            cv.create_text(40 * S, H / 2, text=f"{secs // 60}:{secs % 60:02d}",
                           fill=T.TEXT, font=font, anchor="w")
            # Bars grow up from a baseline. Like the logo, each bar is a gradient from
            # deep orange-red at the bottom to a tip color that gets hotter (yellow) the taller it is.
            x0, x1 = 92 * S, W - 22 * S
            step = (x1 - x0) / self.BARS
            base = H - 12 * S
            maxh = H - 24 * S
            slice_h = max(1.0, 2 * S)
            bw = step * 0.62
            for i, lv in enumerate(self.levels):
                # Soft curve: quiet speech still shows, loud speech approaches the top
                # without flattening every bar at the ceiling.
                frac = 1 - math.exp(-3.0 * max(0.0, lv) ** 0.7)
                h = max(2 * S, frac * maxh)
                x = x0 + i * step
                tip = _fire_mix(BAR_TOP, BAR_HOT, frac ** 1.4)
                r = bw / 2
                body = max(0.0, h - r)  # straight part; a round cap sits on top like the logo
                n = max(1, int(body / slice_h))
                for k in range(n):
                    y1 = base - k * slice_h
                    y0 = base - body if k == n - 1 else y1 - slice_h
                    cv.create_rectangle(x, y0, x + bw, y1, outline="",
                                        fill=_fire_hex(_fire_mix(BAR_BOTTOM, tip, (k + 1) / n)))
                cv.create_oval(x, base - h, x + bw, base - h + bw, outline="", fill=_fire_hex(tip))
        elif self.mode == "busy":
            n = int((time.time() - self.busy_start) * 6) % 3
            for i in range(3):
                cx = (22 + i * 11) * S
                col = T.EMBER if i == n else T.RAISED
                cv.create_oval(cx - 3.5 * S, H / 2 - 3.5 * S, cx + 3.5 * S, H / 2 + 3.5 * S, fill=col, outline="")
            cv.create_text(62 * S, H / 2, text="Transcribing…", fill=T.TEXT, font=font, anchor="w")
        elif self.mode == "msg":
            text, color = self.msg
            cv.create_text(W / 2, H / 2, text=text, fill=color, font=font, width=W - 30 * S)


# ---------------------------------------------------------------- app
class App:
    def __init__(self):
        self.cfg = Config()
        self.root = tk.Tk()
        self.root.withdraw()
        try:  # every window gets the uWhisper icon (taskbar, Alt+Tab)
            self.root.iconbitmap(default=os.path.join(HERE, "uWhisper.ico"))
        except tk.TclError:
            pass
        self.ui = queue.Queue()
        self.overlay = Overlay(self.root, on_click=lambda: self.toggle())
        self.recorder = Recorder()
        self.state = "idle"
        self.mics = list_mics()

        for k, v in uwhisper_engine.DEFAULTS.items():
            self.cfg.whisper.setdefault(k, v)
        self.engine = uwhisper_engine.Engine(self.cfg.whisper, log=log.warning)
        self.engine_ready = threading.Event()
        self.engine_error = None
        self._load_lock = threading.Lock()
        self._load_gen = 0
        self.reload_engine()

        self.panel = Panel(self)
        self.tray = TrayIcon(APP, ICON_BUSY, f"{APP} - loading speech model…",
                             on_click=lambda: self.ui.put(self.panel.toggle))
        self.tray.run_detached()

        self.hotkey = SystemHotkey(on_press=self._hotkey_pressed)
        self._bind_hotkey()
        self.root.after(30, self._pump)
        self._cleanup_lock = threading.Lock()
        self._cleanup_job = None
        self.root.after(3000, self._cleanup_tick)
        log.info("started; output -> %s", OUT_DIR)

    # -- daily audio clean-up
    CLEANUP_CHECK_MS = 30 * 60 * 1000  # look every 30 minutes; it only runs once per day

    def _cleanup_tick(self):
        try:
            if storage.due(self.cfg.cleanup.get("last_run"), datetime.date.today()):
                self.run_cleanup()
        except Exception:
            log.exception("clean-up check failed")
        self.root.after(self.CLEANUP_CHECK_MS, self._cleanup_tick)

    def run_cleanup(self):
        """Delete audio older than the user's setting (in the background)."""
        self.cfg.cleanup["last_run"] = datetime.date.today().isoformat()
        try:
            self.cfg.save()
        except Exception:
            log.exception("could not save config")
        days = storage.keep_days(self.cfg.cleanup["amount"], self.cfg.cleanup["unit"])
        if days is None:
            return
        busy = self.recorder.path if self.state != "idle" else None

        def work():
            if not self._cleanup_lock.acquire(blocking=False):
                return  # one already running
            try:
                n, freed = storage.delete_old_audio(AUDIO_DIR, days, datetime.datetime.now(),
                                                    keep=[busy], log=log.warning)
                log.info("clean-up: deleted %d recordings older than %d days (%.1f MB)",
                         n, days, freed / 1e6)
            finally:
                self._cleanup_lock.release()
        threading.Thread(target=work, daemon=True).start()

    def set_cleanup(self, amount=None, unit=None):
        c = dict(self.cfg.cleanup)
        if amount is not None:
            c["amount"] = amount
        if unit is not None:
            c["unit"] = unit
        c = storage.normalize_cleanup(c)
        if (c["amount"], c["unit"]) == (self.cfg.cleanup["amount"], self.cfg.cleanup["unit"]):
            return
        self.cfg.cleanup = c
        try:
            self.cfg.save()
        except Exception:
            log.exception("could not save config")
        log.info("keep audio for %s", storage.describe(c["amount"], c["unit"]))
        # Apply it soon, not tomorrow - but not instantly: someone going from "30 days" to
        # "2 weeks" passes through "2 days" on the way, and that must not delete anything.
        if self._cleanup_job:
            self.root.after_cancel(self._cleanup_job)
        self._cleanup_job = self.root.after(self.CLEANUP_DELAY_MS, self._delayed_cleanup)

    CLEANUP_DELAY_MS = 30 * 1000

    def _delayed_cleanup(self):
        self._cleanup_job = None
        if self.panel.win and self.panel.editing == "keep_amount":
            self._cleanup_job = self.root.after(self.CLEANUP_DELAY_MS, self._delayed_cleanup)
            return  # still typing
        self.run_cleanup()

    # -- background model load
    def reload_engine(self):
        self._load_gen += 1
        self.engine_ready.clear()
        self.engine_error = None
        threading.Thread(target=self._load_engine, args=(self._load_gen,), daemon=True).start()
        if hasattr(self, "tray"):
            self._refresh_tray()

    def _load_engine(self, gen):
        with self._load_lock:
            if gen != self._load_gen:
                return  # a newer settings change will load instead
            t = time.time()
            try:
                self.engine.load()
                log.info("model %s ready on %s (%s) in %.1fs", self.engine.model_name,
                         self.engine.device, self.engine.compute_type, time.time() - t)
                self.engine_error = None
            except Exception as e:
                log.exception("model failed to load")
                self.engine_error = e
                self.ui.put(lambda: self.overlay.message(
                    "Speech model failed to load (offline?) - press your shortcut to retry",
                    T.ERR_TEXT, 6))
            if gen == self._load_gen:
                self.engine_ready.set()
        if getattr(self.engine, "gpu_missing", False):
            self.ui.put(self._start_gpu_download)
        self.ui.put(self._refresh_tray)

    # -- GPU support (downloaded on first run, only on PCs with an NVIDIA card)
    gpu_download = None
    _gpu_switch_pending = False

    def _start_gpu_download(self):
        if self.gpu_download and not self.gpu_download.done.is_set():
            return  # already downloading
        if gpu.PACKAGE.get("url") is None:
            return
        log.info("downloading GPU support (%s %s)", gpu.PACKAGE["name"], gpu.PACKAGE["version"])
        self.gpu_download = gpu.Download(on_done=lambda d: self.ui.put(self._gpu_download_done)).start()
        self._gpu_progress_tick()

    def _gpu_progress_tick(self):
        d = self.gpu_download
        if d and not d.done.is_set():
            self._refresh_tray()
            self.root.after(1000, self._gpu_progress_tick)

    def _gpu_download_done(self):
        d = self.gpu_download
        if d.error:
            log.warning("GPU support download failed (will retry next start): %s", d.error)
            self.gpu_download = None
            self._refresh_tray()
            return
        log.info("GPU support downloaded")
        self.gpu_download = None
        self._gpu_switch_pending = True
        self._maybe_switch_to_gpu()

    def _maybe_switch_to_gpu(self):
        """Reload on the GPU once nothing is recording or transcribing."""
        if not self._gpu_switch_pending:
            return
        if self.state != "idle":
            self.root.after(2000, self._maybe_switch_to_gpu)
            return
        self._gpu_switch_pending = False
        self.reload_engine()
        self.overlay.message("GPU support installed - transcription is now much faster")

    # -- whisper settings (called by the panel)
    def set_whisper(self, key, value):
        if self.cfg.whisper.get(key) == value:
            return
        self.cfg.whisper[key] = value
        self.cfg.save()
        log.info("setting %s = %r", key, value)
        if key in uwhisper_engine.RELOAD_KEYS:
            self.reload_engine()

    def reset_whisper(self):
        before = {k: self.cfg.whisper.get(k) for k in uwhisper_engine.RELOAD_KEYS}
        self.cfg.whisper.update(uwhisper_engine.DEFAULTS)
        self.cfg.save()
        if any(before[k] != self.cfg.whisper[k] for k in before):
            self.reload_engine()

    # -- hotkey
    def _hotkey_pressed(self):  # runs on the hotkey thread
        log.info("hotkey pressed")
        self.ui.put(self.toggle)

    def _unbind_hotkey(self):
        self.hotkey.set(None)

    def _bind_hotkey(self):
        ok, err = self.hotkey.set(self.cfg.hotkey)
        if ok:
            log.info("hotkey %s registered with Windows", self.cfg.hotkey)
            return True
        label = "+".join(hotkey_label(self.cfg.hotkey))
        log.warning("could not register hotkey %s: %s", self.cfg.hotkey, err)
        self.ui.put(lambda: self.overlay.message(
            f"{label} is {err} - pick another in the uWhisper window", T.ERR_TEXT, 6))
        return False

    def set_hotkey(self, combo):
        self.cfg.hotkey = combo
        self.cfg.save()
        self._bind_hotkey()
        log.info("hotkey set to %s", combo)
        self._refresh_tray()

    def capture_hotkey(self, done):
        """Wait for the next key combo (off the UI thread), then call done(raw combo)."""
        self._unbind_hotkey()

        def worker():
            try:
                raw = keyboard.read_hotkey(suppress=False)
            except Exception:
                raw = ""
            finally:
                keyboard.unhook_all()  # only listen to the keyboard while capturing
            self.ui.put(lambda: done(raw))
        threading.Thread(target=worker, daemon=True).start()

    def finish_capture(self):
        self._bind_hotkey()

    default_hotkey = HOTKEY
    hotkey_label = staticmethod(hotkey_label)
    normalize_hotkey = staticmethod(normalize_hotkey)

    def _refresh_tray(self):
        if self.state == "recording":
            icon, tip = ICON_REC, f"{APP} - recording ({'+'.join(hotkey_label(self.cfg.hotkey))} to stop)"
        elif self.state == "transcribing" or not self.engine_ready.is_set():
            icon, tip = ICON_BUSY, f"{APP} - working…"
        elif self.gpu_download:
            pct = int(self.gpu_download.progress * 100)
            icon, tip = ICON_IDLE, (f"{APP} - {'+'.join(hotkey_label(self.cfg.hotkey))} to record (CPU)"
                                    f"\nDownloading GPU support… {pct}%")
        elif self.engine_error:
            icon, tip = ICON_REC, f"{APP} - speech model failed to load (press your shortcut to retry)"
        else:
            where = "GPU" if self.engine.device == "cuda" else "CPU"
            icon, tip = ICON_IDLE, f"{APP} - {'+'.join(hotkey_label(self.cfg.hotkey))} to record ({where})"
        self.tray.icon = icon
        self.tray.title = tip

        self.panel.refresh()

    # -- things the tray panel calls
    work_area = staticmethod(work_area)
    resolve_mic = staticmethod(resolve_mic)
    autostart_enabled = staticmethod(autostart_enabled)
    set_autostart = staticmethod(set_autostart)

    def set_mic(self, name):
        self.cfg.device = name
        self.cfg.save()
        log.info("microphone set to %r", name)

    def open_folder(self):
        os.startfile(OUT_DIR)

    def refresh_mics(self):
        if self.state != "idle":
            return
        sd._terminate()
        sd._initialize()
        self.mics = list_mics()

    # -- main-thread queue
    def _pump(self):
        try:
            while True:
                self.ui.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            log.exception("ui task failed")
        self.root.after(30, self._pump)

    # -- record / stop
    def toggle(self):
        if self.state == "idle":
            self._start()
        elif self.state == "recording":
            self._stop()

    def _start(self):
        if not self.engine_ready.is_set():  # first run: model still downloading
            self.overlay.message("Still loading the speech model - try again in a moment",
                                 T.WARN_TEXT, 4)
            return
        if self.engine_error:  # e.g. offline on first run: try again now
            self.overlay.message("Speech model isn't loaded - retrying…", T.WARN_TEXT, 4)
            self.reload_engine()
            return
        self.panel.close()  # frees the mic it was monitoring
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.stamp = stamp
        path = os.path.join(AUDIO_DIR, stamp + ".wav")
        try:
            storage.make_folders(OUT_DIR)  # in case someone deleted it while we ran
            try:
                self.recorder.start(self.cfg.device, path)
            except Exception:
                # Mic may have been unplugged/replugged: refresh the device list and retry once.
                log.warning("mic failed to open, refreshing devices and retrying")
                sd._terminate()
                sd._initialize()
                self.mics = list_mics()
                self.recorder.start(self.cfg.device, path)
        except Exception as e:
            log.exception("could not start recording")
            self.overlay.message(f"Mic error: {e}", T.ERR_TEXT, 4)
            return
        self.state = "recording"
        self.overlay.recording(self.recorder)
        self._refresh_tray()

    MIN_SECONDS = 0.3

    def _stop(self):
        target = user32.GetForegroundWindow()  # where the text should go
        duration = self.recorder.stop()
        path = self.recorder.path
        log.info("saved %s (%.1fs)", path, duration)
        if duration < self.MIN_SECONDS:  # an accidental double-tap
            self._finish("Too short - nothing recorded", T.WARN_TEXT)
            return
        self.state = "transcribing"
        self.overlay.transcribing()
        self._refresh_tray()
        threading.Thread(target=self._transcribe, args=(path, target), daemon=True).start()

    def _transcribe(self, path, target):
        try:
            if not self.engine_ready.wait(600):
                raise RuntimeError("speech model is taking too long to load")
            if self.engine_error:
                raise RuntimeError(f"speech model failed to load: {self.engine_error}")
            t = time.time()
            text = self.engine.transcribe(path)
            log.info("transcribed in %.1fs (%d chars)", time.time() - t, len(text))
        except Exception as e:
            log.exception("transcription failed")
            msg = f"Transcription failed (audio is saved): {e}"
            self.ui.put(lambda: self._finish(msg, T.ERR_TEXT, 5))
            return
        try:  # a failed save must not lose the text
            storage.make_folders(OUT_DIR)
            base = os.path.splitext(os.path.basename(path))[0]
            with open(os.path.join(TEXT_DIR, base + ".txt"), "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            log.exception("could not save transcript")
        self.ui.put(lambda: self._deliver(text, target))

    def _deliver(self, text, target):
        if not text:
            self._finish("No speech detected (audio saved)", T.WARN_TEXT)
            return
        try:
            try:
                pyperclip.copy(text)
            except Exception:  # another app is holding the clipboard
                time.sleep(0.15)
                pyperclip.copy(text)
        except Exception:
            log.exception("clipboard busy")
            self._finish("Clipboard is busy - text saved in the text folder", T.ERR_TEXT, 5)
            return
        try:
            focus_window(target)
            wait_for_modifiers_released()
            keyboard.send("ctrl+v")
            self._finish("✓ Pasted · copied to clipboard")
        except Exception:
            log.exception("paste failed")
            self._finish("Copied to clipboard - press Ctrl+V", T.WARN_TEXT, 4)

    def _finish(self, msg, color=T.OK_TEXT, seconds=2.2):
        self.state = "idle"
        self.overlay.message(msg, color, seconds)
        self._refresh_tray()

    def quit(self):
        if self.state == "recording":
            self.recorder.stop()
        for fn in (self.hotkey.stop, keyboard.unhook_all, self.tray.stop):
            try:
                fn()
            except Exception:
                log.exception("error while quitting")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        App().run()
    except Exception:
        log.exception("fatal error")
        raise
