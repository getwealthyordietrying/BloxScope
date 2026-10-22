"""Optional terminal color and measured preparation progress."""
import os
import sys
import time
import shutil

from .common import safe, say

ART = r''' _._     _,-'""`-._
(,-.`._,'(       |\`-/|
    `-.-' \ )-`( , o o)
          `-    \`_`"'-\
'''.rstrip("\n")
COLORS = {"heading": "36", "success": "32", "notice": "33"}
_active = None


def finish_active():
    if _active is not None:
        _active.finish()


def supports_color(stream=None):
    stream = sys.stdout if stream is None else stream
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb" or not stream.isatty():
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        mode = wintypes.DWORD()
        handle = kernel.GetStdHandle(-11 & 0xffffffff)
        return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel.SetConsoleMode(handle, mode.value | 4))
    return True


def decorate(text, tone):
    if tone in COLORS and supports_color():
        return f"\x1b[{COLORS[tone]}m{text}\x1b[0m"
    return text


def banner():
    for line in ART.splitlines():
        say(line, tone="heading")
    say("BloxScope | local public resource search", tone="heading")


def progress_text(label, done, total=None, unit="", *, width=80, blocks=False):
    label = safe(label)
    suffix = f" {unit}" if unit else ""
    label = label[:max(4, width//3)]
    if total is None:
        return f"{label} {'|/-\\'[done % 4]} {done:,}{suffix} (total unknown)"[:width]
    if total <= 0:
        return f"{label}: no work needed (0{suffix})"
    percent = min(100, max(0, done*100//total))
    tail = f" {done:,}/{total:,}{suffix} [{percent:3d}%]"
    if len(tail)+len(label)+5 > width:
        label = label[:max(0, width-len(tail)-5)]
    length = max(1, width-len(label)-len(tail)-4)
    filled = min(length, max(0, done*length//total))
    return f"{label} |{('█' if blocks else '#')*filled}{' '*(length-filled)}|{tail}"[:width]


class Progress:
    """Known totals get bars; other work keeps its stage/count messages."""
    def __init__(self):
        self.last_label = None
        self.last_time = 0
        self.visible = False
        self.line_width = 0
        self.last_event = None

    def __call__(self, text):
        self.finish()
        tone = "success" if text.startswith(("Ready:", "Verified cached")) else "heading"
        if text.strip().startswith(("Not present", "Request failed")):
            tone = "notice"
        say(text, tone=tone)

    def update(self, label, done, total=None, unit=""):
        global _active
        event = (label, done, total, unit)
        if event == self.last_event:
            return
        tick = time.monotonic()
        tty = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        if label == self.last_label and total != done and tick-self.last_time < (0.15 if tty else 2):
            return
        if label != self.last_label:
            self.finish()
        width = max(1, shutil.get_terminal_size((80, 24)).columns-1)
        blocks = False
        if tty:
            try:
                "█".encode(getattr(sys.stdout, "encoding", None) or "ascii")
                blocks = True
            except UnicodeEncodeError:
                pass
        text = progress_text(label, done, total, unit, width=width, blocks=blocks)
        if tty:
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            text = text.encode(encoding, "backslashreplace").decode(encoding)[:width]
            padding = " "*max(0, self.line_width-len(text))
            sys.stdout.write("\r"+decorate(text+padding, "heading"))
            sys.stdout.flush()
            self.visible, self.line_width, _active = True, len(text), self
        else:
            say(text, tone="heading")
        self.last_label, self.last_time = label, tick
        self.last_event = event
        if total is not None and done >= total:
            self.finish()

    def finish(self):
        global _active
        if self.visible:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.visible = False
        if _active is self:
            _active = None
        self.last_label = None


def report(progress, label, done, total=None, unit=""):
    update = getattr(progress, "update", None)
    if update:
        update(label, done, total, unit)
    elif total == done or done == 0:
        progress(progress_text(label, done, total, unit))
