"""uWhisper's window colors, taken from the default_flame_pallet palette.
(Edit default_flame_pallet.py to change them.)"""
from default_flame_pallet import PALETTE as P, dwm_color, rgb  # noqa: F401

BG = P["bg"]                  # deep roast brown (window background)
CARD = P["card"]              # raised boxes
HOVER = P["hover"]            # row hover
LINE = P["line"]              # dividers, empty tracks
RAISED = P["raised"]          # keycaps, slider track
POPUP = P["popup"]            # dropdown list
POPUP_EDGE = P["popup_edge"]
TIP = P["tip"]                # hover bubble
TIP_EDGE = P["tip_edge"]
SCROLL = P["scroll"]

TEXT = P["text"]              # beige
SUB = P["sub"]                # muted beige-brown
DIM = P["dim"]
ON_ACCENT = P["on_accent"]    # text/knobs on orange

ACCENT = P["accent"]          # fire orange
SWITCH_EDGE_ON = P["switch_edge"]
EMBER = P["ember"]            # warm amber/yellow
RED = P["red"]
GREEN = P["ready"]            # "ready" dot
QUIT = P["quit"]

BORDER_DWM = dwm_color("border")  # window edge as 0x00BBGGRR

# recording pill messages
OK_TEXT = P["ok_text"]
WARN_TEXT = P["warn_text"]
ERR_TEXT = P["err_text"]
