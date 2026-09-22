#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
extract_contacts — who you actually talk to, straight out of the message databases.

Reads Messages (chat.db) and, on request, WhatsApp, counts one-to-one messages over
a window, and writes the most-contacted people to JSON: totals, sent/received, a
month-by-month histogram, and the last messages of each thread. A person who
appears in both apps is one row. contacts_html.py renders it.

Local and read-only, in that order:
  · Every database is opened through the immutable URI (`file:…?immutable=1`), so
    SQLite cannot take a write lock, replay a WAL, or create a journal. Nothing is
    copied and nothing is modified.
  · No network. `dependencies = []` above is the proof, and it survives the imports:
    the underscore-prefixed modules beside this one pull in nothing but the standard
    library. There is no
    client to send anything anywhere, and no model is asked about your messages.
  · The output holds real message text, so the tool refuses to write anywhere git
    would track it.

Things about these databases that this tool exists to paper over:
  · 30% of iMessages have a NULL `text` and keep their body in `attributedBody`, an
    NSArchiver blob. `decode_body` reads it — see the comment there.
  · Group threads are excluded. Outgoing iMessage group messages carry handle_id 0,
    so counting them would tally what everyone else said and call it your friendship.
  · One person is several rows: SMS and iMessage, +1 and bare, a phone and an Apple
    ID. They merge on a normalized key, and the leftovers are reported, not hidden.
  · WhatsApp splits a contact's phone number across two columns depending on whether
    the account has migrated to a LID. `wa_phone` reads whichever one holds it.

Names come from Contacts, which resolves ~98% of the top of the list, and from
WhatsApp's own partner names. Whatever neither resolves stays a phone number rather
than becoming a guess.

Usage:
    ./extract_contacts.py                              # last 12 months of iMessage
    ./extract_contacts.py --sources imessage,whatsapp  # both, merged
    ./extract_contacts.py --months 6 --top 100
    ./extract_contacts.py --help
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from _dates import months_ago
from _db import check_access, connect

CHAT_DB = Path.home() / "Library/Messages/chat.db"
WHATSAPP_DB = (Path.home() / "Library/Group Containers"
               / "group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")
ADDRESS_BOOK = Path.home() / "Library/Application Support/AddressBook/Sources"

SOURCES = ("imessage", "whatsapp")

# Both databases store time against 2001-01-01: chat.db in nanoseconds, WhatsApp in
# seconds. Every row in a modern chat.db is nanoseconds; the seconds branch below is
# for databases old enough to predate the change, and is untested here because no
# local row exercises it.
APPLE_EPOCH = 978307200  # int(dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc).timestamp())

SERVICES = {"imessage": "iMessage", "sms": "SMS", "rcs": "RCS", "whatsapp": "WhatsApp"}

# Only the WhatsApp message types worth naming. Everything else falls back to
# "[media]" — a wrong label is worse than a vague one.
WA_MEDIA = {1: "[image]", 2: "[video]", 3: "[audio]", 5: "[location]", 8: "[document]"}


# ── opening the databases ────────────────────────────────────────────────────

# ── message bodies ───────────────────────────────────────────────────────────

def decode_body(blob: bytes | None) -> str | None:
    """
    Pull the text out of an `attributedBody` blob.

    These are NSArchiver "streamtyped" archives, not property lists — plistlib parses
    none of them. The text is a length-prefixed UTF-8 byte string introduced by 0x2B
    after the NSString class name, with 0x81/0x82/0x83 marking 2-, 3- and 4-byte
    lengths. Checked against the 11,789 rows that have both a `text` and a blob: it
    reproduces `text` exactly on all of them.
    """
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i == -1:
        return None
    j = blob.find(b"+", i)
    if j == -1 or j + 1 >= len(blob):
        return None
    p = j + 1
    n = blob[p]
    p += 1
    if n == 0x81:
        n = int.from_bytes(blob[p:p + 2], "little"); p += 2
    elif n == 0x82:
        n = int.from_bytes(blob[p:p + 3], "little"); p += 3
    elif n == 0x83:
        n = int.from_bytes(blob[p:p + 4], "little"); p += 4
    elif n >= 0x80:
        return None
    raw = blob[p:p + n]
    if len(raw) != n:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def imessage_body(row: sqlite3.Row) -> tuple[str, str]:
    """(text, where it came from). Never blank: a card with no words still says why."""
    if (text := (row["text"] or "").strip()):
        return text, "text"
    if (text := (decode_body(row["attributedBody"]) or "").strip()):
        return text, "attributedbody"
    if row["cache_has_attachments"]:
        return "[attachment]", "placeholder"
    if row["balloon_bundle_id"]:
        return "[app message]", "placeholder"
    return "[no decodable text]", "placeholder"


# ZPUSHNAME holds a name on some rows and a base64 protobuf on others — about half
# and half on the group messages here. Anything that is a long unbroken run of
# base64 characters is the latter. Names in this database are short and often a
# single word, so length alone would not separate them.
WA_JUNK_NAME = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def wa_sender(value: str | None) -> str:
    """The sender's own name, or "" when WhatsApp only gave us an opaque token."""
    name = (value or "").strip()
    return "" if not name or WA_JUNK_NAME.fullmatch(name) else name


def whatsapp_body(row: sqlite3.Row) -> tuple[str, str]:
    """WhatsApp keeps media captions on the media item, not the message."""
    if (text := (row["text"] or "").strip()):
        return text, "text"
    if (text := (row["media_title"] or "").strip()):
        return text, "media_title"
    return WA_MEDIA.get(row["mtype"], "[media]"), "placeholder"


# ── identity ─────────────────────────────────────────────────────────────────

def person_key(handle: str) -> str:
    """
    Collapse the rows that are the same person.

    This merges SMS against iMessage and iMessage against WhatsApp, which are the
    splits that actually happen. It does not merge a phone number against an Apple ID
    email — nothing in chat.db links them. Contacts does, and `--names` finishes the
    job by resolving both to one name.
    """
    handle = handle.strip()
    if "@" in handle:
        return handle.lower()
    digits = re.sub(r"[^\d]", "", handle)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) >= 11:
        return "+" + digits
    return handle.lower()  # shortcodes, and anything else that isn't a number


def wa_phone(*values: str | None) -> str | None:
    """
    The phone number behind a WhatsApp session.

    A session is keyed by either a phone JID (`…@s.whatsapp.net`) or, since the LID
    migration, an opaque `…@lid`. The two columns hold complementary halves of that:
    whichever one carries the phone JID is the one to read, and it is not reliably
    the same column twice. Selecting on the suffix rather than on digit length
    matters — a LID is 14-15 digits and would pass for a phone number.
    """
    for value in values:
        if value and value.endswith("@s.whatsapp.net"):
            digits = re.sub(r"\D", "", value.split("@")[0])
            if 7 <= len(digits) <= 15:
                return "+" + digits
    return None


def address_book() -> dict[str, str]:
    """
    Map every phone number and email in Contacts to a name.

    Contacts is one database per account under Sources/ — the top-level file is a
    stub. Accounts overlap heavily (iCloud and Google holding the same people), so
    first writer wins and later duplicates are ignored; they agree on the name in
    the cases that matter, and disagreeing on formatting is not worth a tiebreak.
    """
    book: dict[str, str] = {}
    for db in sorted(ADDRESS_BOOK.glob("*/AddressBook-v22.abcddb")):
        try:
            con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        except sqlite3.OperationalError:
            continue
        for table, column in (("ZABCDPHONENUMBER", "ZFULLNUMBER"),
                              ("ZABCDEMAILADDRESS", "ZADDRESS")):
            try:
                rows = con.execute(
                    f"SELECT v.{column}, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION "
                    f"FROM {table} v JOIN ZABCDRECORD r ON r.Z_PK = v.ZOWNER"
                ).fetchall()
            except sqlite3.Error:
                continue  # a schema this old or this new isn't worth failing the run over
            for value, first, last, org in rows:
                if not value:
                    continue
                name = " ".join(p for p in (first, last) if p).strip() or (org or "").strip()
                if name:
                    book.setdefault(person_key(str(value)), name)
        con.close()
    return book


# ── the window ───────────────────────────────────────────────────────────────

def month_span(start: dt.date, end: dt.date) -> list[str]:
    """Every month in the window, so an empty month is a gap in the bars and not a missing bar."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ── queries ──────────────────────────────────────────────────────────────────

# Seconds since the Unix epoch, whichever unit the row is stored in. Interpolated
# rather than bound because it is a constant of the file format, and because mixing
# named and positional placeholders in one statement is an error.
UNIXTS = (f"((CASE WHEN m.date > 1000000000000 THEN m.date / 1000000000 ELSE m.date END)"
          f" + {APPLE_EPOCH})")

# The window is always bound as an integer, never written as strftime('%s', …):
# that returns TEXT, every number compares below every string, and the whole
# WHERE clause then matches nothing at all rather than failing.
#
# One-to-one threads only (chat.style 45; 43 is a group). associated_message_type
# and item_type strip tapbacks and the "so-and-so joined" events, which are 18% of
# the raw window and are not messages anybody sent.
WINDOW_SQL = f"""
SELECT h.id AS handle,
       m.is_from_me AS is_from_me,
       lower(COALESCE(m.service, '')) AS service,
       {UNIXTS} AS unixts
FROM message m
JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
JOIN chat c                ON c.ROWID        = cmj.chat_id
JOIN handle h              ON h.ROWID        = m.handle_id
WHERE c.style = 45
  AND m.associated_message_type = 0
  AND m.item_type = 0
  AND m.handle_id > 0
  AND {UNIXTS} >= ?
"""

# The last messages of one thread. The %s is filled with one ? per handle rowid.
RECENT_SQL = f"""
SELECT m.is_from_me, m.text, m.attributedBody, m.cache_has_attachments,
       m.balloon_bundle_id, lower(COALESCE(m.service, '')) AS service,
       {UNIXTS} AS unixts
FROM message m
JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
JOIN chat c                ON c.ROWID        = cmj.chat_id
WHERE c.style = 45
  AND m.associated_message_type = 0
  AND m.item_type = 0
  AND m.handle_id IN (%s)
  AND {UNIXTS} >= ?
ORDER BY m.date DESC, m.ROWID DESC
LIMIT ?
"""

# ZSESSIONTYPE 0 is one-to-one, 1 is a group. Group size counts only the other
# people — you are not a row in ZWAGROUPMEMBER — and counts only active members, so
# a thread that used to be large but is now three people reads as three people.
WA_SESSION_SQL = """
SELECT s.Z_PK AS session, s.ZSESSIONTYPE AS kind, s.ZCONTACTJID AS jid,
       s.ZCONTACTIDENTIFIER AS ident, s.ZPARTNERNAME AS partner,
       (SELECT COUNT(*) FROM ZWAGROUPMEMBER g
        WHERE g.ZCHATSESSION = s.Z_PK AND g.ZISACTIVE = 1) AS members
FROM ZWACHATSESSION s
WHERE s.ZSESSIONTYPE IN (0, 1)
"""

# Bound against ZMESSAGEDATE, which is in Core Data seconds, so the window is
# shifted by the epoch before binding.
WA_WINDOW_SQL = f"""
SELECT m.ZCHATSESSION AS session, m.ZISFROMME AS is_from_me,
       m.ZMESSAGEDATE + {APPLE_EPOCH} AS unixts
FROM ZWAMESSAGE m
WHERE m.ZCHATSESSION IN (%s)
  AND m.ZMESSAGEDATE >= ?
"""

# ZPUSHNAME is the only usable name for a group sender — see wa_sender. The obvious
# alternative, joining ZWAGROUPMEMBER, is a dead end: ZCONTACTNAME is NULL in all
# 15,473 rows, ZFIRSTNAME covers 1 of 964 received group messages, and ZMEMBERJID is
# always an opaque @lid that resolves to nothing.
WA_RECENT_SQL = f"""
SELECT m.ZISFROMME AS is_from_me, m.ZTEXT AS text, m.ZMESSAGETYPE AS mtype,
       mi.ZTITLE AS media_title, m.ZMESSAGEDATE + {APPLE_EPOCH} AS unixts,
       m.ZPUSHNAME AS pushname
FROM ZWAMESSAGE m
LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
WHERE m.ZCHATSESSION IN (%s)
  AND m.ZMESSAGEDATE >= ?
ORDER BY m.ZMESSAGEDATE DESC, m.Z_PK DESC
LIMIT ?
"""


# ── accumulating people ──────────────────────────────────────────────────────

def blank(span: list[str]) -> dict:
    return {"handles": set(), "sent": 0, "received": 0, "services": {},
            "months": dict.fromkeys(span, 0), "first": None, "last": None,
            "sources": set(), "names": {}, "recent": [],
            "kind": "person", "members": 0}


def tally(people: dict, key: str, span: list[str], *, handle: str, source: str,
          service: str, is_from_me: int, unixts: float) -> None:
    """Fold one message into a person, from whichever database it came out of."""
    p = people.setdefault(key, blank(span))
    p["handles"].add(handle)
    p["sources"].add(source)
    p["sent" if is_from_me else "received"] += 1
    p["services"][service] = p["services"].get(service, 0) + 1
    stamp = dt.datetime.fromtimestamp(unixts)
    month = stamp.strftime("%Y-%m")
    if month in p["months"]:
        p["months"][month] += 1
    date = stamp.date().isoformat()
    p["first"] = min(p["first"] or date, date)
    p["last"] = max(p["last"] or date, date)


def read_imessage(con: sqlite3.Connection, since: int, span: list[str],
                  people: dict) -> tuple[dict, dict]:
    """Fold chat.db into `people`; return its handle-rowid index and its stats."""
    rows = con.execute(WINDOW_SQL, (since,)).fetchall()
    for row in rows:
        service = row["service"] if row["service"] in SERVICES else "imessage"
        tally(people, person_key(row["handle"]), span, handle=row["handle"],
              source="imessage", service=service,
              is_from_me=row["is_from_me"], unixts=row["unixts"])

    handles = {row["ROWID"]: row["id"] for row in con.execute("SELECT ROWID, id FROM handle")}
    index: dict[str, list[int]] = {}
    for rowid, handle in handles.items():
        index.setdefault(person_key(handle), []).append(rowid)
    return index, {"messages": len(rows), "handle_rows": len(handles)}


def read_whatsapp(con: sqlite3.Connection, since: int, span: list[str], people: dict,
                  group_size: int) -> tuple[dict, dict]:
    """
    Fold WhatsApp into `people`; return its session index and its stats.

    Groups are included only when they are small — `group_size` counts everyone in
    the thread including you. A big group is not a contact: its volume says how
    chatty a crowd is, not how much you talk to anyone in it.
    """
    # A one-to-one session that carries both a LID and a phone JID is the only place
    # this database ever states that the two identify the same person. Harvesting
    # those pairs is what lets a group's members be named at all — inside a group
    # everyone is a bare LID.
    lidmap: dict[str, str] = {}
    for row in con.execute("SELECT ZCONTACTJID AS j, ZCONTACTIDENTIFIER AS i "
                           "FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 0"):
        values = [v for v in (row["j"], row["i"]) if v]
        lids = [v for v in values if v.endswith("@lid")]
        phones = [v for v in values if v.endswith("@s.whatsapp.net")]
        if lids and phones:
            lidmap[lids[0]] = "+" + re.sub(r"\D", "", phones[0].split("@")[0])

    sessions, groups, unresolved = {}, set(), set()
    for row in con.execute(WA_SESSION_SQL):
        if row["kind"] == 1:
            people_in_thread = row["members"] + 1  # ZWAGROUPMEMBER never lists you
            if not group_size or people_in_thread >= group_size:
                continue
            key = f"wa-group:{row['jid'] or row['session']}"
            groups.add(key)
            sessions[row["session"]] = (key, row["partner"], people_in_thread)
            continue
        phone = wa_phone(row["jid"], row["ident"])
        # A session with no phone JID at all is still a real thread; key it on the
        # LID so the person appears, rather than dropping them for a schema reason.
        key = person_key(phone) if phone else f"wa:{row['jid'] or row['session']}"
        if not phone:
            unresolved.add(key)
        sessions[row["session"]] = (key, row["partner"], 0)

    if not sessions:
        return {}, {"messages": 0, "groups": 0, "unresolved": 0}

    pks = list(sessions)
    sql = WA_WINDOW_SQL % ",".join("?" * len(pks))
    rows = con.execute(sql, (*pks, since - APPLE_EPOCH)).fetchall()

    index: dict[str, list[int]] = {}
    for row in rows:
        key, partner, members = sessions[row["session"]]
        is_group = key in groups
        tally(people, key, span, handle="" if is_group else key,
              source="whatsapp", service="whatsapp",
              is_from_me=row["is_from_me"], unixts=row["unixts"])
        p = people[key]
        if partner:
            p["names"]["whatsapp"] = partner
        if is_group and p["kind"] != "group":
            p["kind"], p["members"] = "group", members
            p["member_jids"] = [r["ZMEMBERJID"] for r in con.execute(
                "SELECT ZMEMBERJID FROM ZWAGROUPMEMBER "
                "WHERE ZCHATSESSION = ? AND ZISACTIVE = 1", (row["session"],))]
        if row["session"] not in index.setdefault(key, []):
            index[key].append(row["session"])

    seen = {k for k in index if k in groups}
    return index, {"messages": len(rows), "groups": len(seen),
                   "unresolved": len(unresolved), "lidmap": lidmap}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--months", type=int, default=12, help="Window length. Default 12.")
    ap.add_argument("--top", type=int, default=0, metavar="N",
                    help="Keep only the N most-messaged contacts. Default 0 — keep "
                         "everyone who clears --min-messages.")
    ap.add_argument("--min-messages", type=int, default=10,
                    help="Drop contacts below N messages in the window. Default 10.")
    ap.add_argument("--recent", type=int, default=2,
                    help="Messages to keep per contact. Default 2; 0 for counts only.")
    ap.add_argument("--sources", default=",".join(SOURCES),
                    help=f"Comma-separated subset of {','.join(SOURCES)}. "
                         f"Default all of them; a source that isn't installed is skipped.")
    ap.add_argument("--group-size", type=int, default=5, metavar="N",
                    help="Include WhatsApp groups with fewer than N people, counting "
                         "you. Default 5; 0 leaves every group out.")
    ap.add_argument("--db", type=Path, default=CHAT_DB, help=f"Default {CHAT_DB}")
    ap.add_argument("--whatsapp-db", type=Path, default=WHATSAPP_DB,
                    help=f"Default {WHATSAPP_DB}")
    ap.add_argument("--no-names", action="store_true",
                    help="Skip Contacts; leave every contact as a phone number or email.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Default messages/contacts-<date>-<N>mo.json")
    args = ap.parse_args()

    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
    if bad := [s for s in wanted if s not in SOURCES]:
        sys.exit(f"Unknown source(s): {', '.join(bad)}. Choose from {', '.join(SOURCES)}.")

    start = months_ago(args.months)
    today = dt.date.today()
    since = int(dt.datetime.combine(start, dt.time.min).timestamp())
    span = month_span(start, today)

    people: dict[str, dict] = {}
    stats: dict[str, dict] = {}
    cons: dict[str, sqlite3.Connection] = {}
    index: dict[str, dict] = {}
    missing: list[str] = []

    if "imessage" in wanted:
        check_access(args.db, "Messages")
        print(f"… reading {args.db.name}", file=sys.stderr, flush=True)
        cons["imessage"] = connect(args.db)
        index["imessage"], stats["imessage"] = read_imessage(
            cons["imessage"], since, span, people)

    # WhatsApp is optional in a way Messages is not — it may simply not be installed.
    # Say so and carry on rather than failing a run that iMessage could have answered.
    if "whatsapp" in wanted and not args.whatsapp_db.exists():
        print(f"… no WhatsApp database at {args.whatsapp_db} — skipping that source",
              file=sys.stderr, flush=True)
        wanted.remove("whatsapp")
        missing.append("whatsapp")

    if "whatsapp" in wanted:
        check_access(args.whatsapp_db, "WhatsApp")
        print(f"… reading {args.whatsapp_db.name}", file=sys.stderr, flush=True)
        cons["whatsapp"] = connect(args.whatsapp_db)
        index["whatsapp"], stats["whatsapp"] = read_whatsapp(
            cons["whatsapp"], since, span, people, args.group_size)

    # A thread you have never said anything in is a bank, a 2FA robot, a shipping
    # notification, or a group you only read. None of that is being in contact with
    # someone, which is what this list claims to measure.
    ranked = sorted(
        ((k, p) for k, p in people.items()
         if p["sent"] > 0 and p["sent"] + p["received"] >= args.min_messages),
        key=lambda kp: -(kp[1]["sent"] + kp[1]["received"]),
    )
    robots = sum(1 for p in people.values() if p["sent"] == 0)
    kept = ranked[:args.top] if args.top else ranked

    book = {} if args.no_names else address_book()
    print(f"… {len(people):,} people · {len(book):,} contact records",
          file=sys.stderr, flush=True)

    contacts, placeholders, named = [], 0, 0
    for rank, (key, p) in enumerate(kept):
        recent = []
        if args.recent:
            if ids := index.get("imessage", {}).get(key):
                sql = RECENT_SQL % ",".join("?" * len(ids))
                for row in cons["imessage"].execute(sql, (*ids, since, args.recent)):
                    text, origin = imessage_body(row)
                    service = row["service"] if row["service"] in SERVICES else "imessage"
                    recent.append({"ts": row["unixts"], "service": service,
                                   "direction": "sent" if row["is_from_me"] else "received",
                                   "text": text, "text_source": origin, "sender": ""})
            if ids := index.get("whatsapp", {}).get(key):
                sql = WA_RECENT_SQL % ",".join("?" * len(ids))
                for row in cons["whatsapp"].execute(
                        sql, (*ids, since - APPLE_EPOCH, args.recent)):
                    text, origin = whatsapp_body(row)
                    recent.append({"ts": row["unixts"], "service": "whatsapp",
                                   "direction": "sent" if row["is_from_me"] else "received",
                                   "text": text, "text_source": origin,
                                   # Only a received group message needs to say who
                                   # spoke: in a one-to-one thread the answer is the
                                   # title, and everything you sent is you.
                                   "sender": wa_sender(row["pushname"])
                                   if p["kind"] == "group" and not row["is_from_me"] else ""})
            # Each source contributed its own newest few; the answer is the newest
            # few across all of them.
            recent.sort(key=lambda m: -m["ts"])
            recent = recent[:args.recent]
            for m in recent:
                stamp = dt.datetime.fromtimestamp(m.pop("ts"))
                m["date"] = stamp.date().isoformat()
                m["time"] = stamp.strftime("%H:%M")
                placeholders += m["text_source"] == "placeholder"

        # Contacts is the better name when it has one — it is the name you chose.
        # WhatsApp's partner name is what the other person calls themselves, which
        # is a fine second, and both beat a phone number.
        # A group is named by its subject and nothing else; only a person has a
        # phone number to look up.
        if p["kind"] == "group":
            name = p["names"].get("whatsapp")
            source = "whatsapp" if name else None
            name = name or "[unnamed group]"
            # Who is in it, for the ones Contacts can put a name to. Inside a group
            # a member is a bare LID, so this resolves partially and says so by
            # simply listing fewer names than the group has people.
            lidmap = stats.get("whatsapp", {}).get("lidmap", {})
            members = []
            for jid in p.get("member_jids", []):
                phone = (("+" + re.sub(r"\D", "", jid.split("@")[0]))
                         if (jid or "").endswith("@s.whatsapp.net") else lidmap.get(jid))
                if phone and (who := book.get(person_key(phone))):
                    members.append(who)
            p["member_names"] = sorted(set(members))
        else:
            name = next((book[person_key(h)] for h in sorted(p["handles"])
                         if person_key(h) in book), None)
            source = "addressbook" if name else None
            if not name and (name := p["names"].get("whatsapp")):
                source = "whatsapp"
        named += source is not None
        contacts.append({
            "id": f"c{rank}",  # opaque on purpose: the URL hash must not carry a phone number
            # `id` is a rank and moves between runs; `key` is the identity itself and
            # is what a later snapshot matches on. Without it a diff can only guess.
            "key": key,
            "name": name or (sorted(p["handles"])[0] if p["handles"] else key),
            "name_source": source or "handle",
            "kind": p["kind"],
            "members": p["members"],
            "member_names": p.get("member_names", []),
            "handles": sorted(h for h in p["handles"] if h),
            "count": p["sent"] + p["received"],
            "sent": p["sent"],
            "received": p["received"],
            "services": sorted(p["services"], key=lambda s: -p["services"][s]),
            "service_counts": p["services"],
            "sources": sorted(p["sources"]),
            "first": p["first"],
            "last": p["last"],
            "months": [[m, n] for m, n in p["months"].items()],
            "recent": recent,
        })
    for con in cons.values():
        con.close()

    both = sum(1 for c in contacts if len(c["sources"]) > 1)
    payload = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "months": args.months,
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "merge": {"rule": "e164+lower",
                  "handle_rows": stats.get("imessage", {}).get("handle_rows", 0),
                  "person_keys": len(people),
                  "cross_source": both,
                  "names_from": "addressbook" if book else "none"},
        "sources": [
            {"name": name,
             "db": str(args.db if name == "imessage" else args.whatsapp_db),
             "messages": stats.get(name, {}).get("messages", 0),
             "skipped": ("database not found" if name in missing
                         else "" if name in wanted else "not requested (--sources)")}
            for name in SOURCES
        ],
        "contacts": contacts,
    }

    stamp = f"{today.isoformat()}-{args.months}mo"
    out = args.out or Path("messages") / f"contacts-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    # This file is other people's messages. Refuse to put it anywhere git will take it.
    if subprocess.run(["git", "check-ignore", "-q", str(out)]).returncode != 0:
        sys.exit(f"Refusing to write {out}: it is not gitignored, and it holds real\n"
                 f"  message text. Add 'messages/' to .gitignore, then re-run.")
    out.write_text(json.dumps(payload, ensure_ascii=False))

    total = sum(s.get("messages", 0) for s in stats.values())
    print(f"\n{len(contacts)} contacts · {total:,} messages · {start} → {today}")
    for name in wanted:
        print(f"(source)    {name:9s} {stats[name]['messages']:>7,} messages")
    if len(wanted) > 1:
        print(f"(merged)    {both} contact(s) appear in more than one app")
    if groups := sum(1 for c in contacts if c["kind"] == "group"):
        print(f"(groups)    {groups} of the {len(contacts)} rows are WhatsApp groups "
              f"under {args.group_size} people")
    if unresolved := stats.get("whatsapp", {}).get("unresolved", 0):
        print(f"(whatsapp)  {unresolved} session(s) are LID-only — no phone number to "
              f"merge on, so they stay separate")
    if not args.no_names:
        print(f"(names)     {named}/{len(contacts)} resolved; the rest stay as handles")
    if robots:
        print(f"(dropped)   {robots} thread(s) you never wrote in — robots, and "
              f"groups you only read")
    if placeholders:
        print(f"(bodies)    {placeholders} message(s) had no text; shown as [image] and such")
    print(f"\n  wrote  {out}\n  render  ./tools/contacts_html.py {out}")


if __name__ == "__main__":
    main()
