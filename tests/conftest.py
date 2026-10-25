"""Shared pytest setup for uWhisper tests."""
import os
import sys
import pytest

# Make the app modules (uwhisper_*.py in the project root) importable.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: loads a Whisper model / runs real transcription")
    config.addinivalue_line("markers", "gpu: needs a CUDA GPU")


@pytest.fixture(autouse=True)
def _reset_cuda_broken():
    """A test that fakes a GPU failure must not disable the GPU for later tests."""
    yield
    eng = sys.modules.get("uwhisper_engine")
    if eng is not None:
        eng._cuda_broken = False
