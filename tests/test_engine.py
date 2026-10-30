"""Tests for uwhisper_engine: audio loading, chunking, and real transcription.

Speech fixtures are generated at session start with Windows' built-in TTS
(System.Speech) into tests/fixtures/tts/ (git-ignorable, regenerated if missing).

Run fast tests only:   pytest tests/test_engine.py -m "not slow"
Run everything:        pytest tests/test_engine.py -s   (-s shows timings)
"""
import os
import re
import subprocess
import sys
import time

import numpy as np
import pytest
import soundfile as sf

import uwhisper_engine as eng
from uwhisper_engine import DEFAULTS, RELOAD_KEYS, Engine, load_audio_16k, quiet_chunks

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tts")

SENTENCES = {
    "fox": "The quick brown fox jumps over the lazy dog.",
    "report": "Please send the quarterly report to the finance team by Friday afternoon.",
    "weather": "The weather today is cloudy with a gentle breeze coming from the north.",
}
LONG_TEXT = (
    "Every morning the old lighthouse keeper climbed the narrow stairs to check the lamp. "
    "He wiped the glass, trimmed the wick, and wrote a short note in his leather journal. "
    "The ships that passed in the night never knew his name, but they trusted his light. "
    "In the summer, children from the village would visit and ask him for stories about storms. "
    "He told them about the winter when the waves rose higher than the cliffs. "
    "He told them about the fishing boat that found its way home through the thick fog. "
    "When the afternoon grew warm, he walked along the beach and collected smooth stones. "
    "Some of the stones were gray, some were green, and a few were almost perfectly white. "
    "He kept the best ones in a glass jar beside the window in his small kitchen. "
    "At sunset he lit the lamp again and watched the beam sweep slowly across the water. "
    "The keeper believed that a simple job done carefully could matter a great deal. "
    "Years later, when the light was finally automated, the village remembered him fondly. "
    "They placed a wooden bench near the tower so visitors could sit and watch the sea."
)

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="TTS fixtures need Windows")


# --------------------------------------------------------------------------- helpers

def _tts(text, path, rate=0):
    """Speak `text` into a WAV file with the built-in Windows voice."""
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Rate = {rate};"
        "$s.SetOutputToWaveFile($env:UW_TTS_PATH);"
        "$s.Speak($env:UW_TTS_TEXT);"
        "$s.Dispose()"
    )
    env = dict(os.environ, UW_TTS_PATH=path, UW_TTS_TEXT=text)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, env=env, timeout=300, capture_output=True)


def _words(text):
    return re.findall(r"[a-z']+", text.lower())


def word_recall(expected, got):
    """Fraction of expected words that appear in the transcription (bag of words)."""
    exp, have = _words(expected), set(_words(got))
    return sum(w in have for w in exp) / len(exp)


def _tone(seconds, sr, freq=440.0, amp=0.5):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def tts_clips():
    """{name: (wav_path, expected_text)}; generated once, reused across runs."""
    if sys.platform != "win32":
        pytest.skip("TTS fixtures need Windows")
    os.makedirs(FIXTURES, exist_ok=True)
    clips = dict(SENTENCES, long=LONG_TEXT)
    out = {}
    for name, text in clips.items():
        path = os.path.join(FIXTURES, f"{name}.wav")
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            _tts(text, path)
        out[name] = (path, text)
    dur = sf.info(out["long"][0]).duration
    assert 50 < dur < 120, f"long clip should exercise chunking, got {dur:.1f}s"
    return out


@pytest.fixture(scope="session")
def cpu_engine():
    settings = dict(DEFAULTS, model="base.en", device="cpu", language="en")
    e = Engine(settings, log=lambda m: None)
    t0 = time.perf_counter()
    e.load()
    print(f"\n[timing] base.en CPU load: {time.perf_counter() - t0:.2f}s")
    return e


@pytest.fixture
def engine(cpu_engine):
    """The shared CPU engine with its settings restored after each test."""
    saved = dict(cpu_engine.settings)
    logs = []
    cpu_engine.log = logs.append
    cpu_engine.logs = logs
    yield cpu_engine
    cpu_engine.settings.clear()
    cpu_engine.settings.update(saved)
    cpu_engine.log = lambda m: None


# --------------------------------------------------------------------------- DEFAULTS

def test_defaults_shape():
    for k in ("device", "model", "compute_type", "batch_size", "beam_size",
              "language", "vad", "cpu_threads", "style_prompt"):
        assert k in DEFAULTS
    assert set(RELOAD_KEYS) <= set(DEFAULTS)
    assert DEFAULTS["device"] in ("auto", "cuda", "cpu")
    assert int(DEFAULTS["batch_size"]) > 0 and int(DEFAULTS["beam_size"]) > 0
    # The sample must show both things Whisper is meant to copy: punctuation and fillers.
    prompt = DEFAULTS["style_prompt"]
    assert any(c in prompt for c in ".,!?") and prompt[0].isupper()
    assert any(f in prompt.lower() for f in ("um", "uh", "hmm"))


# --------------------------------------------------------------------------- style prompt

class _FakeWhisper:
    """Stands in for WhisperModel / BatchedInferencePipeline and records the kwargs."""
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("simulated batched failure")
        return iter([]), None


def _fake_engine(tmp_path, fail_batched=False, **settings):
    e = Engine(dict(DEFAULTS, **settings), log=lambda m: None)
    e.model, e.pipeline = _FakeWhisper(), _FakeWhisper(fail=fail_batched)
    p = tmp_path / "x.wav"
    sf.write(p, _tone(1.0, 16000), 16000, subtype="FLOAT")
    return e, str(p)


@pytest.mark.parametrize("vad", [False, True])
def test_style_prompt_reaches_batched_and_one_pass(tmp_path, vad):
    e, path = _fake_engine(tmp_path, fail_batched=True, vad=vad)
    e.transcribe(path)
    assert e.pipeline.calls[0]["initial_prompt"] == DEFAULTS["style_prompt"]
    assert e.model.calls[0]["initial_prompt"] == DEFAULTS["style_prompt"]
    for kw in e.pipeline.calls + e.model.calls:
        assert kw["condition_on_previous_text"] is False  # no repetition loops


@pytest.mark.parametrize("empty", ["", None])
def test_style_prompt_can_be_switched_off(tmp_path, empty):
    e, path = _fake_engine(tmp_path, style_prompt=empty)
    e.transcribe(path)
    assert e.pipeline.calls[0]["initial_prompt"] is None


def test_style_prompt_missing_from_old_config_is_off(tmp_path):
    """A config.json saved by 1.1 has no style_prompt key; the engine must not crash."""
    e, path = _fake_engine(tmp_path)
    del e.settings["style_prompt"]
    e.transcribe(path)
    assert e.pipeline.calls[0]["initial_prompt"] is None


# --------------------------------------------------------------------------- load_audio_16k

def test_mono_16k_passthrough(tmp_path):
    x = _tone(1.0, 16000)
    p = tmp_path / "m.wav"
    sf.write(p, x, 16000, subtype="FLOAT")
    y = load_audio_16k(str(p))
    assert y.dtype == np.float32 and y.ndim == 1 and y.flags["C_CONTIGUOUS"]
    np.testing.assert_allclose(y, x, atol=1e-6)


@pytest.mark.parametrize("rate", [8000, 22050, 44100, 48000, 96000])
def test_resample_to_16k(tmp_path, rate):
    secs = 2.0
    p = tmp_path / f"r{rate}.wav"
    sf.write(p, _tone(secs, rate, freq=300), rate)
    y = load_audio_16k(str(p))
    assert y.dtype == np.float32
    assert abs(len(y) - secs * 16000) <= 2
    # Frequency is preserved: dominant FFT bin is ~300 Hz.
    spec = np.abs(np.fft.rfft(y))
    peak_hz = np.argmax(spec) * 16000 / len(y)
    assert abs(peak_hz - 300) < 5
    assert 0.3 < np.abs(y).max() < 0.7


@pytest.mark.parametrize("loud", [0, 1])
def test_stereo_picks_loud_channel(tmp_path, loud):
    sig = _tone(1.0, 48000, freq=500, amp=0.6)
    noise = (np.random.default_rng(0).standard_normal(len(sig)) * 1e-3).astype(np.float32)
    data = np.zeros((len(sig), 2), np.float32)
    data[:, loud], data[:, 1 - loud] = sig, noise
    p = tmp_path / "st.wav"
    sf.write(p, data, 48000)
    y = load_audio_16k(str(p))
    assert y.ndim == 1
    assert np.sqrt((y ** 2).mean()) > 0.3  # the tone, not the near-silent side


@needs_windows
def test_stereo_speech_on_right_channel_transcribes_same_audio(tmp_path, tts_clips):
    speech, rate = sf.read(tts_clips["fox"][0], dtype="float32")
    data = np.zeros((len(speech), 2), np.float32)
    data[:, 1] = speech
    p = tmp_path / "right.wav"
    sf.write(p, data, rate)
    np.testing.assert_allclose(load_audio_16k(str(p)), load_audio_16k(tts_clips["fox"][0]),
                               atol=1e-4)


def test_multichannel_picks_loudest(tmp_path):
    data = np.zeros((16000, 4), np.float32)
    data[:, 2] = _tone(1.0, 16000)
    data[:, 0] = _tone(1.0, 16000, amp=0.05)
    p = tmp_path / "4ch.wav"
    sf.write(p, data, 16000, subtype="FLOAT")
    np.testing.assert_allclose(load_audio_16k(str(p)), data[:, 2], atol=1e-6)


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"])
def test_sample_formats_scaled_to_unit_range(tmp_path, subtype):
    x = _tone(0.5, 16000, amp=0.5)
    p = tmp_path / f"{subtype}.wav"
    sf.write(p, x, 16000, subtype=subtype)
    y = load_audio_16k(str(p))
    assert y.dtype == np.float32
    np.testing.assert_allclose(y, x, atol=1e-3)  # PCM16 quantisation ~3e-5


def test_pcm16_full_scale_is_within_minus1_1(tmp_path):
    p = tmp_path / "fs.wav"
    sf.write(p, np.array([32767, -32768, 0], np.int16), 16000, subtype="PCM_16")
    y = load_audio_16k(str(p))
    assert y.max() <= 1.0 and y.min() >= -1.0


@pytest.mark.parametrize("rate", [16000, 44100])
def test_very_short_file(tmp_path, rate):
    p = tmp_path / "short.wav"
    sf.write(p, _tone(0.05, rate), rate)  # 50 ms
    y = load_audio_16k(str(p))
    assert abs(len(y) - 800) <= 2 and y.dtype == np.float32


@pytest.mark.parametrize("rate,channels", [(16000, 1), (48000, 2)])
def test_empty_wav(tmp_path, rate, channels):
    p = tmp_path / "empty.wav"
    sf.write(p, np.zeros((0, channels), np.float32), rate)
    y = load_audio_16k(str(p))
    assert y.shape == (0,) and y.dtype == np.float32


def test_missing_file_raises(tmp_path):
    with pytest.raises(Exception):
        load_audio_16k(str(tmp_path / "nope.wav"))


# --------------------------------------------------------------------------- quiet_chunks

SR = 16000


def _check_chunks(chunks, total, max_len=30.0):
    assert chunks[0]["start"] == 0.0
    assert chunks[-1]["end"] == pytest.approx(total)
    for a, b in zip(chunks, chunks[1:]):
        assert a["end"] == b["start"]  # contiguous, no gaps/overlap
    for c in chunks:
        assert c["end"] > c["start"] or total == 0  # ordered, non-empty
        assert c["end"] - c["start"] <= max_len + 1e-9  # Whisper window is 30 s


def _speechy(seconds, gaps=(), seed=0):
    """Noise 'speech' with silent gaps at the given times (s)."""
    x = (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.3).astype(np.float32)
    for g in gaps:
        x[int(g * SR): int((g + 0.3) * SR)] = 0
    return x


def test_chunks_short_audio_one_piece():
    a = _speechy(12)
    ch = quiet_chunks(a)
    assert ch == [{"start": 0.0, "end": 12.0}]


def test_chunks_exactly_30s_one_piece():
    ch = quiet_chunks(_speechy(30))
    assert len(ch) == 1
    _check_chunks(ch, 30.0)


def test_chunks_just_over_30s_splits():
    ch = quiet_chunks(_speechy(31, gaps=[24.0]))
    assert len(ch) == 2
    _check_chunks(ch, 31.0)
    assert 24.0 <= ch[0]["end"] <= 24.3  # cut lands in the gap


@pytest.mark.parametrize("seconds", [45, 61.3, 90, 300, 1800])
def test_chunks_long_audio(seconds):
    ch = quiet_chunks(_speechy(seconds, seed=int(seconds)))
    _check_chunks(ch, float(seconds))
    assert len(ch) >= int(seconds // 30)
    for c in ch[:-1]:
        assert 20.0 <= c["end"] - c["start"] <= 30.0


def test_chunks_cut_at_quiet_gaps():
    gaps = [22.0, 47.5, 73.0]
    ch = quiet_chunks(_speechy(95, gaps=gaps))
    _check_chunks(ch, 95.0)
    for cut, g in zip([c["end"] for c in ch[:-1]], gaps):
        assert g <= cut <= g + 0.3


def test_chunks_all_silence():
    ch = quiet_chunks(np.zeros(SR * 100, np.float32))
    _check_chunks(ch, 100.0)


def test_chunks_empty_audio():
    ch = quiet_chunks(np.zeros(0, np.float32))
    assert ch[0]["start"] == 0.0 and ch[-1]["end"] == 0.0


def test_chunks_odd_length_not_multiple_of_window():
    a = _speechy(57.0123)
    ch = quiet_chunks(a)
    _check_chunks(ch, len(a) / SR)


# --------------------------------------------------------------------------- end-to-end CPU

@pytest.mark.slow
@needs_windows
@pytest.mark.parametrize("vad", [False, True])
@pytest.mark.parametrize("name", list(SENTENCES))
def test_cpu_transcribe_sentences(engine, tts_clips, name, vad):
    engine.settings["vad"] = vad
    path, expected = tts_clips[name]
    t0 = time.perf_counter()
    text = engine.transcribe(path)
    dt = time.perf_counter() - t0
    r = word_recall(expected, text)
    print(f"\n[cpu base.en vad={vad}] {name}: recall={r:.0%} {dt:.2f}s -> {text!r}")
    assert r >= 0.8, text
    assert not engine.logs, engine.logs  # batched path worked, no fallback


@pytest.mark.slow
@needs_windows
@pytest.mark.parametrize("vad", [False, True])
def test_cpu_transcribe_long_chunked(engine, tts_clips, vad):
    engine.settings["vad"] = vad
    path, expected = tts_clips["long"]
    audio = load_audio_16k(path)
    assert len(quiet_chunks(audio)) >= 2  # really exercises chunking
    t0 = time.perf_counter()
    text = engine.transcribe(path)
    dt = time.perf_counter() - t0
    r = word_recall(expected, text)
    print(f"\n[cpu base.en vad={vad}] long {len(audio)/SR:.1f}s: recall={r:.0%} {dt:.2f}s")
    assert r >= 0.8, text
    assert not engine.logs, engine.logs
    # Nothing at chunk boundaries got duplicated wholesale.
    assert len(_words(text)) <= 1.2 * len(_words(expected))
    # The style prompt keeps Whisper punctuating and capitalising all the way through.
    assert text.count(".") >= 8 and text[0].isupper(), text


@pytest.mark.slow
@needs_windows
@pytest.mark.parametrize("vad", [False, True])
def test_cpu_language_autodetect(tts_clips, vad):
    # base.en is English-only, but language=None still exercises the detect path.
    e = Engine(dict(DEFAULTS, model="base.en", device="cpu", language=None, vad=vad),
               log=lambda m: pytest.fail(f"fallback used: {m}"))
    e.load()
    path, expected = tts_clips["report"]
    text = e.transcribe(path)
    assert word_recall(expected, text) >= 0.8, text


@pytest.mark.slow
@needs_windows
def test_cpu_stereo_right_channel_end_to_end(engine, tmp_path, tts_clips):
    speech, rate = sf.read(tts_clips["weather"][0], dtype="float32")
    data = np.zeros((len(speech), 2), np.float32)
    data[:, 1] = speech
    p = tmp_path / "right48k.wav"
    sf.write(p, np.stack([soxr_resample(data[:, 0], rate, 48000),
                          soxr_resample(data[:, 1], rate, 48000)], axis=1), 48000)
    text = engine.transcribe(str(p))
    assert word_recall(tts_clips["weather"][1], text) >= 0.8, text


def soxr_resample(x, a, b):
    import soxr
    return soxr.resample(x, a, b).astype(np.float32)


@pytest.mark.slow
@pytest.mark.parametrize("vad", [False, True])
def test_cpu_silence_and_short_audio_do_not_crash(engine, tmp_path, vad):
    engine.settings["vad"] = vad
    for secs in (0.2, 3.0):
        p = tmp_path / f"sil{secs}.wav"
        sf.write(p, np.zeros(int(secs * 16000), np.float32), 16000)
        assert isinstance(engine.transcribe(str(p)), str)


@pytest.mark.slow
@pytest.mark.parametrize("vad", [False, True])
def test_cpu_empty_recording_returns_empty_string(engine, tmp_path, vad):
    """A hotkey tap that records zero samples should give '' rather than raise."""
    engine.settings["vad"] = vad
    p = tmp_path / "empty.wav"
    sf.write(p, np.zeros((0, 1), np.float32), 16000)
    assert engine.transcribe(str(p)) == ""


# --------------------------------------------------------------------------- fallback paths

@pytest.mark.slow
@needs_windows
@pytest.mark.parametrize("vad", [False, True])
def test_batched_failure_falls_back_to_one_pass(engine, tts_clips, monkeypatch, vad):
    engine.settings["vad"] = vad

    def boom(*a, **k):
        raise RuntimeError("simulated batched failure")

    monkeypatch.setattr(engine.pipeline, "transcribe", boom)
    path, expected = tts_clips["fox"]
    text = engine.transcribe(path)
    assert word_recall(expected, text) >= 0.8, text
    assert any("one-pass" in m and "simulated" in m for m in engine.logs)


@pytest.mark.slow
@needs_windows
def test_batched_failure_during_iteration_falls_back(engine, tts_clips, monkeypatch):
    """faster-whisper returns a lazy generator; errors raised mid-iteration
    must also trigger the one-pass fallback."""
    class Seg:
        text = " partial"

    def lazy(*a, **k):
        def gen():
            yield Seg()
            raise RuntimeError("CUDA out of memory (simulated)")
        return gen(), None

    monkeypatch.setattr(engine.pipeline, "transcribe", lazy)
    path, expected = tts_clips["report"]
    text = engine.transcribe(path)
    assert not text.startswith("partial")  # half-finished batched output discarded
    assert word_recall(expected, text) >= 0.8, text
    assert engine.logs


def test_bad_model_name_raises_clear_error():
    logs = []
    e = Engine(dict(DEFAULTS, model="definitely-not-a-whisper-model", device="cpu"),
               log=logs.append)
    with pytest.raises(Exception) as ei:
        e.load()
    assert "definitely-not-a-whisper-model" in str(ei.value) or "Invalid model" in str(ei.value)
    assert e.model is None and e.pipeline is None


def test_bad_model_name_auto_device_raises(monkeypatch):
    """With a GPU present, auto tries CUDA, logs, falls back to CPU, then raises."""
    logs = []
    e = Engine(dict(DEFAULTS, model="definitely-not-a-whisper-model", device="auto"),
               log=logs.append)
    with pytest.raises(Exception):
        e.load()
    assert e.model is None


def test_cuda_requested_without_gpu_falls_back(monkeypatch):
    monkeypatch.setattr(eng.ctranslate2, "get_cuda_device_count", lambda: 0)
    calls = []
    monkeypatch.setattr(Engine, "_load", lambda self, d: calls.append(d))
    logs = []
    Engine(dict(DEFAULTS, device="cuda"), log=logs.append).load()
    assert calls == ["cpu"]
    assert any("no CUDA" in m for m in logs)


def test_cuda_load_error_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(eng.ctranslate2, "get_cuda_device_count", lambda: 1)
    calls = []

    def fake(self, d):
        calls.append(d)
        if d == "cuda":
            raise RuntimeError("cublas64_12.dll not found")

    monkeypatch.setattr(Engine, "_load", fake)
    logs = []
    Engine(dict(DEFAULTS, device="cuda"), log=logs.append).load()
    assert calls == ["cuda", "cpu"]
    assert any("falling back to CPU" in m and "cublas" in m for m in logs)


def test_cpu_device_never_tries_cuda(monkeypatch):
    monkeypatch.setattr(eng.ctranslate2, "get_cuda_device_count", lambda: 1)
    calls = []
    monkeypatch.setattr(Engine, "_load", lambda self, d: calls.append(d))
    Engine(dict(DEFAULTS, device="cpu"), log=lambda m: None).load()
    assert calls == ["cpu"]


@pytest.mark.parametrize("device,ct,want", [
    ("cpu", "auto", "int8"), ("cuda", "auto", "float16"),
    ("cpu", "float16", "int8"), ("cpu", "int8_float16", "int8"),
    ("cpu", "float32", "float32"), ("cuda", "int8_float16", "int8_float16"),
])
def test_compute_type_mapping(monkeypatch, device, ct, want):
    seen = {}

    class FakeModel:
        def __init__(self, name, device, compute_type, cpu_threads):
            seen.update(device=device, ct=compute_type, threads=cpu_threads)

        def transcribe(self, *a, **k):
            return iter(()), None

    monkeypatch.setattr(eng, "WhisperModel", FakeModel)
    monkeypatch.setattr(eng, "BatchedInferencePipeline", lambda model: object())
    e = Engine(dict(DEFAULTS, compute_type=ct, cpu_threads=0), log=lambda m: None)
    e._load(device)
    assert seen["ct"] == want and e.compute_type == want and e.device == device
    assert (seen["threads"] > 0) == (device == "cpu")


def test_real_cuda_request_works_or_falls_back():
    """device='cuda' with the real environment: ends up on cuda or cpu, never raises."""
    logs = []
    e = Engine(dict(DEFAULTS, model="base.en", device="cuda"), log=logs.append)
    e.load()
    assert e.device in ("cuda", "cpu")
    if eng.ctranslate2.get_cuda_device_count() == 0:
        assert e.device == "cpu"


# --------------------------------------------------------------------------- GPU

@pytest.mark.slow
@pytest.mark.gpu
@needs_windows
@pytest.mark.skipif(eng.ctranslate2.get_cuda_device_count() == 0, reason="no CUDA GPU")
def test_gpu_large_v3_turbo(tts_clips):
    logs = []
    e = Engine(dict(DEFAULTS, model="large-v3-turbo", device="cuda"), log=logs.append)
    t0 = time.perf_counter()
    e.load()
    load_s = time.perf_counter() - t0
    assert e.device == "cuda", logs
    results = []
    for vad in (False, True):
        e.settings["vad"] = vad
        for name in ("fox", "report", "long"):
            path, expected = tts_clips[name]
            t0 = time.perf_counter()
            text = e.transcribe(path)
            dt = time.perf_counter() - t0
            r = word_recall(expected, text)
            results.append((vad, name, sf.info(path).duration, r, dt))
            assert r >= 0.85, (name, vad, text)
    print(f"\n[timing] large-v3-turbo CUDA {e.compute_type} load: {load_s:.2f}s")
    for vad, name, dur, r, dt in results:
        print(f"[gpu turbo vad={vad}] {name} ({dur:.1f}s audio): recall={r:.0%} {dt:.2f}s "
              f"({dur/dt:.0f}x realtime)")
    assert not logs, logs
