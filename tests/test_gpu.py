"""Tests for the on-demand GPU support download (uwhisper_gpu.py), using a fake package."""
import hashlib
import io
import json
import os
import zipfile

import pytest

import uwhisper_gpu as gpu


def make_wheel(files=("cublas64_12.dll", "cublasLt64_12.dll")):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in files:
            z.writestr(f"nvidia/cublas/bin/{name}", b"MZ fake " + name.encode())
        z.writestr("nvidia/cublas/include/cublas.h", b"// header")
        z.writestr("nvidia/cublas/bin/nvblas64_12.dll", b"not needed")
    return buf.getvalue()


class FakeResp(io.BytesIO):
    def __init__(self, data, length=True):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))} if length else {}


def pkg_for(data, **over):
    p = dict(gpu.PACKAGE, url="https://example.invalid/cublas.whl",
             sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    p.update(over)
    return p


def run(tmp_path, data, **over):
    d = gpu.Download(gpu_dir=str(tmp_path), package=pkg_for(data, **over),
                     opener=lambda url, timeout: FakeResp(data))
    d._run()
    return d


def test_download_unpacks_only_needed_dlls(tmp_path):
    d = run(tmp_path, make_wheel())
    assert d.error is None and d.progress == 1.0 and d.done.is_set()
    assert sorted(os.listdir(tmp_path / "bin")) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert gpu.downloaded_dir(str(tmp_path)) == str(tmp_path / "bin")
    assert not (tmp_path / "download.part").exists()


def test_checksum_mismatch_rejected(tmp_path):
    d = run(tmp_path, make_wheel(), sha256="0" * 64)
    assert "checksum" in str(d.error)
    assert gpu.downloaded_dir(str(tmp_path)) is None
    assert not (tmp_path / "bin").exists()
    assert not (tmp_path / "download.part").exists()


def test_truncated_download_rejected(tmp_path):
    data = make_wheel()
    d = gpu.Download(gpu_dir=str(tmp_path), package=pkg_for(data),
                     opener=lambda url, timeout: FakeResp(data[:-100]))
    d._run()
    assert "incomplete" in str(d.error)
    assert gpu.downloaded_dir(str(tmp_path)) is None


def test_missing_dll_in_package(tmp_path):
    d = run(tmp_path, make_wheel(files=("cublas64_12.dll",)))
    assert "cublasLt64_12.dll missing" in str(d.error)
    assert gpu.downloaded_dir(str(tmp_path)) is None


def test_network_error_reported_and_on_done_called(tmp_path):
    calls = []

    def boom(url, timeout):
        raise OSError("no internet")
    d = gpu.Download(on_done=calls.append, gpu_dir=str(tmp_path),
                     package=pkg_for(b"x"), opener=boom)
    d._run()
    assert isinstance(d.error, OSError) and calls == [d]


def test_cancel(tmp_path):
    data = make_wheel()
    d = gpu.Download(gpu_dir=str(tmp_path), package=pkg_for(data),
                     opener=lambda url, timeout: FakeResp(data))
    d.cancel()
    d._run()
    assert "cancelled" in str(d.error)


def test_no_content_length_uses_known_size(tmp_path):
    data = make_wheel()
    d = gpu.Download(gpu_dir=str(tmp_path), package=pkg_for(data),
                     opener=lambda url, timeout: FakeResp(data, length=False))
    d._run()
    assert d.error is None


def test_redownload_replaces_old_files(tmp_path):
    run(tmp_path, make_wheel())
    (tmp_path / "bin" / "stale.dll").write_bytes(b"old")
    d = run(tmp_path, make_wheel())
    assert d.error is None
    assert not (tmp_path / "bin" / "stale.dll").exists()


def test_marker_version_mismatch_means_not_ready(tmp_path):
    run(tmp_path, make_wheel())
    (tmp_path / gpu.MARKER).write_text(json.dumps({"version": "0.0"}))
    assert gpu.downloaded_dir(str(tmp_path)) is None


def test_marker_without_dlls_means_not_ready(tmp_path):
    run(tmp_path, make_wheel())
    os.remove(tmp_path / "bin" / "cublasLt64_12.dll")
    assert gpu.downloaded_dir(str(tmp_path)) is None


def test_corrupt_marker(tmp_path):
    run(tmp_path, make_wheel())
    (tmp_path / gpu.MARKER).write_text("{not json")
    assert gpu.downloaded_dir(str(tmp_path)) is None


def test_package_is_pinned():
    p = gpu.PACKAGE
    assert p["url"] and p["url"].startswith("https://files.pythonhosted.org/")
    assert len(p["sha256"]) == 64 and int(p["sha256"], 16) >= 0
    assert p["size"] > 100_000_000


@pytest.mark.slow
def test_real_package_header_reachable():
    """The pinned URL still exists on PyPI (HEAD request only)."""
    import urllib.request
    req = urllib.request.Request(gpu.PACKAGE["url"], method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        assert int(r.headers["Content-Length"]) == gpu.PACKAGE["size"]
