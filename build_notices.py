"""Write THIRD_PARTY_NOTICES.txt: the licenses of everything bundled in uWhisper.exe.

Run by build_installer.ps1; the file is installed next to uWhisper.exe.
License texts are read from each installed package, so they stay current.
"""
import importlib.metadata as md
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "THIRD_PARTY_NOTICES.txt")

# Python packages bundled into the exe (PyAV/FFmpeg and NVIDIA libraries are NOT bundled).
PACKAGES = [
    "faster-whisper", "ctranslate2", "onnxruntime", "numpy", "pillow", "pystray", "keyboard",
    "pyperclip", "sounddevice", "soundfile", "soxr", "tokenizers", "huggingface-hub", "hf-xet",
    "tqdm", "cffi", "pycparser", "PyYAML", "filelock", "fsspec", "packaging",
    "typing_extensions", "six", "idna", "anyio", "h11", "click", "flatbuffers", "protobuf",
    "httpx", "httpcore", "httpx2", "httpcore2", "certifi", "sniffio", "coloredlogs",
    "humanfriendly", "sympy", "mpmath", "shellingham", "typer", "rich", "pyreadline3",
]

# Native parts that come with those packages or with Python, plus things downloaded later.
OTHER = """\
Python 3.13 runtime - Python Software Foundation License Version 2
  https://docs.python.org/3/license.html
Tcl/Tk (used by tkinter) - Tcl/Tk License (BSD-style)
  https://www.tcl.tk/software/tcltk/license.html
OpenSSL - Apache License 2.0
  https://www.openssl.org/source/license.html
PortAudio (bundled with sounddevice) - MIT-style license
  https://www.portaudio.com/license.html
libsndfile (bundled with soundfile) - GNU LGPL 2.1 or later
  https://github.com/libsndfile/libsndfile/blob/master/COPYING
  uWhisper ships libsndfile as a separate, replaceable DLL in the _internal folder.
Intel OpenMP runtime libiomp5md.dll (bundled with ctranslate2) - Intel Simplified Software License
  https://www.intel.com/content/www/us/en/developer/articles/license/onemkl-license-faq.html

Downloaded on first run (not included in the installer):
OpenAI Whisper model weights (large-v3-turbo, CTranslate2 conversion) - MIT License
  https://github.com/openai/whisper/blob/main/LICENSE
NVIDIA cuBLAS (only on PCs with an NVIDIA GPU; downloaded by the user's PC directly from
  NVIDIA's official package on PyPI) - NVIDIA Software License Agreement
  https://docs.nvidia.com/cuda/eula/index.html
"""

LGPL_NOTE = """\
pystray (LGPL 3.0) and soxr / libsndfile (LGPL 2.1+) are used unmodified and shipped as
separate, replaceable files in the _internal folder, as those licenses allow. Their source
code is available from the project pages listed below.
"""


def license_texts(dist):
    texts = []
    for f in dist.files or []:
        name = os.path.basename(str(f)).upper()
        if name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")) or \
                "/LICENSES/" in str(f).upper().replace("\\", "/"):
            try:
                data = f.locate().read_bytes()
            except OSError:
                continue
            if bytes([0]) in data[:4096]:
                continue  # not a text file
            texts.append((str(f), data.decode("utf-8", "replace")))
    return texts


def main():
    out = ["uWhisper - third-party notices", "=" * 30, "",
           "uWhisper is free for noncommercial use (PolyForm Noncommercial 1.0.0, see LICENSE). It is built on the open-source",
           "software listed below. Each component keeps its own license.", "",
           LGPL_NOTE, "-" * 78, "OTHER COMPONENTS", "-" * 78, OTHER]
    missing = []
    for name in PACKAGES:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        m = dist.metadata
        lic = m.get("License-Expression") or (m.get("License") or "")
        if len(lic) > 80:
            lic = lic.splitlines()[0][:80]
        home = m.get("Home-page") or ""
        for url in m.get_all("Project-URL") or []:
            if not home:
                home = url.split(",", 1)[-1].strip()
        out += ["-" * 78, f"{m['Name']} {m['Version']}  -  {lic}".rstrip(), home, "-" * 78]
        texts = license_texts(dist)
        extra = os.path.join(HERE, "licenses", f"{name}.txt")  # for packages that don't ship one
        if not texts and os.path.isfile(extra):
            with open(extra, encoding="utf-8") as f:
                texts = [(f"licenses/{name}.txt", f.read())]
        if not texts:
            missing.append(name)
            out.append("(license text not included in the package; see the project page)")
        for path, text in texts:
            out += [f"[{path}]", text.strip(), ""]
    with open(OUT, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {OUT} ({os.path.getsize(OUT) // 1024} KB)")
    if missing:
        print("no license file shipped in:", ", ".join(missing), file=sys.stderr)


if __name__ == "__main__":
    main()
