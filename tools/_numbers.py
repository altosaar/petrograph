#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_numbers — say the digits out loud, in English, before any engine is asked to read them.

A letter is written to be read on a screen, so it is full of figures: $4,020, 90%, 4/12,
2.5km, 9-10pm, 2026-04-05. Every TTS engine has its own opinion about what those mean, and
the opinions are neither the same nor stable — ElevenLabs reads "$2,480" one way, Chatterbox
another, and both of them mangle a bare "2,000" often enough that a week's narration comes
back with a garbled sentence in the middle of it. The fix that works for every engine, and
for one that has not been written yet, is to stop sending digits: spell them here, in the
shared text path, and let the engines read words like the words around them.

So this is deliberately upstream of the seam. strip_markdown calls it, which means the local
engine and the remote one narrate exactly the same sentence — the property _speech.py exists
to protect — and a --dry-run prices what will actually be spoken rather than what the
markdown happens to weigh. Spelled text is longer than the digits it replaces, so the
estimate moves, and so does the chunk cache: a letter rendered before this existed will
re-synthesise, because it is not the same letter any more.

Canonical American forms, no cleverness:

    2,000       two thousand                 (no "and", the way a cheque is written)
    $2,480      two thousand four hundred eighty dollars
    $18.75      eighteen dollars and seventy-five cents
    90%         ninety percent
    8.4         eight point four              (digits after the point, one at a time)
    25th        twenty-fifth
    2026        twenty twenty-six            (a bare four-digit number is a year)
    2026-04-05  April fifth, twenty twenty-six
    4/12        April twelfth             (m/d, the way these letters date things)
    9pm         nine PM
    9-10pm      nine to ten PM
    2.5km       two point five km            (the unit is left for the voice to say)
    401k        four oh one k                (a short list of figures that are names)
    0020260704  zero zero two zero…          (leading zero, or very long: an identifier,
                                              read out digit by digit, not as a quantity)

What it leaves alone: anything that reads as a reference rather than a quantity — a URL, an
email address, a path, a filename. "share/audio/compaction-260405.mp3" is a name, and
a name spelled out as two hundred sixty thousand of anything is worse than the digits were.

Runnable, for checking what a sentence will turn into before spending a render on it:

    ./tools/_numbers.py 'we spent $2,480 on the 25th'   # → what the voice will be given
    ./tools/_numbers.py                                 # → the table above, verified
"""

from __future__ import annotations
import re
import sys

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (10 ** 3, "thousand"))
MONTHS = ("", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")
# The ordinals English refuses to build by rule; everything else is -th, and -y becomes -ieth.
IRREGULAR = {"one": "first", "two": "second", "three": "third", "five": "fifth",
             "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


# ── numbers, as words ─────────────────────────────────────────────────────────────────────

def cardinal(n: int) -> str:
    """2000 → "two thousand". No "and": the form a cheque is written in."""
    if n < 0:
        return f"minus {cardinal(-n)}"
    if n < 20:
        return ONES[n]
    if n < 100:
        tens, rest = divmod(n, 10)
        return TENS[tens] + (f"-{ONES[rest]}" if rest else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{ONES[hundreds]} hundred" + (f" {cardinal(rest)}" if rest else "")
    for value, name in SCALES:
        if n >= value:
            count, rest = divmod(n, value)
            return f"{cardinal(count)} {name}" + (f" {cardinal(rest)}" if rest else "")
    return str(n)  # unreachable below a quadrillion, and honest above it


def ordinal(n: int) -> str:
    """25 → "twenty-fifth". Only the last word changes; the rest is the cardinal."""
    words = cardinal(n)
    tail = re.search(r"[a-z]+$", words)
    last = tail.group(0)
    if last in IRREGULAR:
        spelled = IRREGULAR[last]
    elif last.endswith("y"):
        spelled = f"{last[:-1]}ieth"
    else:
        spelled = f"{last}th"
    return words[:tail.start()] + spelled


def digits(s: str) -> str:
    """"0704" → "zero seven zero four". For identifiers, which are not quantities."""
    return " ".join(ONES[int(c)] for c in s if c.isdigit())


def year(n: int) -> str:
    """2026 → "twenty twenty-six"; 2005 → "two thousand five"; 1900 → "nineteen hundred"."""
    if not 1000 <= n <= 2999 or n % 1000 == 0 or 2000 <= n < 2010:
        return cardinal(n)
    century, rest = divmod(n, 100)
    if rest == 0:
        return f"{cardinal(century)} hundred"
    if rest < 10:
        return f"{cardinal(century)} oh {ONES[rest]}"
    return f"{cardinal(century)} {cardinal(rest)}"


def quantity(s: str) -> str:
    """The words for one written number — "2,480", "8.4", "0.40" — commas and point included."""
    whole, _, frac = s.replace(",", "").partition(".")
    words = cardinal(int(whole or 0))
    return f"{words} point {digits(frac)}" if frac else words


# ── numbers, as they appear in a letter ───────────────────────────────────────────────────

# A filename's extension, and the shapes a path takes. Together they decide the one question
# this module asks of a token before touching it: is this a quantity, or a name? Spelling the
# digits inside a name makes it less speakable, not more. A single slash is deliberately not
# enough — "4/12" is a date and "SKU-57/LOT-47" is two codes — so a path has to look like
# one: rooted, relative, deeper than one level, or carrying a file extension.
EXTENSION_RE = re.compile(r"\.(?:md|mp3|wav|flac|py|sh|json|jsonl|txt|html|csv|conf|png|jpe?g|pdf)\b")


def _is_reference(token: str) -> bool:
    """A URL, an email address, a path, a filename — a name, not a number."""
    return ("://" in token or "@" in token or bool(EXTENSION_RE.search(token))
            or token.startswith(("/", "./", "../", "~/")) or token.count("/") > 1)


# The figures that are names rather than quantities, and are said as neither a count nor a
# string of digits: nobody has ever asked about their four hundred one k. Kept deliberately
# short — an entry here is a claim that English has a fixed way of saying this one, not a
# place to tune how the voice reads ordinary numbers.
IDIOMS = {"401k": "four oh one k", "403b": "four oh three b", "457b": "four fifty-seven b"}
IDIOM_RE = re.compile(rf"\b({'|'.join(IDIOMS)})\b", re.I)

MONEY_RE = re.compile(r"\$(\d(?:[\d,]*\d)?)(?:\.(\d{1,2}))?")
PERCENT_RE = re.compile(r"(\d(?:[\d,]*\d)?(?:\.\d+)?)\s*%")
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
CLOCK = r"(\d{1,2})(?::(\d{2}))?"
TIME_RANGE_RE = re.compile(rf"\b{CLOCK}\s*[-–—]\s*{CLOCK}\s*([ap])\.?m\.?\b", re.I)
TIME_RE = re.compile(rf"\b{CLOCK}\s*([ap])\.?m\.?\b", re.I)
ORDINAL_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.I)
IDENTIFIER_RE = re.compile(r"(?<![\d,.])(0\d+|\d{7,})\b")  # leading zero, or too long to count
NUMBER_RE = re.compile(r"\b\d(?:[\d,]*\d)?(?:\.\d+)?\b")
# 2.5km → 2.5 km, so the number can be spelled without gluing itself to the unit. Runs after
# the rules above, whose suffixes (25th, 9pm) are letters that must stay attached.
GLUED_RE = re.compile(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)")


def _money(m: re.Match) -> str:
    dollars = int(m.group(1).replace(",", ""))
    words = f"{cardinal(dollars)} dollar{'' if dollars == 1 else 's'}"
    if m.group(2):
        cents = int(m.group(2).ljust(2, "0"))
        if cents:
            words += f" and {cardinal(cents)} cent{'' if cents == 1 else 's'}"
    return words


def _clock(hour: str, minute: str | None) -> str:
    words = cardinal(int(hour))
    if minute and int(minute):
        words += f" oh {ONES[int(minute)]}" if int(minute) < 10 else f" {cardinal(int(minute))}"
    return words


def _date(month: int, day: int, yr: int | None) -> str:
    words = f"{MONTHS[month]} {ordinal(day)}"
    return f"{words}, {year(yr)}" if yr else words


def _slash_date(m: re.Match) -> str:
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return m.group(0)  # a ratio or a fraction; the plain-number rule will get the parts
    yr = int(m.group(3)) if m.group(3) else None
    return _date(month, day, yr + 2000 if yr is not None and yr < 100 else yr)


def _number(m: re.Match) -> str:
    raw = m.group(0)
    # A bare four-digit number in a letter is a year far more often than it is a count, and
    # "twenty twenty-six" is how it would be said aloud. A written comma says otherwise.
    if "," not in raw and "." not in raw and len(raw) == 4 and 1000 <= int(raw) <= 2999:
        return year(int(raw))
    return quantity(raw)


RULES = (
    (IDIOM_RE, lambda m: IDIOMS[m.group(1).lower()]),
    (MONEY_RE, _money),
    (PERCENT_RE, lambda m: f"{quantity(m.group(1))} percent"),
    (ISO_RE, lambda m: _date(int(m.group(2)), int(m.group(3)), int(m.group(1)))),
    (SLASH_DATE_RE, _slash_date),
    (TIME_RANGE_RE, lambda m: f"{_clock(m.group(1), m.group(2))} to "
                              f"{_clock(m.group(3), m.group(4))} {m.group(5).upper()}M"),
    (TIME_RE, lambda m: f"{_clock(m.group(1), m.group(2))} {m.group(3).upper()}M"),
    (ORDINAL_RE, lambda m: ordinal(int(m.group(1)))),
)


def spell_numbers(text: str) -> str:
    """Every number in a line of prose, written as the words for it.

    Token by token, so a path or a filename can be left as it is; rule by rule within a
    token, most specific first. Each rule leaves words behind, and words hold no digits, so
    a later rule cannot re-read what an earlier one has already said.
    """
    if not any(c.isdigit() for c in text):
        return text
    # split(" ") rather than split(): the empty strings between runs of spaces are what makes
    # the join below give back the line's own spacing rather than a normalised version of it.
    return " ".join(t if _is_reference(t) else _spell_token(t) for t in text.split(" "))


def _spell_token(token: str) -> str:
    for pattern, replace in RULES:
        token = pattern.sub(replace, token)
    token = GLUED_RE.sub(" ", token)
    token = IDENTIFIER_RE.sub(lambda m: digits(m.group(1)), token)
    return NUMBER_RE.sub(_number, token)


# ── check ─────────────────────────────────────────────────────────────────────────────────

CASES = (
    ("2,000", "two thousand"),
    ("$2,480", "two thousand four hundred eighty dollars"),
    ("$18.75", "eighteen dollars and seventy-five cents"),
    ("$1", "one dollar"),
    ("90%", "ninety percent"),
    ("8.4", "eight point four"),
    ("0.40,", "zero point four zero,"),
    ("the 25th,", "the twenty-fifth,"),
    ("3rd", "third"),
    ("2026", "twenty twenty-six"),
    ("1905", "nineteen oh five"),
    ("2005", "two thousand five"),
    ("2026-04-05", "April fifth, twenty twenty-six"),
    ("4/12", "April twelfth"),
    ("9pm", "nine PM"),
    ("5am", "five AM"),
    ("9:30am", "nine thirty AM"),
    ("9–10pm", "nine to ten PM"),
    ("2.5km", "two point five km"),
    ("0020260704", "zero zero two zero two six zero seven zero four"),
    ("SKU-57/LOT-47", "SKU-fifty-seven/LOT-forty-seven"),
    ("Zone-2", "Zone-two"),
    ("401k-and-pension", "four oh one k-and-pension"),
    ("share/audio/compaction-260405.mp3", "share/audio/compaction-260405.mp3"),
    ("no numbers here", "no numbers here"),
    ("we spent $4,020 on the 12th, 90% of it before 9pm",
     "we spent four thousand twenty dollars on the twelfth, "
     "ninety percent of it before nine PM"),
)


def main() -> None:
    if len(sys.argv) > 1:
        print(spell_numbers(" ".join(sys.argv[1:])))
        return
    bad = 0
    for given, want in CASES:
        got = spell_numbers(given)
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {given!r} → {got!r}" + ("" if ok else f"  want {want!r}"))
    print(f"\n{len(CASES) - bad}/{len(CASES)} cases pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
