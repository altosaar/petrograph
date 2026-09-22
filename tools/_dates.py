"""
_dates — the one piece of calendar arithmetic these tools need.

Not a script. No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import datetime as dt


def months_ago(months: int, today: dt.date | None = None) -> dt.date:
    """Calendar-accurate: 12 months before Mar 31 is Mar 31, not 365 days.

    Clamps to the length of the target month, so one month before Mar 31 is Feb 28 (or 29),
    not an invalid date. `today` is injectable so the behaviour can be checked on a fixed day.
    """
    today = today or dt.date.today()
    y, m = divmod(today.year * 12 + today.month - 1 - months, 12)
    day = min(today.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return dt.date(y, m + 1, day)
