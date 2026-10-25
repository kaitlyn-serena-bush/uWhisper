"""Tests for hotkey parsing/normalising and Config + App clean-up logic.

uWhisper.pyw is NOT imported (it creates a mutex and touches real folders); the
needed functions/classes are pulled out with `ast` and exec'd into a sandbox.
"""
import ast
import datetime as dt
import json
import os
import sys
import threading
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uwhisper_storage as storage  # noqa: E402

SRC = os.path.join(ROOT, "uWhisper.pyw")
_TREE = ast.parse(open(SRC, encoding="utf-8").read())


def _top(name):
    for n in _TREE.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name:
            return n
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return n
    raise LookupError(name)


def _app_method(name):
    app = _top("App")
    for n in app.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return n
    raise LookupError(name)


class FakeLog:
    def __init__(self):
        self.records = []

    def _add(self, lvl, msg, *a):
        self.records.append((lvl, msg % a if a else msg))

    def info(self, m, *a): self._add("info", m, *a)
    def warning(self, m, *a): self._add("warning", m, *a)
    def exception(self, m, *a): self._add("exception", m, *a)


def sandbox(tmp_path):
    import shutil
    ns = {"os": os, "json": json, "datetime": dt, "threading": threading, "shutil": shutil,
          "storage": storage, "HOTKEY": "ctrl+shift+r",
          "CONF_PATH": str(tmp_path / "config.json"),
          "AUDIO_DIR": str(tmp_path / "audio"), "log": FakeLog()}
    for name in ("MODIFIERS", "normalize_hotkey", "hotkey_label", "Config"):
        exec(compile(ast.Module([_top(name)], []), SRC, "exec"), ns)
    body = [_app_method(n) for n in ("CLEANUP_CHECK_MS", "CLEANUP_DELAY_MS", "_cleanup_tick",
                                     "run_cleanup", "set_cleanup", "_delayed_cleanup")]
    cls = ast.ClassDef(name="AppLogic", bases=[], keywords=[], body=body, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module([cls], [])), SRC, "exec"), ns)
    return types.SimpleNamespace(**ns)


@pytest.fixture
def sb(tmp_path):
    return sandbox(tmp_path)


# ------------------------------------------------------------ normalize_hotkey
@pytest.mark.parametrize("raw,expected", [
    ("ctrl+shift+r", "ctrl+shift+r"),
    ("left ctrl+Right Shift+R", "ctrl+shift+r"),
    ("shift+ctrl+r", "ctrl+shift+r"),
    ("windows+shift+alt+ctrl+k", "ctrl+alt+shift+windows+k"),
    ("control+win+a", "ctrl+windows+a"),
    ("ctrl+left ctrl+right ctrl+r", "ctrl+r"),     # duplicate modifiers
    ("ctrl+ctrl+r", "ctrl+r"),
    (" ctrl + shift + r ", "ctrl+shift+r"),
    ("CTRL+SHIFT+R", "ctrl+shift+r"),
    ("f13", "f13"),                                   # F-key alone is allowed
    ("F24", "f24"),
    ("ctrl+left", "ctrl+left"),                       # arrow key, not a "left " prefix
    ("ctrl+page up", "ctrl+page up"),
    ("r", None),                                      # bare letter hijacks typing
    ("space", None),
    ("esc", None), ("ctrl+esc", None), ("ctrl+escape", None),
    ("ctrl+shift", None),                             # no key
    ("ctrl", None),
    ("", None), ("+", None), ("ctrl++", None),
    ("ctrl+a+b", None),                               # two keys
    ("ctrl+r+r", "ctrl+r"),
])
def test_normalize_hotkey(sb, raw, expected):
    assert sb.normalize_hotkey(raw) == expected


def test_normalize_hotkey_idempotent(sb):
    for raw in ["left ctrl+Right Shift+R", "alt+f4", "windows+space", "f13"]:
        once = sb.normalize_hotkey(raw)
        assert sb.normalize_hotkey(once) == once


def test_normalized_hotkeys_are_parseable(sb):
    """Anything normalize_hotkey accepts should be registrable by uwhisper_hotkey.parse."""
    import uwhisper_hotkey as hk
    for raw in ["ctrl+shift+r", "f13", "alt+space", "ctrl+page down", "ctrl+f24",
                "windows+print screen", "ctrl+alt+shift+f23"]:
        combo = sb.normalize_hotkey(raw)
        assert combo and hk.parse(combo), raw


@pytest.mark.parametrize("raw", ["f0", "f25", "f99", "ctrl+f0"])
def test_normalize_rejects_nonexistent_fkeys(sb, raw):
    # parse() only knows F1-F24; accepting f0/f25 just leads to a registration error.
    assert sb.normalize_hotkey(raw) is None


# ------------------------------------------------------------ hotkey_label
@pytest.mark.parametrize("combo,label", [
    ("ctrl+shift+r", ["Ctrl", "Shift", "R"]),
    ("ctrl+alt+windows+f13", ["Ctrl", "Alt", "Win", "F13"]),
    ("alt+space", ["Alt", "Space"]),
    ("ctrl+page up", ["Ctrl", "Page Up"]),
    ("ctrl+end", ["Ctrl", "END"]),
    ("f1", ["F1"]),
])
def test_hotkey_label(sb, combo, label):
    assert sb.hotkey_label(combo) == label


# ------------------------------------------------------------ uwhisper_hotkey.parse
import uwhisper_hotkey as hk  # noqa: E402


@pytest.mark.parametrize("combo,expected", [
    ("ctrl+shift+r", (0x2 | 0x4, 0x52)),
    ("ctrl+alt+shift+windows+f23", (0xF, 0x6F + 23)),
    ("f1", (0, 0x70)), ("f24", (0, 0x87)),
    ("ctrl+page up", (0x2, 0x21)),
    ("ctrl+space", (0x2, 0x20)),
    ("CTRL + R", (0x2, 0x52)),
])
def test_parse(combo, expected):
    assert hk.parse(combo) == expected


@pytest.mark.parametrize("combo", ["ctrl", "ctrl+shift", "", "f0", "f25", "ctrl+nope",
                                   "left ctrl+r", "ctrl+fx", "ctrl+f"])
def test_parse_rejects(combo):
    r = hk.parse(combo)
    if combo == "ctrl+f":
        assert r is not None  # 'f' is a plain letter key
    else:
        assert r is None


def test_system_hotkey_register_unregister():
    h = hk.SystemHotkey(on_press=lambda: None)
    try:
        ok, err = h.set("ctrl+alt+shift+f23")
        assert ok, err
        assert h.set("ctrl+alt+shift+f23") == (True, "")      # re-register same combo
        ok2, err2 = h.set("ctrl+nonsense")
        assert not ok2 and "nonsense" in err2
        assert h.set(None) == (True, "")
        # after clearing, someone else (us, directly) can take it
        ok3, _ = h.set("ctrl+alt+shift+f22")
        assert ok3
    finally:
        h.set(None)
        h.stop()
        h._thread.join(3)
    assert not h._thread.is_alive()


# ------------------------------------------------------------ Config
def write_conf(sb, data, raw=False):
    with open(sb.CONF_PATH, "w", encoding="utf-8") as f:
        f.write(data if raw else json.dumps(data))


def test_config_missing_file(sb):
    c = sb.Config()
    assert c.hotkey == "ctrl+shift+r" and c.device is None and c.whisper == {}
    assert c.cleanup == storage.DEFAULT_CLEANUP
    assert not sb.log.records


@pytest.mark.parametrize("raw", ["{not json", "", "[1,2]", "null", "42", '"str"'])
def test_config_corrupt(sb, raw):
    write_conf(sb, raw, raw=True)
    c = sb.Config()
    assert c.hotkey == "ctrl+shift+r" and c.cleanup == storage.DEFAULT_CLEANUP
    assert any(l == "exception" for l, _ in sb.log.records)


def test_config_round_trip(sb):
    c = sb.Config()
    c.device = "Mic (USB)"
    c.hotkey = "ctrl+alt+f13"
    c.whisper = {"model": "small"}
    c.cleanup = storage.normalize_cleanup({"amount": 2, "unit": "weeks", "last_run": "2026-10-01"})
    c.save()
    assert not os.path.exists(sb.CONF_PATH + ".tmp")
    d = sb.Config()
    assert (d.device, d.hotkey, d.whisper, d.cleanup) == (c.device, c.hotkey, c.whisper, c.cleanup)


@pytest.mark.parametrize("cleanup,expected", [
    ({"amount": "abc", "unit": "days"}, (30, "days")),
    ({"amount": -4, "unit": "weeks"}, (1, "weeks")),
    ({"amount": 0, "unit": "days"}, (1, "days")),
    ({"amount": 10**9, "unit": "days"}, (storage.MAX_AMOUNT, "days")),
    ({"amount": 2.5, "unit": "months"}, (2, "months")),
    ({"amount": 3, "unit": "fortnight"}, (3, "days")),
    ({"amount": 3, "unit": "forever"}, (3, "forever")),
    ("garbage", (30, "days")),
    (None, (30, "days")),
])
def test_config_cleanup_wrong_types(sb, cleanup, expected):
    write_conf(sb, {"cleanup": cleanup})
    c = sb.Config()
    assert (c.cleanup["amount"], c.cleanup["unit"]) == expected


def test_config_last_run_int(sb):
    write_conf(sb, {"cleanup": {"amount": 5, "unit": "days", "last_run": 20261024}})
    assert sb.Config().cleanup["last_run"] is None


def test_config_bad_cleanup_does_not_lose_other_settings(sb):
    write_conf(sb, {"device": "Mic", "hotkey": "ctrl+alt+k",
                    "cleanup": {"amount": 5, "unit": ["days"]}})
    c = sb.Config()
    assert c.device == "Mic" and c.hotkey == "ctrl+alt+k"
    assert c.cleanup["amount"] == 5  # the valid part of cleanup should survive


def test_config_infinity_amount(sb):
    write_conf(sb, '{"cleanup": {"amount": Infinity, "unit": "days"}}', raw=True)
    c = sb.Config()
    assert 1 <= c.cleanup["amount"] <= storage.MAX_AMOUNT


@pytest.mark.parametrize("hotkey,expected", [
    (5, "ctrl+shift+r"), ([], "ctrl+shift+r"), (None, "ctrl+shift+r"), ("", "ctrl+shift+r"),
    ("r", "ctrl+shift+r"), ("esc", "ctrl+shift+r"), ({"a": 1}, "ctrl+shift+r"),
    ("ctrl+alt+k", "ctrl+alt+k"),
])
def test_config_bad_hotkey(sb, hotkey, expected):
    write_conf(sb, {"hotkey": hotkey})
    assert sb.Config().hotkey == expected


def test_config_hotkey_is_stored_normalized(sb):
    """A hand-edited 'Left Ctrl+Shift+R' passes validation but parse() can't register it."""
    write_conf(sb, {"hotkey": "Left Ctrl+Shift+R"})
    c = sb.Config()
    assert hk.parse(c.hotkey) is not None, c.hotkey


@pytest.mark.parametrize("whisper", ["str", [1], 5])
def test_config_bad_whisper(sb, whisper):
    write_conf(sb, {"whisper": whisper})
    assert sb.Config().whisper == {}


def test_config_save_failure_keeps_old_file(sb, monkeypatch):
    write_conf(sb, {"hotkey": "ctrl+alt+k"})
    c = sb.Config()
    c.whisper = {"bad": object()}  # not JSON serialisable -> dump fails midway
    with pytest.raises(TypeError):
        c.save()
    assert json.load(open(sb.CONF_PATH, encoding="utf-8"))["hotkey"] == "ctrl+alt+k"


# ------------------------------------------------------------ App clean-up logic
class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))
        return len(self.after_calls)

    def after_cancel(self, job):
        self.after_calls[job - 1] = (None, None)


class FakeRecorder:
    path = None


def make_app(sb, tmp_path, **cleanup):
    app = sb.AppLogic.__new__(sb.AppLogic)
    app.cfg = sb.Config()
    if cleanup:
        app.cfg.cleanup = storage.normalize_cleanup(cleanup)
    app.root = FakeRoot()
    app.recorder = FakeRecorder()
    app.state = "idle"
    app._cleanup_lock = threading.Lock()
    app._cleanup_job = None
    app.panel = types.SimpleNamespace(win=None, editing=None)
    return app


def wait_threads():
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon and t.name.startswith("Thread"):
            t.join(5)


def wav(sb, when, data=b"x"):
    os.makedirs(sb.AUDIO_DIR, exist_ok=True)
    p = os.path.join(sb.AUDIO_DIR, when.strftime(storage.STAMP_FMT) + ".wav")
    open(p, "wb").write(data)
    return p


def test_run_cleanup_deletes_and_records_date(sb, tmp_path):
    old = wav(sb, dt.datetime.now() - dt.timedelta(days=40))
    new = wav(sb, dt.datetime.now() - dt.timedelta(days=5))
    app = make_app(sb, tmp_path, amount=30, unit="days")
    app.run_cleanup()
    wait_threads()
    assert not os.path.exists(old) and os.path.exists(new)
    assert app.cfg.cleanup["last_run"] == dt.date.today().isoformat()
    saved = json.load(open(sb.CONF_PATH, encoding="utf-8"))
    assert saved["cleanup"]["last_run"] == dt.date.today().isoformat()


def test_run_cleanup_forever_deletes_nothing(sb, tmp_path):
    old = wav(sb, dt.datetime(2000, 1, 1))
    app = make_app(sb, tmp_path, amount=1, unit="forever")
    app.run_cleanup()
    wait_threads()
    assert os.path.exists(old)
    assert app.cfg.cleanup["last_run"] == dt.date.today().isoformat()


def test_run_cleanup_keeps_file_being_recorded(sb, tmp_path):
    busy = wav(sb, dt.datetime(2000, 1, 1))
    other = wav(sb, dt.datetime(2000, 1, 2))
    app = make_app(sb, tmp_path, amount=1, unit="days")
    app.state = "recording"
    app.recorder.path = busy
    app.run_cleanup()
    wait_threads()
    assert os.path.exists(busy) and not os.path.exists(other)


def test_run_cleanup_idle_ignores_stale_recorder_path(sb, tmp_path):
    p = wav(sb, dt.datetime(2000, 1, 1))
    app = make_app(sb, tmp_path, amount=1, unit="days")
    app.recorder.path = p  # last recording's path, but we are idle
    app.run_cleanup()
    wait_threads()
    assert not os.path.exists(p)


def test_run_cleanup_save_failure_still_cleans(sb, tmp_path):
    p = wav(sb, dt.datetime(2000, 1, 1))
    app = make_app(sb, tmp_path, amount=1, unit="days")
    app.cfg.save = lambda: (_ for _ in ()).throw(OSError("disk full"))
    app.run_cleanup()
    wait_threads()
    assert not os.path.exists(p)
    assert any(l == "exception" for l, _ in sb.log.records)


def test_run_cleanup_already_running_is_skipped(sb, tmp_path):
    p = wav(sb, dt.datetime(2000, 1, 1))
    app = make_app(sb, tmp_path, amount=1, unit="days")
    app._cleanup_lock.acquire()
    try:
        app.run_cleanup()
        wait_threads()
        assert os.path.exists(p)
    finally:
        app._cleanup_lock.release()


def test_cleanup_tick_runs_once_per_day(sb, tmp_path, monkeypatch):
    app = make_app(sb, tmp_path, amount=30, unit="days")
    calls = []
    monkeypatch.setattr(app, "run_cleanup", lambda: (calls.append(1),
                        app.cfg.cleanup.__setitem__("last_run", dt.date.today().isoformat())))
    app._cleanup_tick()
    app._cleanup_tick()
    assert calls == [1]
    assert [ms for ms, _ in app.root.after_calls] == [app.CLEANUP_CHECK_MS] * 2


def test_cleanup_tick_survives_errors(sb, tmp_path, monkeypatch):
    app = make_app(sb, tmp_path)

    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(app, "run_cleanup", boom)
    app._cleanup_tick()
    assert app.root.after_calls  # still reschedules
    assert any(l == "exception" for l, _ in sb.log.records)


def test_cleanup_tick_not_due(sb, tmp_path, monkeypatch):
    app = make_app(sb, tmp_path, last_run=dt.date.today().isoformat())
    monkeypatch.setattr(app, "run_cleanup", lambda: pytest.fail("should not run"))
    app._cleanup_tick()


def test_set_cleanup_applies_after_delay(sb, tmp_path):
    p = wav(sb, dt.datetime.now() - dt.timedelta(days=10))
    app = make_app(sb, tmp_path, amount=30, unit="days",
                   last_run=dt.date.today().isoformat())
    app.set_cleanup(amount=1, unit="weeks")
    wait_threads()
    assert os.path.exists(p), "must not delete instantly (user may still be typing)"
    ms, fn = app.root.after_calls[-1]
    assert ms == app.CLEANUP_DELAY_MS
    fn()
    wait_threads()
    assert (app.cfg.cleanup["amount"], app.cfg.cleanup["unit"]) == (1, "weeks")
    assert not os.path.exists(p)
    assert json.load(open(sb.CONF_PATH, encoding="utf-8"))["cleanup"]["unit"] == "weeks"


def test_set_cleanup_no_change_is_noop(sb, tmp_path, monkeypatch):
    app = make_app(sb, tmp_path, amount=30, unit="days")
    monkeypatch.setattr(app, "run_cleanup", lambda: pytest.fail("should not run"))
    app.set_cleanup(amount=30)
    app.set_cleanup(amount="30")
    app.set_cleanup(unit="fortnight")  # normalises back to days -> unchanged
    app.set_cleanup()


@pytest.mark.parametrize("amount,expected", [("abc", 30), (-1, 1), (0, 1), (10**6, 9999),
                                             (3.7, 3), ("7", 7)])
def test_set_cleanup_bad_amount(sb, tmp_path, monkeypatch, amount, expected):
    app = make_app(sb, tmp_path, amount=5, unit="days")
    monkeypatch.setattr(app, "run_cleanup", lambda: None)
    app.set_cleanup(amount=amount)
    assert app.cfg.cleanup["amount"] == expected


def test_set_cleanup_preserves_last_run(sb, tmp_path, monkeypatch):
    app = make_app(sb, tmp_path, amount=5, unit="days", last_run="2026-01-01")
    monkeypatch.setattr(app, "run_cleanup", lambda: None)
    app.set_cleanup(unit="weeks")
    assert app.cfg.cleanup["last_run"] == "2026-01-01"


def test_set_cleanup_forever_then_back(sb, tmp_path):
    p = wav(sb, dt.datetime(2000, 1, 1))
    t = os.path.join(str(tmp_path), "text", "2000-01-01_00-00-00.txt")
    os.makedirs(os.path.dirname(t))
    open(t, "w").write("hi")
    app = make_app(sb, tmp_path, amount=30, unit="days")
    app.set_cleanup(unit="forever")
    wait_threads()
    assert os.path.exists(p)
    app.set_cleanup(amount=1, unit="years")
    wait_threads()
    assert os.path.exists(p)  # deletion waits a moment in case the user is still choosing
    app.root.after_calls[-1][1]()
    wait_threads()
    assert not os.path.exists(p)
    assert os.path.exists(t)  # transcripts never deleted


def test_run_cleanup_huge_setting_does_not_kill_worker(sb, tmp_path):
    """9999 years overflows datetime; the worker thread must not die with a traceback."""
    p = wav(sb, dt.datetime(2000, 1, 1))
    app = make_app(sb, tmp_path, amount=9999, unit="years")
    errors = []
    old_hook = threading.excepthook
    threading.excepthook = lambda a: errors.append(a.exc_value)
    try:
        app.run_cleanup()
        wait_threads()
    finally:
        threading.excepthook = old_hook
    assert os.path.exists(p)
    assert not errors, errors
