<p align="center"><img src="logo.png" width="96" alt="uWhisper logo"></p>

# uWhisper

Press a shortcut anywhere in Windows, talk, press it again — your words are typed into
whatever you were writing in. Speech-to-text runs **entirely on your own PC** with
OpenAI's Whisper model: nothing is uploaded, no account, no subscription.

## Download

**[Download uWhisper-Setup.exe](../../releases/latest)** (Windows 10/11, 64-bit, ~55 MB)

Windows may say *"Windows protected your PC"* because the installer isn't code-signed.
Click **More info → Run anyway**.

## How to use

1. Press **Ctrl+Shift+R** to start recording. A small waveform appears in the corner.
2. Press **Ctrl+Shift+R** again (or click the waveform) to stop.
3. The text is pasted where your cursor was and copied to the clipboard.

Click the uWhisper icon in the taskbar tray (you may need the **^** arrow) to change the
shortcut, pick a microphone, or tune the speech model.

## Good to know

- **First start downloads the speech model** (about 1.6 GB), so the very first recording
  can take a few minutes.
- **NVIDIA graphics card?** uWhisper downloads GPU support once (about 550 MB) and then
  transcribes many times faster. Until then, and on PCs without an NVIDIA card, it runs
  on the CPU.
- **Your recordings** are saved in `C:\Users\<you>\Documents\uWhisper` — audio in `audio`,
  text in `text`. They stay on this PC (never in OneDrive). Audio is deleted after
  **30 days** by default; change that under *Keep audio for* in the tray window.
  Transcripts are kept forever.
- **Uninstall** from *Settings → Apps → Installed apps*. You'll be asked whether to also
  delete the speech model and your recordings.

## Building from source

Needs Windows and Python 3.13.

```powershell
python -m venv .venv
.venv\Scripts\pip install faster-whisper keyboard numpy pillow pyperclip pystray sounddevice soundfile soxr pyinstaller pytest
.venv\Scripts\pythonw uWhisper.pyw                                     # run it
.venv\Scripts\python -m pytest tests                                   # run the tests
powershell -ExecutionPolicy Bypass -File build_installer.ps1           # build dist\uWhisper-Setup-*.exe
```

Building the installer also needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install --id JRSoftware.InnoSetup -e`).

## License

uWhisper is free to use, share and modify for **noncommercial** purposes under the
[PolyForm Noncommercial License 1.0.0](LICENSE). Keep the credit notice in any copy:

> Required Notice: Copyright (c) 2026 kaitlyn-serena-bush (https://github.com/kaitlyn-serena-bush/uWhisper)

Selling it or using it commercially is not allowed.

It's built on open-source software, listed with their licenses in
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
