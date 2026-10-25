"""GUI tests for the tray window (uwhisper_panel.Panel).

Runs on the real desktop: a small uWhisper window briefly appears.
Never imports uWhisper.pyw; uses a FakeApp with everything Panel touches.
No microphone is opened (monitor start/stop are patched out).
"""
import ctypes
import os
import sys
import threading
import tkinter as tk
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:  # same as the real app, so screenshot coordinates are real pixels
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import uwhisper_panel as up  # noqa: E402
import uwhisper_storage as storage  # noqa: E402

SHOTS = os.path.join(HERE, "screenshots")
HOTKEY = "ctrl+shift+space"


# ---------------------------------------------------------------- fake app
class FakeApp:
    default_hotkey = HOTKEY

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.cfg = SimpleNamespace(
            device=None,
            hotkey=HOTKEY,
            whisper={"device": "auto", "cpu_threads": 0, "model": "large-v3-turbo",
                     "compute_type": "auto", "batch_size": 16, "beam_size": 5,
                     "language": "en", "vad": True},
            cleanup={"amount": 30, "unit": "days", "last_run": None},
        )
        self.state = "idle"
        self.mics = ["Microphone (Realtek(R) Audio)", "Analog (1+2) (RME Fireface UCX II)",
                     "Headset Mic (USB Audio)", "Line In (Focusrite)"]
        self.engine = SimpleNamespace(device="cuda", model_name="large-v3-turbo", compute_type="float16")
        self.engine_ready = threading.Event()
        self.engine_ready.set()
        self.engine_error = None
        self.recorder = SimpleNamespace(level=0.0)
        self._autostart = False

    @staticmethod
    def hotkey_label(combo):
        names = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "windows": "Win"}
        return [names.get(p, p.upper() if len(p) <= 3 else p.title()) for p in combo.split("+")]

    @staticmethod
    def normalize_hotkey(combo):
        parts = combo.lower().split("+")
        return combo.lower() if len(parts) > 1 else None

    def work_area(self):
        return (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 48)

    def resolve_mic(self, name):
        return 3, {"name": name or "Microphone (Realtek(R) Audio)", "max_input_channels": 2,
                   "default_samplerate": 48000}

    def autostart_enabled(self):
        return self._autostart

    def set_autostart(self, on):
        self.calls.append(("set_autostart", on))
        self._autostart = on

    def set_mic(self, name):
        self.calls.append(("set_mic", name))
        self.cfg.device = name

    def set_hotkey(self, combo):
        self.calls.append(("set_hotkey", combo))
        self.cfg.hotkey = combo

    def set_whisper(self, key, value):
        self.calls.append(("set_whisper", key, value))
        self.cfg.whisper[key] = value

    def reset_whisper(self):
        self.calls.append(("reset_whisper",))

    def set_cleanup(self, amount=None, unit=None):  # mirrors uWhisper.App.set_cleanup
        self.calls.append(("set_cleanup", amount, unit))
        c = dict(self.cfg.cleanup)
        if amount is not None:
            c["amount"] = amount
        if unit is not None:
            c["unit"] = unit
        self.cfg.cleanup = storage.normalize_cleanup(c)

    def open_folder(self):
        self.calls.append(("open_folder",))

    def refresh_mics(self):
        self.calls.append(("refresh_mics",))

    def capture_hotkey(self, cb):
        self.calls.append(("capture_hotkey",))
        self._capture_cb = cb

    def finish_capture(self):
        self.calls.append(("finish_capture",))

    def quit(self):
        self.calls.append(("quit",))

    def cleanup_calls(self):
        return [c for c in self.calls if c[0] == "set_cleanup"]


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def tk_errors(root):
    errors = []
    old = root.report_callback_exception
    root.report_callback_exception = lambda exc, val, tb: errors.append((exc, val))
    yield errors
    root.report_callback_exception = old


@pytest.fixture
def panel(root, tk_errors, monkeypatch):
    monkeypatch.setattr(up.Panel, "_start_monitor", lambda self: None)
    monkeypatch.setattr(up.Panel, "_stop_monitor", lambda self: None)
    monkeypatch.setattr(up.sd, "InputStream", None)  # any real mic use would blow up
    app = FakeApp(root)
    p = up.Panel(app)
    p.open()
    pump(p)
    yield p
    if p.win:
        p.close()
    pump(p)
    assert not tk_errors, tk_errors


def pump(p):
    p.root.update()
    p.root.update_idletasks()


def row(p, key):
    rows = [r for r in p.rows if r[2] == key]
    return rows[0] if rows else None


def click(p, key):
    r = row(p, key)
    assert r is not None, f"no hit region {key!r}; have {[x[2] for x in p.rows]}"
    y0, y1, _, _, x0, x1, _ = r
    x = (x0 + x1) / 2 if x0 is not None else p.W / 2
    y = (y0 + y1) / 2
    click_xy(p, x, y)


def click_xy(p, x, y):
    p.cv.event_generate("<Motion>", x=int(x), y=int(y))
    cv = p.cv
    cv.event_generate("<ButtonPress-1>", x=int(x), y=int(y))
    if p.win and cv.winfo_exists():  # the click may have closed the window
        cv.event_generate("<ButtonRelease-1>", x=int(x), y=int(y))
    pump(p)


def type_and(p, text, how="return"):
    assert p.entry is not None and p.editing
    p.entry.delete(0, "end")
    p.entry.insert(0, text)
    if how == "return":
        p.entry.event_generate("<Return>")
    elif how == "kp":
        p.entry.event_generate("<KP_Enter>")
    elif how == "focusout":
        p.entry.event_generate("<FocusOut>")
    elif how == "escape":
        p.entry.event_generate("<Escape>")
    pump(p)


def shot(p, name):
    from PIL import ImageGrab
    pump(p)
    p.win.lift()
    p.win.attributes("-topmost", True)
    pump(p)
    p.win.after(250)  # let DWM compose
    pump(p)
    x, y = p.win.winfo_rootx(), p.win.winfo_rooty()
    w, h = p.win.winfo_width(), p.win.winfo_height()
    os.makedirs(SHOTS, exist_ok=True)
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(os.path.join(SHOTS, name))
    p.win.attributes("-topmost", False)
    return img


# ---------------------------------------------------------------- open/close/pages
def test_open_draw_pages_close_reopen(panel):
    p = panel
    assert p.view == "main"
    assert row(p, ("keep", "unit")) and row(p, ("num", "keep_amount"))
    p._draw()
    click(p, "gear")
    assert p.view == "settings"
    click(p, "back")
    assert p.view == "main"
    click(p, "close")
    assert p.win is None
    p.open()
    pump(p)
    assert p.win is not None and p.view == "main"
    p.toggle()
    pump(p)
    # escape on main page closes
    p.win.event_generate("<Escape>")
    pump(p)
    assert p.win is None


def test_window_height_matches_canvas(panel):
    p = panel
    assert p.win.winfo_height() == p.H
    assert int(p.cv["height"]) == p.H


# ---------------------------------------------------------------- keep row: unit dropdown
@pytest.mark.parametrize("unit", ["days", "weeks", "months", "years", storage.FOREVER])
def test_keep_unit_choose(panel, unit):
    p = panel
    h0 = p.H
    click(p, ("keep", "unit"))
    assert p.dropdown == "keep_unit"
    assert p.H > h0, "window should grow to fit the popup"
    for v in up.KEEP_UNITS:
        assert row(p, ("dd", v)) is not None
    click(p, ("dd", unit))
    assert p.dropdown is None
    assert p.app.cleanup_calls()[-1] == ("set_cleanup", None, unit)
    assert p.app.cfg.cleanup["unit"] == unit
    has_num = row(p, ("num", "keep_amount")) is not None
    assert has_num == (unit != storage.FOREVER)
    assert p.H == h0


def test_forever_then_back_restores_number_box(panel):
    p = panel
    click(p, ("keep", "unit"))
    click(p, ("dd", storage.FOREVER))
    assert row(p, ("num", "keep_amount")) is None
    click(p, ("keep", "unit"))
    click(p, ("dd", "weeks"))
    assert row(p, ("num", "keep_amount")) is not None
    assert p.app.cfg.cleanup["amount"] == 30


def test_keep_dropdown_toggle_escape_outside(panel):
    p = panel
    click(p, ("keep", "unit"))
    assert p.dropdown == "keep_unit"
    # only dropdown items are clickable while open: clicking the gear just closes it
    click(p, "gear")
    assert p.dropdown is None and p.view == "main"
    click(p, ("keep", "unit"))
    p.win.event_generate("<Escape>")
    pump(p)
    assert p.dropdown is None and p.win is not None
    # clicking the button again (it's under the popup? no, above) — reopen then click empty space
    click(p, ("keep", "unit"))
    click_xy(p, 3, 3)
    assert p.dropdown is None
    assert not p.app.cleanup_calls()


def test_keep_popup_is_top_and_inside_window(panel):
    p = panel
    click(p, ("keep", "unit"))
    items = [row(p, ("dd", v)) for v in up.KEEP_UNITS]
    bottom = max(r[1] for r in items)
    assert bottom <= p.H
    # the last canvas items drawn are the popup's (on top of action rows)
    quit_row = row(p, "quit")
    dd_rows = [r for r in p.rows if r[6] == "dd"]
    assert p.rows.index(dd_rows[0]) > p.rows.index(quit_row)
    # popup x range stays inside the window
    for r in items:
        assert r[4] >= 0 and r[5] <= p.W


# ---------------------------------------------------------------- keep row: number box
AMOUNT_CASES = [
    ("7", 7), ("0", 1), ("-5", 1), ("abc", None), ("1e9", 9999), ("nan", None),
    ("inf", None), ("-inf", None), ("", None), ("12.6", 13), (" 42 ", 42), ("9999", 9999),
    ("10000", 9999), ("1e400", None), ("0x10", None), ("1,000", None),
]


@pytest.mark.parametrize("how", ["return", "focusout"])
@pytest.mark.parametrize("text,expected", AMOUNT_CASES)
def test_keep_amount_typing(panel, text, expected, how):
    p = panel
    click(p, ("num", "keep_amount"))
    assert p.editing == "keep_amount"
    assert p.entry.get() == "30"
    type_and(p, text, how)
    assert p.editing is None and p.entry is None
    calls = p.app.cleanup_calls()
    if expected is None:
        assert calls == []
        assert p.app.cfg.cleanup["amount"] == 30
    else:
        assert calls == [("set_cleanup", expected, None)]
        assert isinstance(calls[0][1], int)
        assert p.app.cfg.cleanup["amount"] == expected
    assert row(p, ("num", "keep_amount")) is not None


def test_keep_amount_escape_cancels(panel):
    p = panel
    click(p, ("num", "keep_amount"))
    type_and(p, "5", "escape")
    assert p.editing is None and p.win is not None, "Esc in the box must not close the window"
    assert p.app.cleanup_calls() == []


def test_keep_amount_then_open_dropdown(panel):
    p = panel
    click(p, ("num", "keep_amount"))
    p.entry.delete(0, "end")
    p.entry.insert(0, "3")
    click(p, ("keep", "unit"))   # commits the box, then opens the dropdown
    assert p.editing is None
    assert p.dropdown == "keep_unit"
    assert ("set_cleanup", 3, None) in p.app.cleanup_calls()
    click(p, ("dd", "months"))
    assert p.app.cfg.cleanup == {"amount": 3, "unit": "months", "last_run": None}


def test_dropdown_open_then_click_number(panel):
    p = panel
    click(p, ("keep", "unit"))
    click(p, ("num", "keep_amount"))   # popup is modal: closes, doesn't start edit
    assert p.dropdown is None and p.editing is None


def test_close_while_editing(panel, tk_errors):
    p = panel
    click(p, ("num", "keep_amount"))
    p.entry.insert("end", "5")
    p.close()
    pump(p)
    assert p.win is None
    assert not tk_errors
    p.open()
    pump(p)
    assert p.editing is None and p.entry is None
    click(p, ("num", "keep_amount"))
    type_and(p, "8")
    assert p.app.cfg.cleanup["amount"] == 8


def test_close_button_while_editing(panel, tk_errors):
    p = panel
    click(p, ("num", "keep_amount"))
    p.entry.delete(0, "end")
    p.entry.insert(0, "11")
    click(p, "close")
    assert p.win is None
    assert not tk_errors
    assert ("set_cleanup", 11, None) in p.app.cleanup_calls()


def test_go_settings_while_editing_keep(panel):
    p = panel
    click(p, ("num", "keep_amount"))
    p.win.event_generate("<Escape>")  # window-level Esc ignored while editing
    pump(p)
    assert p.win is not None
    p._go("settings")
    pump(p)
    assert p.editing is None and not p.cv.find_withtag("entrywin")
    p._go("main")
    pump(p)


def test_redraw_while_editing_keeps_entry(panel):
    p = panel
    click(p, ("num", "keep_amount"))
    e = p.entry
    p.entry.insert("end", "1")
    for _ in range(3):
        p._draw()
    pump(p)
    assert p.entry is e and p.cv.find_withtag("entrywin")
    type_and(p, "14")
    assert p.app.cfg.cleanup["amount"] == 14


def test_bad_cleanup_cfg_does_not_crash_draw(panel):
    """Panel reads cfg.cleanup straight; the App normalizes it on load."""
    p = panel
    p.app.cfg.cleanup = {"amount": 1, "unit": "years", "last_run": None}
    p._draw()
    p.app.cfg.cleanup = {"amount": 9999, "unit": storage.FOREVER, "last_run": "2026-01-01"}
    p._draw()


# ---------------------------------------------------------------- settings page regression
def test_settings_dropdowns(panel):
    p = panel
    click(p, "gear")
    for key, _, _, kind, opts in up.SETTINGS:
        if kind != "dropdown" or row(p, (key, "row")) is None:
            continue
        click(p, (key, "row"))
        assert p.dropdown == key
        value = opts[-1][0]
        click(p, ("dd", value))
        assert p.dropdown is None
        assert p.app.cfg.whisper[key] == value
    # Escape closes an open dropdown, then the page, then the window
    click(p, ("model", "row"))
    p.win.event_generate("<Escape>")
    pump(p)
    assert p.dropdown is None and p.view == "settings"
    p.win.event_generate("<Escape>")
    pump(p)
    assert p.view == "main"


def test_settings_cpu_threads_shown_for_cpu(panel):
    p = panel
    click(p, "gear")
    assert row(p, ("cpu_threads", "row")) is None
    click(p, ("device", "row"))
    click(p, ("dd", "cpu"))
    assert row(p, ("cpu_threads", "row")) is not None


def test_settings_slider(panel):
    p = panel
    click(p, "gear")
    r = row(p, ("slider", "beam_size"))
    key, lo, hi, step, x0, x1 = r[3]
    y = (r[0] + r[1]) / 2
    p.cv.event_generate("<ButtonPress-1>", x=int(x1 + 3), y=int(y))
    p.cv.event_generate("<B1-Motion>", x=int(x0 - 50), y=int(y))
    p.cv.event_generate("<B1-Motion>", x=int((x0 + x1) / 2), y=int(y))
    p.cv.event_generate("<ButtonRelease-1>", x=int((x0 + x1) / 2), y=int(y))
    pump(p)
    v = p.app.cfg.whisper["beam_size"]
    assert lo <= v <= hi and v in (5, 6)
    # direct _slide_to clamps
    p.slider_drag = r[3]
    p._slide_to(-1000)
    assert p.slider_live[1] == lo
    p._slide_to(10000)
    assert p.slider_live[1] == hi
    p.slider_drag = p.slider_live = None
    # batch_size step 2 stays even
    r = row(p, ("slider", "batch_size"))
    p.slider_drag = r[3]
    for x in range(int(r[3][4]), int(r[3][5]), 7):
        p._slide_to(x)
        assert p.slider_live[1] % 2 == 0
    p.slider_drag = p.slider_live = None


@pytest.mark.parametrize("text,expected", [("7", 8), ("33", 34), ("1", 2), ("999", 64), ("x", None)])
def test_settings_number_box(panel, text, expected):
    p = panel
    click(p, "gear")
    before = p.app.cfg.whisper["batch_size"]
    click(p, ("num", "batch_size"))
    assert p.entry.get() == str(before)
    type_and(p, text)
    got = p.app.cfg.whisper["batch_size"]
    assert got == (before if expected is None else expected)


def test_settings_segment_and_switch(panel):
    p = panel
    click(p, "gear")
    click(p, ("language", 1))
    assert p.app.cfg.whisper["language"] is None
    click(p, ("language", 0))
    assert p.app.cfg.whisper["language"] == "en"
    click(p, ("vad", "row"))
    assert p.app.cfg.whisper["vad"] is False
    click(p, ("vad", "row"))
    assert p.app.cfg.whisper["vad"] is True
    click(p, "wreset")
    assert ("reset_whisper",) in p.app.calls


def test_main_page_regression(panel):
    p = panel
    click(p, "autostart")
    assert p.app.autostart_enabled()
    click(p, "hotkey")
    assert p.capturing
    p.app._capture_cb("ctrl+alt+k")
    pump(p)
    assert p.app.cfg.hotkey == "ctrl+alt+k"
    click(p, "hotreset")
    assert p.app.cfg.hotkey == HOTKEY
    click(p, "refresh")
    assert ("refresh_mics",) in p.app.calls


# ---------------------------------------------------------------- screenshots
def test_screenshots(panel):
    p = panel
    shot(p, "main.png")
    click(p, ("keep", "unit"))
    p._set_hover(("dd", "weeks"))
    shot(p, "main_keep_dropdown.png")
    click(p, ("dd", storage.FOREVER))
    shot(p, "main_forever.png")
    click(p, ("keep", "unit"))
    click(p, ("dd", "days"))
    click(p, ("num", "keep_amount"))
    shot(p, "main_editing.png")
    p.entry.event_generate("<Escape>")
    pump(p)
    click(p, "gear")
    shot(p, "settings.png")
    click(p, ("model", "row"))
    shot(p, "settings_dropdown.png")
