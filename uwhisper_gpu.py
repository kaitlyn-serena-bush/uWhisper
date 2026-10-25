"""On-demand GPU support for uWhisper.

The app ships without NVIDIA's libraries (they're huge). On a PC with an NVIDIA
card, the first run downloads cuBLAS once (NVIDIA's official package on PyPI,
checked against a pinned SHA-256), unpacks just the two DLLs it needs, and keeps
them in %USERPROFILE%\\.uwhisper\\gpu. Until that finishes, transcription runs on
the CPU. PCs without an NVIDIA card download nothing.

Only cuBLAS is needed: tested with large-v3-turbo on CUDA, ctranslate2 never
loads the separate cuDNN / NVRTC libraries (and it ships its own cudnn64_9.dll).

Important: CTranslate2 must not try CUDA before these DLLs exist. A failed CUDA
attempt poisons CUDA for the rest of the process (a later try hangs), so the
engine asks `ready()` first and goes straight to CPU until then.
"""
import hashlib
import json
import os
import shutil
import site
import sys
import tempfile
import threading
import urllib.request
import zipfile

GPU_DIR = os.path.join(os.path.expanduser("~"), ".uwhisper", "gpu")
MARKER = "ready.json"

# NVIDIA cuBLAS for CUDA 12 (same version the app was built and tested with).
PACKAGE = {
    "name": "nvidia-cublas-cu12",
    "version": "12.9.2.10",
    "url": "https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
    "sha256": "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
    "size": 553162896,  # bytes (553 MB)
    "files": ["cublas64_12.dll", "cublasLt64_12.dll"],
}

_added = set()


# ---------------------------------------------------------------- where the DLLs are
def bundled_dirs():
    """NVIDIA DLL folders that came with Python itself (developer setup with pip packages)."""
    roots = list(getattr(site, "getsitepackages", lambda: [])())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    if hasattr(sys, "_MEIPASS"):
        roots.insert(0, sys._MEIPASS)
    found = []
    for root in roots:
        b = os.path.join(root, "nvidia", "cublas", "bin")
        if os.path.isfile(os.path.join(b, "cublas64_12.dll")):
            found.append(b)
    return found


def downloaded_dir(gpu_dir=GPU_DIR):
    """The folder with our downloaded DLLs, if the download finished and is intact."""
    try:
        with open(os.path.join(gpu_dir, MARKER), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    if info.get("version") != PACKAGE["version"]:
        return None
    d = os.path.join(gpu_dir, "bin")
    if all(os.path.isfile(os.path.join(d, n)) for n in PACKAGE["files"]):
        return d
    return None


def ready(gpu_dir=GPU_DIR):
    """True if cuBLAS is available (bundled or downloaded) and its folder is on the DLL path."""
    dirs = bundled_dirs()
    d = downloaded_dir(gpu_dir)
    if d:
        dirs.append(d)
    for d in dirs:
        if d not in _added:
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                continue
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            _added.add(d)
    return bool(dirs)


def has_nvidia_gpu():
    """True if an NVIDIA card with a working driver is present. Safe to call any time:
    counting devices only needs the driver, so it doesn't poison CUDA."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.WinDLL("nvcuda.dll")  # the NVIDIA driver
    except OSError:
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# ---------------------------------------------------------------- download
class Download:
    """Downloads and unpacks cuBLAS in a background thread.

    progress: 0..1 while downloading. done/error set at the end.
    """

    def __init__(self, on_done=None, gpu_dir=GPU_DIR, package=None, opener=None):
        self.package = package or PACKAGE
        self.gpu_dir = gpu_dir
        self.on_done = on_done
        self.opener = opener or urllib.request.urlopen
        self.progress = 0.0
        self.error = None
        self.done = threading.Event()
        self.cancelled = False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="gpu-download").start()
        return self

    def cancel(self):
        self.cancelled = True

    def _run(self):
        try:
            self._download_and_unpack()
        except Exception as e:
            self.error = e
        finally:
            self.done.set()
            if self.on_done:
                try:
                    self.on_done(self)
                except Exception:
                    pass

    def _download_and_unpack(self):
        pkg = self.package
        os.makedirs(self.gpu_dir, exist_ok=True)
        part = os.path.join(self.gpu_dir, "download.part")
        sha = hashlib.sha256()
        got = 0
        with self.opener(pkg["url"], timeout=60) as resp, open(part, "wb") as f:
            total = int(resp.headers.get("Content-Length") or pkg.get("size") or 0)
            while True:
                if self.cancelled:
                    raise RuntimeError("cancelled")
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                if total:
                    self.progress = min(0.99, got / total)
        try:
            if pkg.get("size") and got != pkg["size"]:
                raise RuntimeError(f"download incomplete ({got} of {pkg['size']} bytes)")
            if sha.hexdigest() != pkg["sha256"]:
                raise RuntimeError("download is corrupted (checksum mismatch)")
            # Unpack only the DLLs we need into a temp folder, then swap it in.
            tmp = tempfile.mkdtemp(prefix="bin-", dir=self.gpu_dir)
            try:
                with zipfile.ZipFile(part) as z:
                    names = {os.path.basename(n).lower(): n for n in z.namelist()
                             if n.lower().endswith(".dll")}
                    for want in pkg["files"]:
                        src = names.get(want.lower())
                        if not src:
                            raise RuntimeError(f"{want} missing from package")
                        with z.open(src) as s, open(os.path.join(tmp, want), "wb") as d:
                            shutil.copyfileobj(s, d, 1 << 20)
                final = os.path.join(self.gpu_dir, "bin")
                if os.path.isdir(final):
                    shutil.rmtree(final, ignore_errors=True)
                os.replace(tmp, final)
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            with open(os.path.join(self.gpu_dir, MARKER), "w", encoding="utf-8") as f:
                json.dump({"package": pkg["name"], "version": pkg["version"]}, f)
            self.progress = 1.0
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
