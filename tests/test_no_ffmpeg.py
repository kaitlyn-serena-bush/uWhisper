"""The exe ships without PyAV/FFmpeg. Prove the engine still works when `av` can't be imported."""
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "tts")

BLOCK_AV = """
import sys
class _BlockAV:
    def find_spec(self, name, path=None, target=None):
        if name == "av" or name.startswith("av."):
            raise ImportError("av blocked for this test")
sys.meta_path.insert(0, _BlockAV())
sys.path.insert(0, {root!r})
"""


def run(code):
    script = BLOCK_AV.format(root=ROOT) + textwrap.dedent(code)
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                       timeout=300, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-3000:]
    return r.stdout


def test_engine_imports_without_av():
    out = run("""
        import uwhisper_engine, av
        print(getattr(av, "__uwhisper_stub__", False))
    """)
    assert out.strip() == "True"


def test_stub_fails_loudly_if_file_decoding_is_attempted():
    out = run("""
        import uwhisper_engine
        from faster_whisper import decode_audio
        try:
            decode_audio("x.mp3")
        except RuntimeError as e:
            print("raised:", e)
    """)
    assert "built without FFmpeg" in out


@pytest.mark.slow
@pytest.mark.parametrize("vad", [False, True])
def test_transcribes_without_av(vad):
    clip = os.path.join(FIXTURES, "fox.wav")
    if not os.path.exists(clip):
        pytest.skip("TTS fixtures not generated yet (run test_engine.py first)")
    out = run(f"""
        import uwhisper_engine as E
        s = dict(E.DEFAULTS, model="base.en", device="cpu", vad={vad})
        e = E.Engine(s, log=print)
        e.load()
        print("TEXT:", e.transcribe({clip!r}))
    """)
    text = out.split("TEXT:", 1)[1].lower()
    assert "quick brown fox" in text and "lazy dog" in text
