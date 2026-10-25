"""Speech-to-text engine for uWhisper (faster-whisper, GPU when available).

Speed tricks:
- The model is loaded once at startup and kept in memory, so every
  transcription starts instantly.
- BatchedInferencePipeline splits the recording into chunks at natural
  pauses (voice detection) and transcribes many chunks at once in parallel.
"""
import os

import uwhisper_gpu as gpu  # noqa: E402

gpu.ready()  # put cuBLAS on the DLL path if it's already here (bundled or downloaded)


def _stub_av():
    """faster-whisper imports PyAV (FFmpeg, ~67 MB) at startup, but only uses it to decode
    audio *files*. uWhisper always passes raw samples (see load_audio_16k), so the exe is
    built without PyAV and this stand-in satisfies the import. If anything ever does try to
    decode a file with it, it fails loudly instead of silently."""
    try:
        import av  # noqa: F401  (present when running from source)
        return
    except ImportError:
        pass
    import sys
    import types

    def unavailable(*_a, **_k):
        raise RuntimeError("uWhisper is built without FFmpeg; pass audio samples, not a file")

    av = types.ModuleType("av")
    av.open = unavailable
    av.audio = types.SimpleNamespace(
        resampler=types.SimpleNamespace(AudioResampler=unavailable),
        fifo=types.SimpleNamespace(AudioFifo=unavailable))
    av.error = types.SimpleNamespace(InvalidDataError=type("InvalidDataError", (Exception,), {}))
    av.__uwhisper_stub__ = True
    sys.modules["av"] = av


_stub_av()

import ctranslate2  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402
from faster_whisper import BatchedInferencePipeline, WhisperModel  # noqa: E402

def load_audio_16k(path):
    """Read an audio file as mono float32 at 16 kHz (what Whisper expects)."""
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    # Interfaces like the Fireface often put a mic on one side of a stereo
    # pair, so use the loudest channel instead of averaging in silence.
    loudness = np.sqrt((data ** 2).mean(axis=0)) if len(data) else [0]
    mono = data[:, int(np.argmax(loudness))]
    if rate != 16000:
        mono = soxr.resample(mono, rate, 16000)
    return np.ascontiguousarray(mono, dtype=np.float32)


def quiet_chunks(audio, target=25.0, search=5.0, sr=16000):
    """Split points about every `target` seconds, each nudged to the quietest
    100 ms within +/- `search` seconds, so chunk edges land between words."""
    total = len(audio) / sr
    cuts, pos, win = [0.0], 0.0, int(sr * 0.1)
    while pos + target + search < total:
        lo, hi = int((pos + target - search) * sr), int((pos + target + search) * sr)
        frames = audio[lo:hi][: (hi - lo) // win * win].reshape(-1, win)
        quietest = int(np.argmin(np.sqrt((frames ** 2).mean(axis=1))))
        pos = (lo + quietest * win + win // 2) / sr
        cuts.append(pos)
    cuts.append(total)
    return [{"start": a, "end": b} for a, b in zip(cuts, cuts[1:])]


DEFAULTS = {
    "device": "auto",            # auto | cuda | cpu
    "model": "large-v3-turbo",
    "compute_type": "auto",      # auto | float16 | int8_float16 | int8 | float32
    "batch_size": 24,            # chunks transcribed in parallel
    "beam_size": 5,              # higher = a bit more accurate, slower
    "language": "en",            # None = auto-detect
    "vad": False,                # skip silent parts (faster, but can drop quiet words)
    "cpu_threads": 0,            # 0 = all cores
}

# Settings that need the model reloaded when they change.
RELOAD_KEYS = ("device", "model", "compute_type", "cpu_threads")


_cuda_broken = False  # set after a CUDA failure; retrying in the same process can hang


class Engine:
    gpu_missing = False

    def __init__(self, settings, log=print):
        self.settings = settings  # shared dict; transcribe() reads it live
        self.log = log
        self.model = None
        self.pipeline = None
        self.device = None
        self.compute_type = None
        self.model_name = None

    def load(self):
        global _cuda_broken
        s = self.settings
        want = s["device"]
        self.gpu_missing = False  # True: NVIDIA card present but GPU support not downloaded yet
        has_gpu = want in ("auto", "cuda") and ctranslate2.get_cuda_device_count() > 0
        if has_gpu and not gpu.ready():
            # Never try CUDA without cuBLAS: a failed try breaks CUDA until restart.
            self.gpu_missing = True
            self.log("NVIDIA GPU found but GPU support isn't downloaded yet, using CPU")
        elif has_gpu and _cuda_broken:
            self.log("GPU failed earlier in this session, using CPU until restart")
        elif has_gpu:
            try:
                self._load("cuda")
                return
            except ValueError:  # bad model name etc. - not the GPU's fault
                raise
            except Exception as e:  # unsupported GPU, old driver, out of memory...
                _cuda_broken = True
                self.log(f"GPU load failed, falling back to CPU: {e}")
        elif want == "cuda":
            self.log("no CUDA GPU found, using CPU")
        self._load("cpu")

    def _load(self, device):
        s = self.settings
        ct = s["compute_type"]
        if ct == "auto":
            ct = "float16" if device == "cuda" else "int8"
        elif device == "cpu" and "float16" in ct:
            ct = "int8"  # CPUs can't do float16
        threads = s["cpu_threads"] or (os.cpu_count() or 8)
        model = WhisperModel(s["model"], device=device, compute_type=ct,
                             cpu_threads=threads if device == "cpu" else 0)
        # Warm up so the first real transcription isn't slowed by GPU init.
        list(model.transcribe(np.zeros(16000, dtype="float32"), beam_size=1)[0])
        self.model = model
        self.pipeline = BatchedInferencePipeline(model=model)
        self.device, self.compute_type, self.model_name = device, ct, s["model"]

    def transcribe(self, path):
        s = self.settings
        if self.model is None:
            raise RuntimeError("speech model is not loaded")
        audio = load_audio_16k(path)
        try:
            if s["vad"]:
                # Skip silence: voice detection removes pauses, then chunks run in batches.
                # Fastest, but can drop very quiet speech.
                segments, _info = self.pipeline.transcribe(
                    audio, batch_size=int(s["batch_size"]), beam_size=int(s["beam_size"]),
                    language=s["language"], vad_filter=True, condition_on_previous_text=False)
            else:
                # Keep everything: cut into ~25 s pieces at the quietest nearby moment
                # (so words aren't split), then transcribe the pieces in parallel.
                segments, _info = self.pipeline.transcribe(
                    audio, batch_size=int(s["batch_size"]), beam_size=int(s["beam_size"]),
                    language=s["language"], vad_filter=False, clip_timestamps=quiet_chunks(audio),
                    condition_on_previous_text=False)
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:  # never lose a recording: fall back to one-pass mode
            self.log(f"parallel transcription failed, retrying one-pass: {e}")
        # One-pass mode: slower, but handles anything.
        segments, _info = self.model.transcribe(
            audio,
            beam_size=int(s["beam_size"]),
            language=s["language"],
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
