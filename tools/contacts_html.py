#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
contacts_html — a browsable page of the people you actually message.

Takes the JSON written by extract_contacts.py and emits one HTML file: contacts
ranked by how much you talk, and clicking one gives you the month-by-month shape of
the thread, the sent/received split, the services it ran over, and the last two
messages as they were written.

No network, no build step, no dependencies: the data is inlined and the page opens
from disk. The look comes from _page.py, shared with names_html.py — the two pages
are deliberately the same object, one pointed at a vault and one at a message
database.

Every string from the databases — message bodies, contact names, raw handles — is
put on the page as a text node, never as markup. A message is arbitrary text that
somebody else chose, which is the definition of untrusted input.

Usage:
    ./contacts_html.py messages/contacts-2026-01-15-12mo.json
    ./contacts_html.py            # newest messages/contacts-*.json
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from _page import render
from _term import link

# Service and role colours, drawn from the shared palette so the two pages agree.
EXTRA_STYLE = """  .badge.imessage { background: var(--cyan); }
  .badge.sms { background: var(--lime); }
  .badge.rcs { background: var(--lime); }
  .badge.whatsapp { background: var(--orange); }
  .badge.sent { background: var(--pink); }
  .badge.received { background: var(--card); }
  .badge.handle { background: var(--paper); }
  .badge.group { background: var(--yellow); }"""

SCRIPT = """
const { contacts } = DATA;

// A fixed vocabulary, so a value out of the database is never used as a class name.
const SERVICE = { imessage: "iMessage", sms: "SMS", rcs: "RCS", whatsapp: "WhatsApp" };
const label = s => SERVICE[s] || "other";
const cls = s => SERVICE[s] ? s : "";

const KINDS = [...new Set(contacts.flatMap(c => c.services))].sort();
const active = new Set(KINDS);
let selected = null;

KINDS.forEach(k => {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = label(k);
  b.setAttribute("aria-pressed", "true");
  b.onclick = () => {
    active.has(k) ? active.delete(k) : active.add(k);
    b.setAttribute("aria-pressed", String(active.has(k)));
    renderList();
  };
  kindsEl.appendChild(b);
});

function visible() {
  const q = qEl.value.trim().toLowerCase();
  // A contact can span services, so it shows if any of its services is on.
  let rows = contacts.filter(c => c.services.some(s => active.has(s))
    && (!q || c.name.toLowerCase().includes(q) || c.handles.some(h => h.includes(q))));
  const mode = sortEl.value;
  if (mode === "alpha") rows = [...rows].sort((a, b) => a.name.localeCompare(b.name));
  else if (mode === "recent") rows = [...rows].sort((a, b) => b.last.localeCompare(a.last));
  else rows = [...rows].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return rows;
}

function renderList() {
  listEl.textContent = "";
  const rows = visible();
  // The list scrolls, so say outright how many rows are in it — otherwise a long
  // list that runs past the panel is indistinguishable from a truncated one.
  tallyEl.textContent = rows.length === contacts.length
    ? contacts.length.toLocaleString() + " contacts"
    : rows.length.toLocaleString() + " of " + contacts.length.toLocaleString() + " contacts";
  if (!rows.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No contacts match.";
    listEl.appendChild(d);
    return;
  }
  for (const c of rows) {
    const b = document.createElement("button");
    b.className = "name-row";
    b.setAttribute("aria-current", String(selected === c.id));
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = c.name;
    const k = document.createElement("span");
    k.className = "count";
    k.textContent = c.count.toLocaleString();
    // A group named "Family" is indistinguishable from a person in a bare list.
    if (c.kind === "group") {
      const g = document.createElement("span");
      g.className = "count";
      g.textContent = "group of " + c.members;
      b.append(n, g, k);
    } else {
      b.append(n, k);
    }
    b.onclick = () => { select(c.id); };
    listEl.appendChild(b);
  }
}

function renderDetail(c) {
  detailEl.textContent = "";
  const h = document.createElement("h2");
  h.className = "title";
  h.textContent = c.name;
  detailEl.append(h);

  const facts = document.createElement("div");
  facts.className = "facts";
  if (c.kind === "group") facts.append(badge("group of " + c.members, "group"));
  facts.append(
    badge(c.count.toLocaleString() + " message" + (c.count === 1 ? "" : "s")),
    badge(c.sent.toLocaleString() + " sent", "sent"),
    badge(c.received.toLocaleString() + " received", "received"),
    badge(c.first === c.last ? c.first : c.first + " → " + c.last),
  );
  for (const s of c.services) {
    facts.append(badge(label(s) + " ×" + (c.service_counts[s] || 0).toLocaleString(), cls(s)));
  }
  // Inside a group everyone is an opaque id, so only some members can be named.
  // Listing the ones we know beats listing none, and the count above says how many
  // people are actually in the thread.
  for (const who of c.member_names) facts.append(badge(who, "group"));
  // Show the raw handle only when it isn't already the title, so a resolved contact
  // still displays the number you actually reach them on.
  for (const raw of c.handles) {
    if (raw !== c.name) facts.append(badge(raw, "handle"));
  }
  detailEl.append(facts);

  // The months come pre-filled by the extractor, zeros included — the page only
  // ships two messages per contact, so it cannot derive the shape of the year itself.
  const buckets = c.months;
  if (buckets.length > 1) {
    const max = Math.max(...buckets.map(b => b[1]));
    const hist = document.createElement("div");
    hist.className = "hist";
    for (const [month, n] of buckets) {
      const bar = document.createElement("div");
      // Pixels, not percent: a flex child's percentage height is unreliable here,
      // and a 2px floor keeps an empty-ish month visible rather than invisible.
      bar.style.height = Math.max(2, Math.round(56 * n / (max || 1))) + "px";
      bar.title = month + ": " + n + " message" + (n === 1 ? "" : "s");
      hist.append(bar);
    }
    const axis = document.createElement("div");
    axis.className = "hist-axis";
    const a = document.createElement("span"), b = document.createElement("span");
    a.textContent = buckets[0][0];
    b.textContent = buckets[buckets.length - 1][0];
    axis.append(a, b);
    detailEl.append(hist, axis);
  }

  if (!c.recent.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No message bodies in this file.";
    detailEl.append(d);
    return;
  }

  for (const m of c.recent) {
    const wrap = document.createElement("div");
    wrap.className = "mention";
    const head = document.createElement("div");
    head.className = "head";
    const who = document.createElement("span");
    who.className = "path";
    // In a group, WhatsApp only names about half of the senders — "someone" is
    // honest, where reusing the group's own name would be a quiet lie.
    who.textContent = m.direction === "sent" ? "you"
      : (c.kind === "group" ? (m.sender || "someone") : c.name);
    head.append(who, badge(m.date + " " + m.time), badge(m.direction, m.direction),
                badge(label(m.service), cls(m.service)));
    const pre = document.createElement("pre");
    pre.textContent = m.text;   // a message body is somebody else's text: never markup
    wrap.append(head, pre);
    detailEl.append(wrap);
  }
}

// The hash carries the opaque id, never the name — a phone number in the URL bar
// would end up in browser history and in every screenshot of this page.
function select(id) {
  const c = contacts.find(x => x.id === id);
  if (!c) return;
  selected = id;
  if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + id);
  renderList();
  renderDetail(c);
  document.querySelector('.name-row[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
}

qEl.addEventListener("input", renderList);
sortEl.addEventListener("change", renderList);
addEventListener("hashchange", () => select(location.hash.slice(1)));
renderList();
if (location.hash) select(location.hash.slice(1));
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("contacts", nargs="?", type=Path,
                    help="JSON from extract_contacts.py. Default: newest messages/contacts-*.json")
    ap.add_argument("--out", type=Path, default=None, help="Default: alongside the input, .html")
    ap.add_argument("--top", type=int, default=0,
                    help="Keep only the N most-messaged contacts. Default 0 (keep all).")
    args = ap.parse_args()

    src = args.contacts or max(Path("messages").glob("contacts-*.json"), default=None,
                               key=lambda p: p.stat().st_mtime)
    if not src or not src.exists():
        sys.exit("No contacts JSON found — run tools/extract_contacts.py first.")

    data = json.loads(src.read_text())
    contacts = sorted(data["contacts"], key=lambda c: -c["count"])
    if args.top:
        contacts = contacts[:args.top]

    messages = sum(c["count"] for c in contacts)
    window = data["window"]
    names = sum(c["name_source"] != "handle" for c in contacts)
    groups = sum(c["kind"] == "group" for c in contacts)
    meta = (f"{messages:,} messages · {window['start']} → {window['end']} · "
            f"{names}/{len(contacts)} named · "
            f"{groups} small group{'' if groups == 1 else 's'} · "
            f"generated {data['generated']}")

    html = render(
        title="contacts — message history",
        heading="Contacts",
        meta=meta,
        count=f"{len(contacts):,} contacts",
        search="search contacts  ( / )",
        sorts=[("count", "most messages"), ("recent", "most recent"), ("alpha", "a &rarr; z")],
        detail_heading="Recent",
        empty=("Pick a contact to see the last two messages,<br>"
               "with the month histogram and service badges."),
        extra_style=EXTRA_STYLE,
        script=SCRIPT,
        data={"contacts": contacts},
    )

    out = args.out or src.with_suffix(".html")
    out.write_text(html)
    print(f"{len(contacts):,} contacts · {messages:,} messages · "
          f"{out.stat().st_size / 1e6:.1f} MB")
    print(f"\n  open  {link(out)}")


if __name__ == "__main__":
    main()
