"""The uWhisper window that opens when you click the tray icon.

Drawn by hand on a canvas so it looks like a Windows 11 flyout (dark card,
rounded corners, hover highlights, animated switches) instead of a plain menu.
Two pages: the main page and the Whisper settings page (gear icon).
"""
import ctypes
import math
import os
import re
import time
import tkinter as tk
import tkinter.font as tkfont

import numpy as np
import sounddevice as sd
from PIL import ImageTk

from uwhisper_logo import draw_icon
import uwhisper_storage as storage

KEEP_UNITS = {"days": "Days", "weeks": "Weeks", "months": "Months", "years": "Years",
              storage.FOREVER: "Forever"}

user32 = ctypes.windll.user32
HERE = getattr(__import__("sys"), "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# colors (shared with the recording pill)
from uwhisper_theme import (ACCENT, BG, CARD, DIM, EMBER, GREEN, HOVER, LINE, ON_ACCENT, POPUP,
                            POPUP_EDGE, QUIT, RAISED, RED, SCROLL, SUB, TEXT, TIP, TIP_EDGE)
from uwhisper_theme import BORDER_DWM as BORDER
AMBER = EMBER
ICONS = "Segoe Fluent Icons"
GLYPH_UP, GLYPH_DOWN, GLYPH_CHECK = "", "", ""

FRAME_MS = 16          # ~60 fps animations
SWITCH_SECONDS = 0.18  # how long a switch takes to slide
MIC_ROWS_VISIBLE = 3.5
SEGMENT_W = 180        # Language buttons width (wide enough for "Auto-detect")
from uwhisper_theme import SWITCH_EDGE_ON  # dark brown switch border when on

# Whisper settings page rows: (key, label, info, kind, options)
#   info: text for the (i) hover bubble next to the label, or None
#   dropdown: options = [(value, label)]   slider: options = (min, max, step)
#   segment:  options = [(value, label)]   switch: options = None
SETTINGS = [
    ("device", "Processor", None, "dropdown",
     [("auto", "Auto"), ("cuda", "GPU"), ("cpu", "CPU")]),
    ("cpu_threads", "CPU threads", "How many CPU cores transcription uses. All is fastest.", "dropdown",
     [(0, "All"), (4, "4"), (8, "8"), (12, "12"), (16, "16"), (24, "24")]),
    ("model", "Model", None, "dropdown",
     [("large-v3-turbo", "Large v3 Turbo"), ("large-v3", "Large v3"),
      ("distil-large-v3", "Distil Large v3"), ("medium.en", "Medium (EN)"),
      ("small.en", "Small (EN)"), ("base.en", "Base (EN)")]),
    ("compute_type", "Precision", None, "dropdown",
     [("auto", "Auto"), ("float16", "FP16"), ("int8_float16", "INT8 + FP16"),
      ("int8", "INT8"), ("float32", "FP32")]),
    ("batch_size", "Parallel chunks",
     "Audio is sliced into chunks processed at once. Higher is faster, but may split words.",
     "slider", (2, 64, 2)),
    ("beam_size", "Beam size",
     "How many candidate transcripts Whisper compares. Higher is more accurate, but slower.",
     "slider", (1, 10, 1)),
    ("language", "Language", None, "segment",
     [("en", "English"), (None, "Auto-detect")]),
    ("vad", "Skip silence", "Skips pauses for extra speed, but may drop very quiet words.", "switch", None),
]

# Hover blurbs for dropdown choices.
OPTION_INFO = {
    "model": {
        "large-v3-turbo": "Fast and accurate. Best everyday choice on a GPU.",
        "large-v3": "Most accurate, but slower and uses more memory.",
        "distil-large-v3": "Fast English model, nearly as accurate as Large v3.",
        "medium.en": "Balanced English model for weaker GPUs.",
        "small.en": "Light and quick. Good for CPU-only computers.",
        "base.en": "Smallest and fastest. Lower accuracy, fine for quick notes.",
    },
    "compute_type": {
        "auto": "Picks the best option for your hardware.",
        "float16": "Full GPU speed with no noticeable accuracy loss.",
        "int8_float16": "Uses less GPU memory, with a tiny accuracy trade-off.",
        "int8": "Fastest on CPU. Slightly less accurate.",
        "float32": "Maximum precision, but slowest and uses the most memory.",
    },
}
MODEL_NAMES = dict(SETTINGS[2][4])
PRECISION = {"float16": "FP16", "int8_float16": "INT8 + FP16", "int8": "INT8",
             "float32": "FP32", "int8_float32": "INT8"}


_icon_handles = []  # keep loaded icons alive for the life of the app


def set_exact_icons(hwnd):
    """Give the window its icon at exactly the pixel sizes the taskbar and title use,
    so Windows never has to resize it (resizing is what made it blurry)."""
    path = os.path.join(HERE, "uWhisper.ico")
    try:
        dpi = user32.GetDpiForWindow(hwnd) or 96
    except Exception:
        dpi = 96
    big = round(24 * dpi / 96)                       # Windows 11 taskbar icon (72 px at 300%)
    small = user32.GetSystemMetrics(49) or round(16 * dpi / 96)  # SM_CXSMICON
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for which, px in ((1, big), (0, small)):          # ICON_BIG, ICON_SMALL
        h = user32.LoadImageW(None, path, 1, px, px, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if h:
            _icon_handles.append(h)
            user32.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON


def split_name(name):
    """'Analog (1+2) (RME Fireface UCX II)' -> ('Analog (1+2)', 'RME Fireface UCX II')"""
    m = re.match(r"^(.*\S)\s+\(([^()]+)\)$", name)
    return (m.group(1), m.group(2)) if m else (name, "")


def ease(t):
    return t * t * (3 - 2 * t)  # smoothstep


def mix(c1, c2, t):
    """Blend two #rrggbb colors."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class Panel:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.S = self.root.winfo_fpixels("1i") / 96.0
        self.win = None
        self.view = "main"
        self.capturing = False
        self.capture_note = None   # (text, color) shown briefly under the keys
        self.anims = {}            # key -> [current 0..1, target 0..1]
        self.monitor = None
        self.raw_level = 0.0       # loudest mic block since the last frame
        self.last_raw = 0.0
        self.shown_level = 0.0     # smoothed value the meter draws

    # ================================================================ open/close
    def toggle(self):
        """Tray click: open the window, or bring it to the front if it's already open."""
        if not self.win:
            self.open()
            return
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def open(self):
        S = self.S
        self.W = int(320 * S)
        self.view = "main"
        self.hover = None
        self.rows = []
        self.meter = None
        self.pos = None             # always open centered
        self.drag = None
        self.dropdown = None        # settings key whose dropdown is open
        self.slider_drag = None     # (key, lo, hi, step, x0, x1)
        self.slider_live = None     # (key, value) while dragging
        self.editing = None         # settings key whose number is being typed
        self.scroll = 0.0
        self.scroll_target = 0.0

        self.win = tk.Toplevel(self.root)
        self.win.title("uWhisper")
        self.win.overrideredirect(True)  # our own dark title area instead of the Windows one
        self.win.configure(bg=BG)
        try:
            self.win.iconbitmap(os.path.join(HERE, "uWhisper.ico"))
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, width=self.W, height=10, bg=BG, highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Motion>", self._on_move)
        self.cv.bind("<Leave>", lambda e: self._set_hover(None))
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.win.bind("<Escape>", self._on_escape)
        self.win.bind("<MouseWheel>", self._on_wheel)

        # Scrollable microphone list lives in its own small canvas (it clips for free).
        self.mc = tk.Canvas(self.cv, bg=BG, highlightthickness=0)
        self.mc.bind("<Motion>", self._mic_move)
        self.mc.bind("<Leave>", lambda e: self._set_hover(None))
        self.mc.bind("<ButtonPress-1>", self._mic_click)

        self.entry = None
        self.body_font = tkfont.Font(root=self.root, family="Segoe UI", size=10)
        self.logo_img = ImageTk.PhotoImage(draw_icon(int(44 * S)))
        self.gate_open = False
        self._draw()
        self._place()
        self.win.update_idletasks()
        hwnd = int(self.win.wm_frame(), 16)
        # Show in the taskbar and Alt+Tab like a normal app window.
        GWL_EXSTYLE, TOOLWINDOW, APPWINDOW = -20, 0x80, 0x40000
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~TOOLWINDOW) | APPWINDOW)
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)  # rounded corners (Windows 11)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        border = ctypes.c_uint(BORDER)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
        set_exact_icons(hwnd)
        self.win.withdraw()   # re-show so the taskbar picks up the new style
        self.win.deiconify()
        self.win.focus_force()
        self._start_monitor()
        self._last_frame = time.time()
        self._tick()

    def close(self):
        if self.capturing:
            self.capturing = False
            self.app.finish_capture()
        self._stop_monitor()
        if self.win:
            self.win.destroy()
            self.win = None

    def refresh(self):
        if self.win:
            self._draw()
            self._place()

    def _place(self):
        """Center on first draw; afterwards keep the top-left where it is (or where it was dragged)."""
        if self.pos is None:
            l, t, r, b = self.app.work_area()
            self.pos = (l + (r - l - self.W) // 2, t + (b - t - self.H) // 2)
        x, y = self.pos
        self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _on_escape(self, e):
        if self.capturing or self.editing:
            return  # key capture / number box handle Esc themselves
        if self.dropdown:
            self.dropdown = None
            self._draw()
        elif self.view == "settings":
            self._go("main")
        else:
            self.close()

    def _go(self, view):
        self.view = view
        self.hover = None
        self.dropdown = None
        self._cancel_edit()
        self.refresh()

    # ================================================================ live mic meter
    def _start_monitor(self):
        self._stop_monitor()
        if self.app.state != "idle":
            return
        try:
            idx, info = self.app.resolve_mic(self.app.cfg.device)

            def cb(indata, frames, t, status):
                if frames:
                    rms = float(np.sqrt(np.mean(indata ** 2)))
                    self.raw_level = max(self.raw_level, rms)

            self.monitor = sd.InputStream(device=idx, channels=min(2, int(info["max_input_channels"])),
                                          samplerate=int(info["default_samplerate"]), callback=cb)
            self.monitor.start()
        except Exception:
            self.monitor = None

    def _stop_monitor(self):
        if self.monitor:
            try:
                self.monitor.stop()
                self.monitor.close()
            except Exception:
                pass
            self.monitor = None
        self.raw_level = self.last_raw = 0.0

    def _meter_target(self):
        if self.app.state == "recording":
            raw = self.app.recorder.level * 0.35  # recorder tracks peaks; roughly rms
        else:
            raw, self.raw_level = self.raw_level, 0.0
            if raw == 0.0:
                raw = self.last_raw  # no new audio block this frame
            self.last_raw = raw
        db = 20 * math.log10(raw + 1e-7)
        # Gate with a little hysteresis: opens above -46 dB, closes below -52 dB.
        if self.gate_open and db < -52:
            self.gate_open = False
        elif not self.gate_open and db > -46:
            self.gate_open = True
        if not self.gate_open:
            return 0.0
        return min(1.0, max(0.0, (db + 52) / 47))  # -52 dB .. -5 dB -> 0..1

    # ================================================================ animation loop
    def _anim(self, key, target):
        """Animated 0..1 value for key that glides toward target."""
        a = self.anims.get(key)
        if a is None:
            self.anims[key] = a = [target, target]
        a[1] = target
        return ease(a[0])

    def _tick(self):
        if not self.win:
            return
        now = time.time()
        dt = now - self._last_frame
        self._last_frame = now
        redraw = False

        for a in self.anims.values():
            if a[0] != a[1]:
                step = dt / SWITCH_SECONDS
                a[0] = min(a[1], a[0] + step) if a[1] > a[0] else max(a[1], a[0] - step)
                redraw = True

        if abs(self.scroll - self.scroll_target) > 0.5:
            self.scroll += (self.scroll_target - self.scroll) * min(1.0, dt * 14)
            if self.view == "main":
                self._draw_mics()
        elif self.scroll != self.scroll_target:
            self.scroll = self.scroll_target
            if self.view == "main":
                self._draw_mics()

        if redraw:
            self._draw()
        if self.meter:
            target = self._meter_target()
            k = 0.35 if target > self.shown_level else 0.08  # quick rise, slow fall
            self.shown_level += (target - self.shown_level) * k
            x0, y0, x1, y1 = self.meter
            frac = self.shown_level
            if frac < 0.012:
                self.shown_level = frac = 0.0
            self.cv.coords("meterfill", x0, y0, x0 + (x1 - x0) * frac, y1)
            self.cv.itemconfigure("meterfill", state="normal" if frac else "hidden",
                                  fill=mix(EMBER, RED, max(0.0, (frac - 0.75) / 0.25)))
        if self.capturing:
            self.cv.itemconfigure("capture", fill=ACCENT if int(now * 2.5) % 2 else TEXT)
        self.win.after(FRAME_MS, self._tick)

    # ================================================================ input
    def _hit(self, y0, y1, key, fn, x0=None, x1=None, kind="click"):
        self.rows.append((y0, y1, key, fn, x0, x1, kind))

    def _row_at(self, x, y):
        rows = self.rows
        if self.dropdown:  # an open dropdown captures all clicks
            rows = [r for r in rows if r[6] == "dd"]
        # Rows with an x range (small buttons) are checked before full-width rows.
        for row in sorted(rows, key=lambda r: r[4] is None):
            y0, y1, _, _, x0, x1, _ = row
            if y0 <= y < y1 and (x0 is None or x0 <= x < x1):
                return row
        return None

    def _on_move(self, e):
        if self.slider_drag or self.drag:
            return
        row = self._row_at(e.x, e.y)
        self._set_hover(row[2] if row else None)

    def _set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self._draw()
            cursor = ""
            if key:
                cursor = "xterm" if isinstance(key, tuple) and key[0] == "num" else "hand2"
            self.cv.configure(cursor=cursor)

    def _on_press(self, e):
        if self.editing:
            self._commit_edit()
        row = self._row_at(e.x, e.y)
        if self.dropdown and not row:
            self.dropdown = None  # click outside an open dropdown closes it
            self._draw()
            return
        if row and row[6] == "slider":
            self.slider_drag = row[3]
            self._slide_to(e.x)
        elif row:
            row[3]()
        else:
            self.drag = (e.x_root - self.pos[0], e.y_root - self.pos[1])  # empty space: move window

    def _on_drag(self, e):
        if self.slider_drag:
            self._slide_to(e.x)
        elif self.drag and self.win:
            self.pos = (e.x_root - self.drag[0], e.y_root - self.drag[1])
            self.win.geometry(f"+{self.pos[0]}+{self.pos[1]}")

    def _on_release(self, e):
        self.drag = None
        if self.slider_drag:
            key = self.slider_drag[0]
            value = self.slider_live[1] if self.slider_live else None
            self.slider_drag = self.slider_live = None
            if value is not None:
                self.app.set_whisper(key, value)
            self._draw()

    def _on_wheel(self, e):
        if self.view != "main" or not self.win:
            return
        w = self.win.winfo_containing(e.x_root, e.y_root)
        if w is not self.mc:
            return
        rh = 46 * self.S
        self.scroll_target = max(0.0, min(self.max_scroll, self.scroll_target - e.delta / 120 * rh * 0.8))

    # ================================================================ actions
    def _pick(self, name):
        self.app.set_mic(name)
        self._start_monitor()
        self._draw()

    def _refresh_list(self):
        self._stop_monitor()
        self.app.refresh_mics()
        self._start_monitor()
        self.scroll = self.scroll_target = 0.0
        self.refresh()

    def _toggle_autostart(self):
        self.app.set_autostart(not self.app.autostart_enabled())
        self._draw()

    def _open_folder(self):
        self.close()
        self.app.open_folder()

    def _quit(self):
        self.close()
        self.app.quit()

    def _start_capture(self):
        if self.capturing or self.app.state != "idle":
            return
        self.capturing = True
        self.capture_note = None
        self._draw()
        self.app.capture_hotkey(self._captured)

    def _captured(self, raw):
        if not self.capturing:
            return  # window was closed meanwhile
        self.capturing = False
        raw_l = raw.lower()
        if raw_l in ("esc", "escape") or not raw_l:
            self.app.finish_capture()
        else:
            combo = self.app.normalize_hotkey(raw)
            if combo:
                self.app.set_hotkey(combo)
                self.capture_note = ("Saved", GREEN)
            else:
                self.app.finish_capture()
                self.capture_note = ("Add Ctrl, Alt, Shift or Win", RED)
            self.win.after(1800, self._clear_note)
        self._draw()

    def _clear_note(self):
        self.capture_note = None
        if self.win:
            self._draw()

    def _reset_hotkey(self):
        self.app.set_hotkey(self.app.default_hotkey)
        self._draw()

    # -- settings controls
    def _toggle_dropdown(self, key):
        self.dropdown = None if self.dropdown == key else key
        self.refresh()

    def _choose(self, key, value):
        self.dropdown = None
        self.app.set_whisper(key, value)
        self.refresh()

    def _flip(self, key):
        self.app.set_whisper(key, not self.app.cfg.whisper.get(key))
        self._draw()

    def _slide_to(self, x):
        key, lo, hi, step, x0, x1 = self.slider_drag
        frac = min(1.0, max(0.0, (x - x0) / (x1 - x0)))
        value = lo + math.floor(frac * (hi - lo) / step + 0.5) * step
        if not self.slider_live or self.slider_live[1] != value:
            self.slider_live = (key, value)
            self._draw()

    def _reset_whisper(self):
        self.dropdown = None
        self.app.reset_whisper()
        self.refresh()

    # -- how long to keep audio (main page)
    def _toggle_keep_dropdown(self):
        self.dropdown = None if self.dropdown == "keep_unit" else "keep_unit"
        self._draw()

    def _choose_keep(self, unit):
        self.dropdown = None
        self.app.set_cleanup(unit=unit)
        self._draw()

    # -- typing a number into a value box
    def _start_edit(self, key, lo, hi, step, value=None, on_commit=None):
        if self.editing:
            self._commit_edit()
        self.editing = key
        self.edit_limits = (lo, hi, step)
        self.edit_commit = on_commit or (lambda v, k=key: self.app.set_whisper(k, v))
        self.entry = tk.Entry(self.cv, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                              justify="center", font=("Segoe UI Semibold", 10),
                              highlightthickness=1, highlightcolor=ACCENT, highlightbackground=ACCENT)
        self.entry.insert(0, str(self.app.cfg.whisper.get(key) if value is None else value))
        self.entry.select_range(0, "end")
        self.entry.bind("<Return>", lambda e: self._commit_edit())
        self.entry.bind("<KP_Enter>", lambda e: self._commit_edit())
        self.entry.bind("<Escape>", lambda e: (self._cancel_edit(), self._draw(), "break")[-1])
        self.entry.bind("<FocusOut>", lambda e: self._commit_edit())
        self._draw()
        self.entry.focus_set()

    def _commit_edit(self):
        if not self.editing:
            return
        lo, hi, step = self.edit_limits
        commit = self.edit_commit
        text = self.entry.get().strip()
        self._cancel_edit()
        try:
            value = float(text)
            if not math.isfinite(value):
                value = None
        except ValueError:
            value = None
        if value is not None:
            value = lo + math.floor((value - lo) / step + 0.5) * step  # odd chunk counts -> next even
            value = int(min(hi, max(lo, value)))
            commit(value)
        if self.win:
            self._draw()

    def _cancel_edit(self):
        if self.entry:
            entry, self.entry, self.editing = self.entry, None, None
            self.cv.delete("entrywin")
            entry.destroy()
            if self.win:
                self.win.focus_force()

    # ================================================================ drawing helpers
    def _rrect(self, x0, y0, x1, y1, r, cv=None, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return (cv or self.cv).create_polygon(pts, smooth=True, **kw)

    def _pill(self, x0, cy, x1, h, fill):
        r = h / 2
        cv = self.cv
        cv.create_oval(x0, cy - r, x0 + h, cy + r, fill=fill, outline="")
        cv.create_oval(x1 - h, cy - r, x1, cy + r, fill=fill, outline="")
        cv.create_rectangle(x0 + r, cy - r, x1 - r, cy + r, fill=fill, outline="")

    def _switch(self, xc, cy, key, on):
        """Toggle switch centered at xc that slides between off and on."""
        S = self.S
        t = self._anim(("switch", key), 1.0 if on else 0.0)
        sw, sh = 36 * S, 18 * S
        x0, x1 = xc - sw / 2, xc + sw / 2
        g = max(1, 1.3 * S)
        self._pill(x0, cy, x1, sh, mix(SUB, SWITCH_EDGE_ON, t))  # grey border off, dark brown on
        self._pill(x0 + g, cy, x1 - g, sh - 2 * g, BG)   # dark inside
        kx = (x0 + sh / 2) + (sw - sh) * t
        if t > 0.005:  # purple slides in behind the knob as it moves right
            self._pill(x0 + g, cy, kx + sh / 2 - g, sh - 2 * g, mix(BG, ACCENT, min(1.0, t * 3)))
        kr = 5 * S
        self.cv.create_oval(kx - kr, cy - kr, kx + kr, cy + kr, fill=mix(SUB, ON_ACCENT, t), outline="")

    def _icon_button(self, key, x, cy, glyph, fn, size=11):
        S = self.S
        r = 14 * S
        if self.hover == key:
            self.cv.create_oval(x - r, cy - r, x + r, cy + r, fill=HOVER, outline="")
        self.cv.create_text(x, cy, text=glyph, font=(ICONS, size), fill=TEXT if self.hover == key else SUB)
        self._hit(cy - r, cy + r, key, fn, x - r, x + r)

    def _status(self):
        app = self.app
        if app.state == "recording":
            return RED, "Recording…"
        if app.state == "transcribing":
            return AMBER, "Transcribing…"
        if not app.engine_ready.is_set():
            return AMBER, "Loading speech model…"
        if app.engine_error:
            return RED, "Speech model failed to load"
        dl = getattr(app, "gpu_download", None)
        if dl:
            return GREEN, f"Ready  ·  CPU  ·  getting GPU support {int(dl.progress * 100)}%"
        return GREEN, "Ready  ·  " + ("GPU" if app.engine.device == "cuda" else "CPU")

    # ================================================================ drawing
    def _draw(self):
        # Embedded widgets (mic list, number box) are kept, not recreated, so they
        # don't flicker or lose keyboard focus on every redraw.
        for item in self.cv.find_all():
            if "keep" not in self.cv.gettags(item):
                self.cv.delete(item)
        if self.view != "main":
            self.cv.delete("micwin")
        if not self.editing:
            self.cv.delete("entrywin")
        self.rows = []
        self.meter = None
        y = self._draw_settings() if self.view == "settings" else self._draw_main()
        self.H = int(y)
        self.cv.configure(height=self.H)
        if self.win and self.pos:
            # Keep the whole window above the taskbar when a dropdown makes it taller.
            l, t, r, b = self.app.work_area()
            self.pos = (self.pos[0], max(t, min(self.pos[1], b - self.H)))
            self.win.geometry(f"{self.W}x{self.H}+{self.pos[0]}+{self.pos[1]}")

    # ---------------------------------------------------------------- main page
    def _draw_main(self):
        cv, S, W, app = self.cv, self.S, self.W, self.app
        P = 16 * S
        f_title = ("Segoe UI Semibold", 13)
        f_body = ("Segoe UI", 10)
        f_bold = ("Segoe UI Semibold", 10)
        f_small = ("Segoe UI", 8)
        f_italic = ("Segoe UI", 8, "italic")
        f_label = ("Segoe UI Semibold", 8)
        f_icon = (ICONS, 11)

        y = 16 * S
        # header: badge, name, status, gear, close
        b = 34 * S
        cv.create_image(P + b / 2, y + b / 2, image=self.logo_img, anchor="center")
        cv.create_text(P + b + 12 * S, y + 9 * S, text="uWhisper", font=f_title, fill=TEXT, anchor="w")
        dot, status = self._status()
        sy = y + 26 * S
        cv.create_oval(P + b + 12 * S, sy - 3.5 * S, P + b + 19 * S, sy + 3.5 * S, fill=dot, outline="")
        cv.create_text(P + b + 25 * S, sy, text=status, font=f_small, fill=SUB, anchor="w")
        self._icon_button("gear", W - P - 40 * S, y + b / 2, "", lambda: self._go("settings"), 12)
        self._icon_button("close", W - P - 8 * S, y + b / 2, "", self.close, 10)
        y += b + 16 * S

        # hotkey card: label left, keys right, "Click to change" under the keys
        h = 58 * S
        hot = self.hover == "hotkey" or self.capturing
        self._rrect(P, y, W - P, y + h, 8 * S, fill=HOVER if hot else CARD,
                    outline=ACCENT if self.capturing else "", width=max(1, 1.2 * S))
        cy = y + h / 2
        kr = W - P - 12 * S  # right edge of the keys
        if self.capturing:
            cv.create_text(P + 12 * S, cy, text="Press new shortcut…", font=f_bold,
                           fill=ACCENT, anchor="w", tags="capture")
            cv.create_text(kr, cy, text="Esc to cancel", font=f_italic, fill=SUB, anchor="e")
        else:
            cv.create_text(P + 12 * S, cy, text="Record / stop", font=f_body, fill=TEXT, anchor="w")
            ky = cy - 8 * S
            kx = kr
            for key in reversed(app.hotkey_label(app.cfg.hotkey)):
                kw = (len(key) * 7 + 14) * S
                self._rrect(kx - kw, ky - 10 * S, kx, ky + 10 * S, 5 * S, fill=RAISED, outline="")
                cv.create_text(kx - kw / 2, ky, text=key, font=f_label, fill=TEXT)
                kx -= kw + 5 * S
            note, color = self.capture_note or ("Click to change", DIM)
            cv.create_text(kr, cy + 16 * S, text=note, font=f_italic, fill=color, anchor="e")
            if app.cfg.hotkey != app.default_hotkey:
                self._icon_button("hotreset", kx - 12 * S, ky, "", self._reset_hotkey, 9)
        self._hit(y, y + h, "hotkey", self._start_capture)
        y += h + 22 * S

        # microphone section (scrollable list, 3.5 rows tall)
        cv.create_text(P, y, text="MICROPHONE", font=f_label, fill=DIM, anchor="w")
        self._icon_button("refresh", W - P - 6 * S, y, "", self._refresh_list, 9)
        y += 12 * S
        rh = 46 * S
        try:
            default_name = app.resolve_mic(None)[1]["name"]
        except Exception:
            default_name = ""
        self.mic_entries = [(None, "Windows default", split_name(default_name)[0])]
        self.mic_entries += [(n, *split_name(n)) for n in app.mics]
        visible = min(len(self.mic_entries), MIC_ROWS_VISIBLE)
        vh = visible * rh
        self.max_scroll = max(0.0, len(self.mic_entries) * rh - vh)
        self.scroll_target = min(self.scroll_target, self.max_scroll)
        lw = W - 2 * P + 12 * S
        self.mc.configure(width=lw, height=vh)
        if cv.find_withtag("micwin"):
            cv.coords("micwin", P - 6 * S, y)
        else:
            cv.create_window(P - 6 * S, y, window=self.mc, anchor="nw", tags=("keep", "micwin"))
        self.mic_geom = (lw, vh, rh)
        self._draw_mics()
        y += vh + 16 * S

        # live input meter
        cv.create_text(P, y + 4 * S, text="", font=(ICONS, 10), fill=SUB, anchor="w")
        mx0, mx1 = P + 24 * S, W - P
        my0, my1 = y + 1 * S, y + 7 * S
        self._rrect(mx0, my0, mx1, my1, 3 * S, fill=LINE, outline="")
        cv.create_rectangle(mx0, my0, mx0, my1, fill=EMBER, outline="", tags="meterfill")
        self.meter = (mx0, my0, mx1, my1)
        y += 22 * S

        # how long to keep audio: [30] [Days v]  (transcripts are always kept)
        keep_dd = self._draw_keep_row(y)
        y += 46 * S

        cv.create_line(P, y, W - P, y, fill=LINE)
        y += 6 * S

        def action(key, icon, label, fn, color=TEXT, right=None):
            nonlocal y
            ah = 38 * S
            if self.hover == key:
                self._rrect(P - 6 * S, y + 2 * S, W - P + 6 * S, y + ah - 2 * S, 7 * S, fill=HOVER, outline="")
            cv.create_text(P + 9 * S, y + ah / 2, text=icon, font=f_icon, fill=color)
            cv.create_text(P + 28 * S, y + ah / 2, text=label, font=f_body, fill=color, anchor="w")
            if right:
                right(y + ah / 2)
            self._hit(y, y + ah, key, fn)
            y += ah

        action("folder", "", "Open recordings folder", self._open_folder)
        action("autostart", "", "Start with Windows", self._toggle_autostart,
               right=lambda cy: self._switch(W - P - 18 * S, cy, "autostart", app.autostart_enabled()))
        action("quit", "", "Quit uWhisper", self._quit, color=QUIT)
        y += 10 * S
        if keep_dd:  # drawn last so it sits on top of the buttons below it
            y = max(y, self._draw_keep_popup(*keep_dd))
        return y

    def _draw_keep_row(self, y):
        """'Keep audio for  [30] [Days v]'. Returns where the dropdown list goes if it's open."""
        cv, S, W, app = self.cv, self.S, self.W, self.app
        P = 16 * S
        rh = 46 * S
        cy = y + rh / 2
        c = app.cfg.cleanup
        forever = c["unit"] == storage.FOREVER
        self._label(P, cy - 7 * S, "Keep audio for", None, "keep")
        cv.create_text(P, cy + 10 * S, text="Transcripts are always kept", font=("Segoe UI", 8),
                       fill=DIM, anchor="w")

        dw, bw, gap, bh = 92 * S, 52 * S, 6 * S, 26 * S
        dx1 = W - P
        dx0 = dx1 - dw
        is_open = self.dropdown == "keep_unit"
        hov = self.hover == ("keep", "unit")
        self._rrect(dx0, cy - bh / 2, dx1, cy + bh / 2, 6 * S, fill=HOVER if hov else CARD,
                    outline=ACCENT if is_open else "", width=max(1, 1.2 * S))
        cv.create_text(dx0 + 10 * S, cy, text=KEEP_UNITS[c["unit"]], font=("Segoe UI Semibold", 9),
                       fill=TEXT, anchor="w")
        cv.create_text(dx1 - 11 * S, cy, text=GLYPH_UP if is_open else GLYPH_DOWN, font=(ICONS, 8), fill=SUB)
        self._hit(cy - bh / 2, cy + bh / 2, ("keep", "unit"), self._toggle_keep_dropdown, dx0, dx1)

        if not forever:  # number box (click to type)
            bx1 = dx0 - gap
            bx0 = bx1 - bw
            if self.editing == "keep_amount":
                if cv.find_withtag("entrywin"):
                    cv.coords("entrywin", (bx0 + bx1) / 2, cy)
                else:
                    cv.create_window((bx0 + bx1) / 2, cy, window=self.entry, width=bw, height=bh,
                                     tags=("keep", "entrywin"))
            else:
                nkey = ("num", "keep_amount")
                self._rrect(bx0, cy - bh / 2, bx1, cy + bh / 2, 6 * S,
                            fill=HOVER if self.hover == nkey else CARD, outline="")
                cv.create_text((bx0 + bx1) / 2, cy, text=str(c["amount"]),
                               font=("Segoe UI Semibold", 9), fill=TEXT)
                self._hit(cy - bh / 2, cy + bh / 2, nkey,
                          lambda: self._start_edit("keep_amount", 1, storage.MAX_AMOUNT, 1,
                                                   value=app.cfg.cleanup["amount"],
                                                   on_commit=lambda v: app.set_cleanup(amount=v)),
                          bx0, bx1)
        return (dx0, dx1, cy + bh / 2 + 4 * S, c["unit"]) if is_open else None

    def _draw_keep_popup(self, x0, x1, top, cur):
        cv, S = self.cv, self.S
        ih = 32 * S
        x0 -= 20 * S
        bottom = top + ih * len(KEEP_UNITS) + 8 * S
        self._rrect(x0, top, x1, bottom, 8 * S, fill=POPUP, outline=POPUP_EDGE, width=max(1, S))
        iy = top + 4 * S
        for value, text in KEEP_UNITS.items():
            if self.hover == ("dd", value):
                self._rrect(x0 + 4 * S, iy, x1 - 4 * S, iy + ih, 5 * S, fill=HOVER, outline="")
            sel = value == cur
            if sel:
                cv.create_text(x0 + 14 * S, iy + ih / 2, text=GLYPH_CHECK, font=(ICONS, 9), fill=ACCENT)
            cv.create_text(x0 + 28 * S, iy + ih / 2, text=text,
                           font=("Segoe UI Semibold", 9) if sel else ("Segoe UI", 10), fill=TEXT, anchor="w")
            self._hit(iy, iy + ih, ("dd", value), lambda v=value: self._choose_keep(v), x0, x1, kind="dd")
            iy += ih
        return bottom + 12 * S

    def _draw_mics(self):
        mc, S, app = self.mc, self.S, self.app
        lw, vh, rh = self.mic_geom
        mc.delete("all")
        f_body = ("Segoe UI", 10)
        f_bold = ("Segoe UI Semibold", 10)
        f_small = ("Segoe UI", 8)
        L = 6 * S  # left inset (list canvas starts 6px left of the page padding)
        for i, (name, title, sub) in enumerate(self.mic_entries):
            y = i * rh - self.scroll
            if y > vh or y + rh < 0:
                continue
            if self.hover == ("mic", name):
                self._rrect(0, y + 2 * S, lw - (8 * S if self.max_scroll else 0), y + rh - 2 * S,
                            7 * S, cv=mc, fill=HOVER, outline="")
            sel = app.cfg.device == name
            cx, cy, r = L + 9 * S, y + rh / 2, 8 * S
            if sel:
                mc.create_oval(cx - r, cy - r, cx + r, cy + r, fill=ACCENT, outline="")
                mc.create_oval(cx - 3.5 * S, cy - 3.5 * S, cx + 3.5 * S, cy + 3.5 * S, fill=ON_ACCENT, outline="")
            else:
                mc.create_oval(cx - r, cy - r, cx + r, cy + r, outline=DIM, width=max(1, 1.5 * S))
            tx = L + 28 * S
            if sub:
                mc.create_text(tx, cy - 8 * S, text=title, font=f_bold if sel else f_body, fill=TEXT, anchor="w")
                mc.create_text(tx, cy + 9 * S, text=sub, font=f_small, fill=SUB, anchor="w")
            else:
                mc.create_text(tx, cy, text=title, font=f_bold if sel else f_body, fill=TEXT, anchor="w")
        if self.max_scroll:  # slim scrollbar
            total = len(self.mic_entries) * rh
            th = max(20 * S, vh * vh / total)
            ty = (vh - th) * (self.scroll / self.max_scroll)
            x = lw - 3 * S
            self._rrect(x - 2 * S, ty + 2 * S, x + 2 * S, ty + th - 2 * S, 2 * S, cv=mc, fill=SCROLL, outline="")

    def _mic_at(self, y):
        rh = self.mic_geom[2]
        i = int((y + self.scroll) // rh)
        return self.mic_entries[i][0] if 0 <= i < len(self.mic_entries) else "__none__"

    def _mic_move(self, e):
        name = self._mic_at(e.y)
        self._set_hover(None if name == "__none__" else ("mic", name))
        self.mc.configure(cursor="hand2" if name != "__none__" else "")

    def _mic_click(self, e):
        name = self._mic_at(e.y)
        if name != "__none__":
            self._pick(name)

    # ---------------------------------------------------------------- settings page
    def _show_cpu_threads(self):
        d = self.app.cfg.whisper.get("device")
        return d == "cpu" or (d == "auto" and self.app.engine.device == "cpu")

    def _draw_settings(self):
        cv, S, W, app = self.cv, self.S, self.W, self.app
        P = 16 * S
        f_title = ("Segoe UI Semibold", 12)
        f_body = ("Segoe UI", 10)
        f_small = ("Segoe UI", 8)
        f_value = ("Segoe UI Semibold", 9)

        y = 16 * S
        # header: back, title, what's loaded right now, close
        hy = y + 17 * S
        self._icon_button("back", P + 8 * S, hy, "", lambda: self._go("main"), 11)
        self._icon_button("close", W - P - 8 * S, hy, "", self.close, 10)
        cv.create_text(P + 30 * S, y + 9 * S, text="Whisper AI Model Settings", font=f_title, fill=TEXT, anchor="w")
        dot, status = self._status()
        if dot == GREEN and app.engine.model_name:
            status = "  ·  ".join([MODEL_NAMES.get(app.engine.model_name, app.engine.model_name),
                                   "GPU" if app.engine.device == "cuda" else "CPU",
                                   PRECISION.get(app.engine.compute_type, app.engine.compute_type)])
        sy = y + 27 * S
        cv.create_oval(P + 30 * S, sy - 3.5 * S, P + 37 * S, sy + 3.5 * S, fill=dot, outline="")
        cv.create_text(P + 43 * S, sy, text=status, font=f_small, fill=SUB, anchor="w")
        y += 50 * S

        vw = 136 * S          # width of the value column
        vx1 = W - P
        vx0 = vx1 - vw
        vxc = (vx0 + vx1) / 2
        open_dd = None         # drawn last so it sits on top

        self._tooltip = None   # (text, x, y) of the info bubble to draw last

        for key, label, info, kind, opts in SETTINGS:
            if key == "cpu_threads" and not self._show_cpu_threads():
                continue
            cur = app.cfg.whisper.get(key)

            if kind == "slider":
                lo, hi, step = opts
                rh = 66 * S
                if self.slider_live and self.slider_live[0] == key:
                    cur = self.slider_live[1]
                ly = y + 20 * S
                self._label(P, ly, label, info, key)
                # number box (click to type)
                bw, bh = 56 * S, 26 * S
                bx0, by0 = vx1 - bw, ly - bh / 2
                nkey = ("num", key)
                if self.editing == key:
                    if cv.find_withtag("entrywin"):
                        cv.coords("entrywin", (bx0 + vx1) / 2, by0 + bh / 2)
                    else:
                        cv.create_window((bx0 + vx1) / 2, by0 + bh / 2, window=self.entry,
                                         width=bw, height=bh, tags=("keep", "entrywin"))
                else:
                    self._rrect(bx0, by0, vx1, by0 + bh, 6 * S,
                                fill=HOVER if self.hover == nkey else CARD, outline="")
                    cv.create_text((bx0 + vx1) / 2, by0 + bh / 2, text=str(cur), font=f_value, fill=TEXT)
                    self._hit(by0, by0 + bh, nkey, lambda k=key, o=opts: self._start_edit(k, *o), bx0, vx1)
                # track + knob
                ty = y + 50 * S
                tx0, tx1 = P + 8 * S, W - P - 8 * S
                frac = (min(hi, max(lo, cur)) - lo) / (hi - lo)
                kx = tx0 + (tx1 - tx0) * frac
                self._pill(tx0, ty, tx1, 4 * S, RAISED)
                self._pill(tx0, ty, max(tx0 + 4 * S, kx), 4 * S, ACCENT)
                active = self.hover == ("slider", key) or (self.slider_drag and self.slider_drag[0] == key)
                kr = (9 if active else 8) * S
                cv.create_oval(kx - kr, ty - kr, kx + kr, ty + kr, fill=ON_ACCENT, outline=ACCENT,
                               width=max(1, 2 * S))
                self._hit(ty - 14 * S, ty + 14 * S, ("slider", key), (key, lo, hi, step, tx0, tx1),
                          P - 4 * S, W - P + 4 * S, kind="slider")
                y += rh
                continue

            rh = 46 * S
            cy = y + rh / 2
            if kind in ("dropdown", "switch") and self.hover == (key, "row"):
                self._rrect(P - 6 * S, y + 2 * S, W - P + 6 * S, y + rh - 2 * S, 7 * S, fill=HOVER, outline="")
            self._label(P, cy, label, info, key)

            if kind == "dropdown":
                labels = dict(opts)
                is_open = self.dropdown == key
                self._rrect(vx0, cy - 13 * S, vx1, cy + 13 * S, 6 * S, fill=CARD,
                            outline=ACCENT if is_open else "", width=max(1, 1.2 * S))
                cv.create_text(vx0 + 12 * S, cy, text=labels.get(cur, str(cur)), font=f_value, fill=TEXT, anchor="w")
                cv.create_text(vx1 - 12 * S, cy, text="" if is_open else "",
                               font=(ICONS, 8), fill=SUB)
                self._hit(y, y + rh, (key, "row"), lambda k=key: self._toggle_dropdown(k))
                if is_open:
                    open_dd = (key, opts, cur, cy + 16 * S)
            elif kind == "segment":
                values = [v for v, _ in opts]
                idx = values.index(cur) if cur in values else 0
                t = self._anim(("seg", key), float(idx))
                h = 26 * S
                sw = SEGMENT_W * S  # a bit wider than other values so "Auto-detect" fits
                sx = vx1 - sw
                self._rrect(sx, cy - h / 2, vx1, cy + h / 2, 6 * S, fill=CARD, outline="")
                half = sw / 2
                hx = sx + 2 * S + half * t
                self._rrect(hx, cy - h / 2 + 2 * S, hx + half - 4 * S, cy + h / 2 - 2 * S, 5 * S,
                            fill=ACCENT, outline="")
                for i, (value, text) in enumerate(opts):
                    sx0 = sx + half * i
                    sel = i == idx
                    hov = self.hover == (key, i)
                    cv.create_text(sx0 + half / 2, cy, text=text, font=f_value,
                                   fill=ON_ACCENT if sel else (TEXT if hov else SUB))
                    self._hit(cy - h / 2, cy + h / 2, (key, i), lambda k=key, v=value: self._choose(k, v),
                              sx0, sx0 + half)
            elif kind == "switch":
                # right-aligned with the controls above it
                self._switch(vx1 - 18 * S, cy, key, bool(cur))
                self._hit(y, y + rh, (key, "row"), lambda k=key: self._flip(k))
            y += rh

        y += 6 * S
        cv.create_line(P, y, W - P, y, fill=LINE)
        y += 6 * S
        ah = 38 * S
        if self.hover == "wreset":
            self._rrect(P - 6 * S, y + 2 * S, W - P + 6 * S, y + ah - 2 * S, 7 * S, fill=HOVER, outline="")
        cv.create_text(P + 9 * S, y + ah / 2, text="", font=(ICONS, 11), fill=TEXT)
        cv.create_text(P + 28 * S, y + ah / 2, text="Reset to defaults", font=f_body, fill=TEXT, anchor="w")
        self._hit(y, y + ah, "wreset", self._reset_whisper)
        y += ah + 10 * S

        if open_dd:  # dropdown list on top of everything else
            key, opts, cur, top = open_dd
            ih = 32 * S
            x0, x1 = vx0 - 44 * S, vx1
            bottom = top + ih * len(opts) + 8 * S
            self._rrect(x0, top, x1, bottom, 8 * S, fill=POPUP, outline=POPUP_EDGE, width=max(1, S))
            iy = top + 4 * S
            blurbs = OPTION_INFO.get(key, {})
            for value, text in opts:
                if self.hover == ("dd", value):
                    self._rrect(x0 + 4 * S, iy, x1 - 4 * S, iy + ih, 5 * S, fill=HOVER, outline="")
                sel = value == cur
                if sel:
                    cv.create_text(x0 + 16 * S, iy + ih / 2, text="", font=(ICONS, 9), fill=ACCENT)
                cv.create_text(x0 + 32 * S, iy + ih / 2, text=text, font=f_value if sel else f_body,
                               fill=TEXT, anchor="w")
                tip = blurbs.get(value)
                if tip:
                    ix, ikey = x1 - 18 * S, ("info", key, value)
                    hov = self.hover == ikey
                    cv.create_text(ix, iy + ih / 2, text="", font=(ICONS, 9), fill=TEXT if hov else DIM)
                    self._hit(iy, iy + ih, ikey, lambda: None, ix - 12 * S, ix + 12 * S, kind="dd")
                    if hov:
                        self._tooltip = (tip, ix, iy + ih / 2)
                self._hit(iy, iy + ih, ("dd", value), lambda k=key, v=value: self._choose(k, v),
                          x0, x1, kind="dd")
                iy += ih
            y = max(y, bottom + 12 * S)

        if self._tooltip:
            y = max(y, self._draw_tooltip(*self._tooltip, page_bottom=y))
        return y

    def _label(self, x, cy, label, info, key):
        """Row label, with an (i) icon after it that shows a bubble on hover."""
        S = self.S
        self.cv.create_text(x, cy, text=label, font=("Segoe UI", 10), fill=TEXT, anchor="w")
        if not info:
            return
        ix = x + self.body_font.measure(label) + 12 * S
        ikey = ("info", key)
        hov = self.hover == ikey
        self.cv.create_text(ix, cy + 1 * S, text="", font=(ICONS, 9), fill=TEXT if hov else DIM)
        self._hit(cy - 11 * S, cy + 11 * S, ikey, lambda: None, ix - 11 * S, ix + 11 * S)
        if hov:
            self._tooltip = (info, ix, cy)

    def _draw_tooltip(self, text, ax, ay, page_bottom):
        """Small rounded bubble near (ax, ay). Returns its bottom edge."""
        cv, S, W = self.cv, self.S, self.W
        pad, maxw = 10 * S, 210 * S
        item = cv.create_text(0, 0, text=text, font=("Segoe UI", 9), fill=TEXT, anchor="nw", width=maxw)
        bx0, by0, bx1, by1 = cv.bbox(item)
        tw, th = bx1 - bx0, by1 - by0
        bw, bh = tw + 2 * pad, th + 2 * pad
        x = min(max(8 * S, ax - 16 * S), W - bw - 8 * S)
        y = ay + 16 * S
        if y + bh > page_bottom - 6 * S:  # no room below: put it above
            y = ay - 16 * S - bh
        bubble = self._rrect(x, y, x + bw, y + bh, 7 * S, fill=TIP, outline=TIP_EDGE, width=max(1, S))
        cv.coords(item, x + pad, y + pad)
        cv.tag_raise(item, bubble)
        return y + bh + 8 * S
