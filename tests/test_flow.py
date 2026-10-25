"""End-to-end flow / robustness tests for uWhisper.

uWhisper.pyw is NOT imported (it grabs a single-instance mutex and touches the real
Documents/Music folders). Instead the Recorder class and selected App methods are
pulled out with `ast` and executed in a controlled namespace with fakes.

Run:  .venv\\Scripts\\python.exe -m pytest tests\\test_flow.py -v
"""
import ast
import datetime
import logging
import os
import queue
import sys
import threading
import time
import types

import numpy as np
import pytest
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uwhisper_storage as storage  # noqa: E402  (plain Python, safe)

SRC = open(os.path.join(ROOT, "uWhisper.pyw"), encoding="utf-8").read()
TREE = ast.parse(SRC)


def _node(name, kind=ast.ClassDef, tree=TREE):
    for n in tree.body:
        if isinstance(n, kind) and n.name == name:
            return n
    raise LookupError(name)


def _compile(nodes, ns):
    mod = ast.Module(body=list(nodes), type_ignores=[])
    exec(compile(mod, "uWhisper.pyw(extract)", "exec"), ns)
    return ns


# ---------------------------------------------------------------- fakes
class FakeStream:
    """Stands in for sd.InputStream; feeds `blocks` to the callback on start()."""
    blocks = []
    fail_on_init = None
    fail_on_start = None
    fail_on_stop = None
    instances = []

    def __init__(self, device=None, samplerate=None, channels=None, dtype=None, callback=None):
        if FakeStream.fail_on_init:
            raise FakeStream.fail_on_init
        self.callback = callback
        self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        if FakeStream.fail_on_start:
            raise FakeStream.fail_on_start
        for b in FakeStream.blocks:
            self.callback(b, len(b), None, None)

    def stop(self):
        if FakeStream.fail_on_stop:
            raise FakeStream.fail_on_stop

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeStream.blocks = []
    FakeStream.fail_on_init = FakeStream.fail_on_start = FakeStream.fail_on_stop = None
    FakeStream.instances = []
    yield


def recorder_ns():
    ns = {
        "np": np, "sf": sf, "queue": queue, "threading": threading, "time": time, "os": os,
        "log": logging.getLogger("test"),
        "sd": types.SimpleNamespace(InputStream=FakeStream),
        "resolve_mic": lambda name: (0, {"name": "Fake mic", "default_samplerate": 48000,
                                         "max_input_channels": 1}),
    }
    return _compile([_node("Recorder")], ns)


def threads_named(target_name):
    return [t for t in threading.enumerate()
            if getattr(t, "_target", None) and getattr(t._target, "__name__", "") == target_name]


# ---------------------------------------------------------------- Recorder
def test_recorder_happy_path(tmp_path):
    ns = recorder_ns()
    FakeStream.blocks = [np.full((480, 1), 0.25, np.float32) for _ in range(10)]
    r = ns["Recorder"]()
    p = str(tmp_path / "a.wav")
    r.start(None, p)
    r.stop()
    data, rate = sf.read(p, dtype="float32")
    assert rate == 48000 and len(data) == 4800
    assert np.allclose(data, 0.25, atol=1e-3)


def test_recorder_float_above_full_scale_is_clipped_not_wrapped(tmp_path):
    """WASAPI shared-mode float input can exceed +/-1.0 (mic boost, APOs).
    libsndfile does NOT clip float->PCM16 by default; values wrap around."""
    ns = recorder_ns()
    FakeStream.blocks = [np.full((480, 1), 1.5, np.float32), np.full((480, 1), -1.5, np.float32)]
    r = ns["Recorder"]()
    p = str(tmp_path / "loud.wav")
    r.start(None, p)
    r.stop()
    data, _ = sf.read(p, dtype="float32")
    # A clipped write would give ~ +1.0 then ~ -1.0.
    assert data[:480].min() > 0.9, f"positive overload wrapped to {data[:480].min():.3f}"
    assert data[480:].max() < -0.9, f"negative overload wrapped to {data[480:].max():.3f}"


def test_recorder_start_failure_leaks_writer_thread_and_open_file(tmp_path):
    """sd.InputStream raising (device busy / unplugged / bad format) after the
    SoundFile is opened and the writer thread started leaves both behind."""
    ns = recorder_ns()
    FakeStream.fail_on_init = RuntimeError("Error opening InputStream: Invalid device")
    r = ns["Recorder"]()
    p = str(tmp_path / "fail.wav")
    before = len(threads_named("_write_loop"))
    with pytest.raises(RuntimeError):
        r.start(None, p)
    leaked = len(threads_named("_write_loop")) - before
    assert leaked == 0, f"leaked writer threads={leaked}"
    assert not os.path.exists(p), "stray empty .wav left behind"
    assert r.stream is None


def test_recorder_stop_failure_then_stop_again(tmp_path):
    """stream.stop() raising (mic unplugged mid-recording -> PortAudioError).
    The exception escapes stop(); a second stop() then hits self.stream=None."""
    ns = recorder_ns()
    FakeStream.blocks = [np.zeros((480, 1), np.float32)]
    r = ns["Recorder"]()
    r.start(None, str(tmp_path / "x.wav"))
    FakeStream.fail_on_stop = RuntimeError("Error stopping stream: Unanticipated host error")
    r.stop()  # must not raise: the file is finished and the writer thread joined
    assert not threads_named("_write_loop") or all(not t.is_alive() for t in threads_named("_write_loop"))
    # A retry (user presses the hotkey again, or Quit) must not explode.
    r.stop()


# ---------------------------------------------------------------- App state machine
class FakeOverlay:
    def __init__(self):
        self.calls = []

    def recording(self, rec): self.calls.append("recording")
    def transcribing(self): self.calls.append("transcribing")
    def message(self, *a): self.calls.append(("message",) + a)


def app_ns(tmp_path, **extra):
    ns = recorder_ns()
    audio, text = storage.make_folders(str(tmp_path / "out"))
    ns.update({
        "datetime": datetime, "os": os, "storage": storage,
        "AUDIO_DIR": audio, "TEXT_DIR": text, "OUT_DIR": str(tmp_path / "out"),
        "wait_for_modifiers_released": lambda: None, "list_mics": lambda: [],
        "T": types.SimpleNamespace(ERR_TEXT="r", WARN_TEXT="y", OK_TEXT="g"),
        "user32": types.SimpleNamespace(GetForegroundWindow=lambda: 1234),
        "focus_window": lambda hwnd: None,
        "keyboard": types.SimpleNamespace(send=lambda k: None, unhook_all=lambda: None),
        "pyperclip": types.SimpleNamespace(copy=lambda t: None),
    })
    ns.update(extra)
    app_cls = _node("App")
    wanted = {"toggle", "_start", "_stop", "_transcribe", "_deliver", "_finish", "quit",
              "run_cleanup"}
    body = [f for f in app_cls.body if isinstance(f, ast.FunctionDef) and f.name in wanted]
    body.append(ast.parse("MIN_SECONDS = 0").body[0])  # fake recordings are instant
    cls = ast.ClassDef(name="App", bases=[], keywords=[], body=body, decorator_list=[])
    ast.fix_missing_locations(cls)
    _compile([cls], ns)
    App = ns["App"]
    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(device=None, cleanup=dict(storage.DEFAULT_CLEANUP),
                                    save=lambda: None)
    app.overlay = FakeOverlay()
    app.recorder = ns["Recorder"]()
    app.state = "idle"
    app.ui = queue.Queue()
    app.panel = types.SimpleNamespace(close=lambda: None)
    app._refresh_tray = lambda: None
    app.engine_ready = threading.Event(); app.engine_ready.set()
    app.engine_error = None
    app.engine = types.SimpleNamespace(transcribe=lambda p: "hello world")
    app._cleanup_lock = threading.Lock()
    app._cleanup_job = None
    app.hotkey = types.SimpleNamespace(stop=lambda: None)
    app.tray = types.SimpleNamespace(stop=lambda: None)
    app.root = types.SimpleNamespace(destroy=lambda: None)
    return ns, app


def pump(app, timeout=3):
    """Run queued UI callbacks until the app returns to idle (or timeout)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            app.ui.get(timeout=0.05)()
        except queue.Empty:
            pass
        if app.state == "idle":
            return


def test_full_cycle_pastes_and_saves_text(tmp_path):
    pasted = []
    ns, app = app_ns(tmp_path, keyboard=types.SimpleNamespace(send=pasted.append,
                                                               unhook_all=lambda: None))
    app.toggle(); assert app.state == "recording"
    app.toggle(); assert app.state == "transcribing"
    app.toggle(); assert app.state == "transcribing"  # ignored while busy
    pump(app)
    assert app.state == "idle" and pasted == ["ctrl+v"]
    assert os.listdir(ns["TEXT_DIR"])


def test_stop_error_does_not_wedge_state(tmp_path):
    """Mic unplugged mid-recording: recorder.stop() raises inside _stop -> state stays
    'recording' forever; the next press raises AttributeError; Quit raises too."""
    ns, app = app_ns(tmp_path)
    app.toggle()
    FakeStream.fail_on_stop = RuntimeError("PortAudio: Unanticipated host error")
    try:
        app.toggle()
    except Exception:
        pass  # _pump would log "ui task failed"
    FakeStream.fail_on_stop = None
    assert app.state != "recording", "stuck in 'recording' after stop error"


def test_quit_after_stop_error(tmp_path):
    ns, app = app_ns(tmp_path)
    app.toggle()
    FakeStream.fail_on_stop = RuntimeError("PortAudio: Unanticipated host error")
    try:
        app.toggle()
    except Exception:
        pass
    FakeStream.fail_on_stop = None
    app.quit()  # must not raise, or the app can't be quit from the panel


def test_clipboard_busy_does_not_wedge_state(tmp_path):
    """pyperclip.copy raises when another process holds the clipboard
    (clipboard managers, RDP, Office). It's outside the try in _deliver."""
    def busy(_):
        raise RuntimeError("Error calling OpenClipboard")
    ns, app = app_ns(tmp_path, pyperclip=types.SimpleNamespace(copy=busy))
    app.toggle(); app.toggle()
    end = time.time() + 3
    while time.time() < end and app.state != "idle":
        try:
            app.ui.get(timeout=0.05)()
        except queue.Empty:
            pass
        except Exception:
            break  # what _pump does: log and carry on
    assert app.state == "idle", "stuck in 'transcribing' after clipboard error"


def test_text_save_failure_still_delivers_text(tmp_path):
    """If the text folder vanished (user tidied Documents, OneDrive hiccup) the
    finished transcript is thrown away instead of pasted."""
    pasted = []
    ns, app = app_ns(tmp_path, keyboard=types.SimpleNamespace(send=pasted.append,
                                                               unhook_all=lambda: None))
    app.toggle(); app.toggle()
    os.rmdir(ns["TEXT_DIR"])
    pump(app)
    assert pasted == ["ctrl+v"], "transcript lost because the .txt couldn't be written"


def test_recording_after_output_folder_deleted(tmp_path):
    """User deletes Documents\\uWhisper while the app runs -> every recording fails
    with a misleading 'Mic error' until restart."""
    import shutil
    ns, app = app_ns(tmp_path)
    shutil.rmtree(os.path.dirname(ns["AUDIO_DIR"]))
    app.toggle()
    assert app.state == "recording", app.overlay.calls


def test_cleanup_never_touches_busy_recording(tmp_path):
    ns, app = app_ns(tmp_path)
    old = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime(storage.STAMP_FMT)
    p = os.path.join(ns["AUDIO_DIR"], old + ".wav")
    open(p, "wb").close()
    app.recorder.path = p
    app.state = "transcribing"
    app.run_cleanup()
    time.sleep(0.3)
    assert os.path.exists(p)


def test_cleanup_huge_years_value_does_not_crash(tmp_path):
    """amount is capped at 9999 but 9999 years -> date before year 1 -> OverflowError
    escapes delete_old_audio (which promises 'Never raises')."""
    days = storage.keep_days(9999, "years")
    storage.delete_old_audio(str(tmp_path), days, datetime.datetime.now())
