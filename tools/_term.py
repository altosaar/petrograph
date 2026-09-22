"""
_term — what the tools say to a terminal, said the same way by all of them.

Not a script. Every tool here finishes by printing where it put something, and several
print a timing table on the way. That was seven copies of the hyperlink helper and two of
the timing class, which is how two of them ended up supporting four terminals while the
rest supported six.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import os
import sys
import threading
import time
from pathlib import Path

# OSC 8 is the terminal hyperlink escape: iTerm2, WezTerm, kitty, Ghostty and VS Code render
# it as a real link. Terminal.app ignores OSC 8 but makes a bare file:// URL cmd-clickable,
# so that is the fallback. Piped output gets the plain path.
OSC8_TERMS = {"iTerm.app", "WezTerm", "vscode", "ghostty", "Hyper", "rio"}


def link(path: Path) -> str:
    """A clickable path, where the terminal can render one."""
    p = Path(path).expanduser().resolve()
    if not sys.stdout.isatty():
        return str(p)
    uri = p.as_uri()  # percent-encodes spaces and non-ASCII for us
    # kitty announces itself through TERM rather than TERM_PROGRAM, hence the second test.
    if os.environ.get("TERM_PROGRAM", "") in OSC8_TERMS or "kitty" in os.environ.get("TERM", ""):
        return f"\033]8;;{uri}\033\\{p}\033]8;;\033\\"
    return uri


def say(msg: str = "") -> None:
    """Stage banners are flushed: the subprocesses that follow write straight to the same
    stdout, and a buffered banner would land after the output it announces."""
    print(msg, flush=True)


class Steps:
    """Wall-clock per stage, reported as a table at the end.

    Two kinds of tool use this and they want it for opposite reasons. In the ones that call
    an API, most of the time is network round-trips, and the table separates what was waited
    on from what was computed. In the offline ones no stage is obviously expensive from the
    outside, and the table is the only way to know where a slow render went — it is also how
    the two mix engines are compared: the same table, different rows.
    """

    def __init__(self) -> None:
        self.marks: list[tuple[str, float]] = []
        self._t = self._start = time.perf_counter()

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        self.marks.append((label, now - self._t))
        self._t = now

    def report(self, title: str = "timing") -> None:
        if not self.marks:
            return
        total = time.perf_counter() - self._start
        width = max(len(l) for l, _ in self.marks)
        print(f"\n{title}:")
        for label, dt in self.marks:
            bar = "▏" * max(1, round(30 * dt / total)) if dt > 0 else ""
            print(f"  {label:<{width}}  {dt:7.2f}s  {100 * dt / total:4.1f}%  {bar}")
        print(f"  {'─' * width}  {'─' * 7}")
        print(f"  {'total':<{width}}  {total:7.2f}s")


class Waiting:
    """A one-line "still working" indicator for a subprocess that can go minutes silent.

    A model call is the one stage here with no natural progress to report: the banner prints,
    and then nothing happens for several minutes whether the run is thinking or the connection
    has died. This paints a line that keeps moving on a clock of its own, so the wait is
    visibly a wait rather than a hang, and lets the caller hand it a note whenever the
    subprocess does say something.

    Painting is done by a thread rather than by the caller, because the caller is blocked on
    the subprocess's output — which is exactly the thing that isn't arriving.

    Piped output (`just` into a log, CI) gets a plain line every `quiet` seconds instead of a
    repainted one: carriage returns in a log file are noise, but a record of how long the call
    took to say anything is the same information the terminal is showing live.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str, *, indent: str = "    ", quiet: float = 30.0) -> None:
        self.label, self.indent, self.quiet = label, indent, quiet
        self.note = ""
        self.tty = sys.stdout.isatty()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._paint, daemon=True)
        self._start = time.perf_counter()
        self._width = 0

    # ── what the caller drives ────────────────────────────────────────────────────────────

    def __enter__(self) -> "Waiting":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self._clear()

    def say(self, note: str) -> None:
        """The subprocess said something. Shown until the next thing it says."""
        # One line, and short enough to leave the clock visible in a narrow terminal.
        note = " ".join(note.split())
        self.note = note[:57] + "…" if len(note) > 58 else note

    # ── the painting ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def elapsed(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"

    def _line(self, frame: str) -> str:
        secs = self.elapsed(time.perf_counter() - self._start)
        return f"{self.indent}{frame} {self.label} · {secs}" + (f" · {self.note}" if self.note else "")

    def _clear(self) -> None:
        if self.tty and self._width:
            print("\r" + " " * self._width + "\r", end="", flush=True)

    def _paint(self) -> None:
        tick = 0
        while not self._stop.is_set():
            if self.tty:
                line = self._line(self.FRAMES[tick % len(self.FRAMES)])
                # Pad to the widest line printed so far: a note shrinking mid-spin would
                # otherwise leave the tail of the longer one behind it.
                self._width = max(self._width, len(line))
                print(f"\r{line:<{self._width}}", end="", flush=True)
                self._stop.wait(0.1)
                tick += 1
            else:
                self._stop.wait(self.quiet)
                if not self._stop.is_set():
                    print(self._line("·"), flush=True)
