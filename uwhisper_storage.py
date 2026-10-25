"""Where uWhisper keeps recordings, and the daily clean-up of old audio.

Plain Python (no Windows calls) so it can be tested on its own.

Layout:  C:\\Users\\<name>\\Documents\\uWhisper\\audio\\<timestamp>.wav
         C:\\Users\\<name>\\Documents\\uWhisper\\text\\<timestamp>.txt
         (always on this PC - never the OneDrive-synced Documents)

Clean-up deletes old audio only. Transcripts are tiny and are kept forever.
Only files uWhisper made itself (named like 2026-10-24_22-38-42.wav) are ever
deleted, so anything else a user drops in the folder is left alone.
"""
import datetime
import os
import re
import shutil

UNITS = {"days": 1, "weeks": 7, "months": 30, "years": 365}
FOREVER = "forever"
DEFAULT_CLEANUP = {"amount": 30, "unit": "days", "last_run": None}
MAX_AMOUNT = 9999

STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.wav$", re.IGNORECASE)
STAMP_FMT = "%Y-%m-%d_%H-%M-%S"


# ---------------------------------------------------------------- settings
def normalize_cleanup(c):
    """Fill in defaults and fix bad values from a hand-edited or old config."""
    out = dict(DEFAULT_CLEANUP)
    if isinstance(c, dict):
        out.update({k: c[k] for k in DEFAULT_CLEANUP if k in c})
    if not isinstance(out["unit"], str) or (out["unit"] not in UNITS and out["unit"] != FOREVER):
        out["unit"] = DEFAULT_CLEANUP["unit"]
    try:
        out["amount"] = int(out["amount"])
    except (TypeError, ValueError, OverflowError):  # "abc", None, Infinity, NaN
        out["amount"] = DEFAULT_CLEANUP["amount"]
    out["amount"] = min(MAX_AMOUNT, max(1, out["amount"]))
    if not isinstance(out["last_run"], str):
        out["last_run"] = None
    return out


def keep_days(amount, unit):
    """How many days audio is kept, or None for forever."""
    if unit == FOREVER:
        return None
    return max(1, int(amount)) * UNITS[unit]


def describe(amount, unit):
    """'30 days', '1 week', 'Forever'."""
    if unit == FOREVER:
        return "Forever"
    word = unit[:-1] if amount == 1 else unit
    return f"{amount} {word}"


def due(last_run, today):
    """True when clean-up hasn't run yet today (today is a datetime.date)."""
    return last_run != today.isoformat()


# ---------------------------------------------------------------- clean-up
def recorded_at(filename):
    """The time a recording was made, from its name. None if uWhisper didn't name it."""
    m = STAMP_RE.fullmatch(filename)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), STAMP_FMT)
    except ValueError:
        return None


def old_audio(audio_dir, days, now, keep=()):
    """Paths of uWhisper recordings older than `days` days. `keep` paths are skipped."""
    if days is None or not os.path.isdir(audio_dir):
        return []
    try:
        cutoff = now - datetime.timedelta(days=days)
    except OverflowError:  # e.g. 9999 years: nothing is that old
        return []
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep if p}
    found = []
    for name in sorted(os.listdir(audio_dir)):
        when = recorded_at(name)
        path = os.path.join(audio_dir, name)
        if when is None or when >= cutoff or not os.path.isfile(path):
            continue
        if os.path.normcase(os.path.abspath(path)) in keep:
            continue
        found.append(path)
    return found


def delete_old_audio(audio_dir, days, now, keep=(), log=None):
    """Delete old recordings. Returns (deleted count, bytes freed). Never raises."""
    count = freed = 0
    try:
        paths = old_audio(audio_dir, days, now, keep)
    except (OSError, OverflowError, ValueError) as e:
        if log:
            log(f"clean-up could not read {audio_dir}: {e}")
        return 0, 0
    for path in paths:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            count += 1
            freed += size
        except OSError as e:  # in use, read-only, already gone...
            if log:
                log(f"could not delete {path}: {e}")
    return count, freed


# ---------------------------------------------------------------- folders
def make_folders(out_dir):
    audio, text = os.path.join(out_dir, "audio"), os.path.join(out_dir, "text")
    os.makedirs(audio, exist_ok=True)
    os.makedirs(text, exist_ok=True)
    return audio, text


def migrate(old_dir, new_dir, log=None):
    """Move recordings from the old folder (Music\\uWhisper) into the new one.

    Never overwrites: a file that already exists in the new folder stays in the
    old one. The old folder is removed only if it ends up empty.
    Returns how many files were moved.
    """
    if not old_dir or not os.path.isdir(old_dir):
        return 0
    if os.path.normcase(os.path.abspath(old_dir)) == os.path.normcase(os.path.abspath(new_dir)):
        return 0
    moved = 0
    for sub in ("audio", "text"):
        src = os.path.join(old_dir, sub)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(new_dir, sub)
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            s, d = os.path.join(src, name), os.path.join(dst, name)
            if not os.path.isfile(s) or os.path.exists(d):
                continue
            try:
                shutil.move(s, d)
                moved += 1
            except OSError as e:
                if log:
                    log(f"could not move {s}: {e}")
    # Tidy up empty leftovers only.
    for p in (os.path.join(old_dir, "audio"), os.path.join(old_dir, "text"), old_dir):
        try:
            os.rmdir(p)
        except OSError:
            pass
    return moved
