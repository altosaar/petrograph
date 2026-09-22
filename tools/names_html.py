#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
names_html — a browsable, self-contained page of every name and where it came from.

Takes the JSON written by extract_names.py and emits one HTML file: click a name
and you get the chunks it was found in, the note each came from, its date, and the
provenance layer that produced it (real diff vs. reconstruction). Names are shown
normalized — lowercase, unpunctuated — so one person reads as one entry.

No network, no build step, no dependencies: the data is inlined and the page opens
from disk. The look comes from _page.py, shared with contacts_html.py — hard
borders and flat blocks make the provenance badges legible at a glance, which is
the whole point of the corpus.

Usage:
    ./names_html.py corpus/names-2026-01-15-12mo.json
    ./names_html.py            # newest corpus/names-*.json
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from _page import render
from _term import link

# Entity kinds and provenance layers, drawn from the shared palette.
EXTRA_STYLE = """  .badge.person { background: var(--cyan); }
  .badge.organization { background: var(--orange); }
  .badge.place { background: var(--lime); }
  .badge.git, .badge.recovery { background: var(--lime); }
  .badge.entry { background: var(--cyan); }
  .badge.file { background: var(--paper); }"""

SCRIPT = """
const { chunks, entities } = DATA;

const KINDS = [...new Set(entities.map(e => e.kind))].sort();
const active = new Set(KINDS);
let selected = null;

KINDS.forEach(k => {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = k;
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
  let rows = entities.filter(e => active.has(e.kind) && (!q || e.name.includes(q)));
  const mode = sortEl.value;
  if (mode === "alpha") rows = [...rows].sort((a, b) => a.name.localeCompare(b.name));
  else if (mode === "recent") rows = [...rows].sort((a, b) => b.last.localeCompare(a.last));
  else rows = [...rows].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return rows;
}

function renderList() {
  listEl.textContent = "";
  const rows = visible();
  if (!rows.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No names match.";
    listEl.appendChild(d);
    return;
  }
  for (const e of rows) {
    const b = document.createElement("button");
    b.className = "name-row";
    b.setAttribute("aria-current", String(selected === e.name));
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = e.name;
    const c = document.createElement("span");
    c.className = "count";
    c.textContent = e.count;
    b.append(n, c);
    b.onclick = () => { select(e.name); };
    listEl.appendChild(b);
  }
}

// Build the highlighted chunk as DOM nodes — the corpus is private note text and
// is never interpolated as HTML.
function highlight(text, needle) {
  const frag = document.createDocumentFragment();
  const low = text.toLowerCase(), n = needle.toLowerCase();
  let i = 0;
  while (n && i < text.length) {
    const hit = low.indexOf(n, i);
    if (hit === -1) break;
    frag.append(document.createTextNode(text.slice(i, hit)));
    const m = document.createElement("mark");
    m.textContent = text.slice(hit, hit + n.length);
    frag.append(m);
    i = hit + n.length;
  }
  frag.append(document.createTextNode(text.slice(i)));
  return frag;
}

function months(mentions) {
  const counts = new Map();
  for (const m of mentions) {
    const key = chunks[m.chunk].date.slice(0, 7);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function renderDetail(e) {
  detailEl.textContent = "";
  const h = document.createElement("h2");
  h.className = "title";
  h.textContent = e.name;
  detailEl.append(h);

  const facts = document.createElement("div");
  facts.className = "facts";
  facts.append(
    badge(e.kind, e.kind),
    badge(e.count + " mention" + (e.count === 1 ? "" : "s")),
    badge(e.first === e.last ? e.first : e.first + " → " + e.last),
    badge("as written: " + e.display),
  );
  const layers = {};
  for (const m of e.mentions) {
    const l = chunks[m.chunk].layer;
    layers[l] = (layers[l] || 0) + 1;
  }
  for (const [l, n] of Object.entries(layers)) facts.append(badge(l + " ×" + n, l));
  detailEl.append(facts);

  const buckets = months(e.mentions);
  if (buckets.length > 1) {
    const max = Math.max(...buckets.map(b => b[1]));
    const hist = document.createElement("div");
    hist.className = "hist";
    for (const [month, n] of buckets) {
      const bar = document.createElement("div");
      // Pixels, not percent: a flex child's percentage height is unreliable here,
      // and a 2px floor keeps an empty-ish month visible rather than invisible.
      bar.style.height = Math.max(2, Math.round(56 * n / max)) + "px";
      bar.title = month + ": " + n + " mention" + (n === 1 ? "" : "s");
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

  for (const m of e.mentions) {
    const c = chunks[m.chunk];
    const wrap = document.createElement("div");
    wrap.className = "mention";
    const head = document.createElement("div");
    head.className = "head";
    const p = document.createElement("span");
    p.className = "path";
    p.textContent = c.path;
    head.append(p, badge(c.date), badge(c.layer, c.layer));
    if (c.provenance) head.append(badge(c.provenance));
    const pre = document.createElement("pre");
    pre.append(highlight(c.text, m.surface));
    wrap.append(head, pre);
    detailEl.append(wrap);
    // A chunk is ~4000 characters and the name can be anywhere in it; open each
    // mention scrolled to its first hit, with a few lines of lead-in above.
    const hit = pre.querySelector("mark");
    if (hit) pre.scrollTop = Math.max(0, hit.offsetTop - pre.offsetTop - 60);
  }
}

// The URL hash carries the selected name, so a person is a linkable address and
// reloading keeps your place.
function select(name) {
  const e = entities.find(x => x.name === name);
  if (!e) return;
  selected = name;
  if (decodeURIComponent(location.hash.slice(1)) !== name) {
    history.replaceState(null, "", "#" + encodeURIComponent(name));
  }
  renderList();
  renderDetail(e);
  document.querySelector('.name-row[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
}

qEl.addEventListener("input", renderList);
sortEl.addEventListener("change", renderList);
addEventListener("hashchange", () => select(decodeURIComponent(location.hash.slice(1))));
renderList();
if (location.hash) select(decodeURIComponent(location.hash.slice(1)));
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("names", nargs="?", type=Path,
                    help="JSON from extract_names.py. Default: newest corpus/names-*.json")
    ap.add_argument("--out", type=Path, default=None, help="Default: alongside the input, .html")
    ap.add_argument("--min-mentions", type=int, default=1,
                    help="Drop names mentioned fewer than N times. Default 1 (keep all).")
    args = ap.parse_args()

    src = args.names or max(Path("corpus").glob("names-*.json"), default=None,
                            key=lambda p: p.stat().st_mtime)
    if not src or not src.exists():
        sys.exit("No names JSON found — run tools/extract_names.py first.")

    data = json.loads(src.read_text())
    entities = [e for e in data["entities"] if e["count"] >= args.min_mentions]
    keep = sorted({m["chunk"] for e in entities for m in e["mentions"]})
    remap = {old: new for new, old in enumerate(keep)}
    for e in entities:
        for m in e["mentions"]:
            m["chunk"] = remap[m["chunk"]]
    payload = {"chunks": [data["chunks"][i] for i in keep], "entities": entities}

    mentions = sum(e["count"] for e in entities)
    meta = (f"{mentions:,} mentions · {len(payload['chunks']):,} chunks · "
            f"{data['model']} · corpus {Path(data['corpus']).name} · generated {data['generated']}")

    html = render(
        title="names — vault corpus",
        heading="Names",
        meta=meta,
        count=f"{len(entities):,} names",
        search="search names  ( / )",
        sorts=[("count", "most mentions"), ("recent", "most recent"), ("alpha", "a &rarr; z")],
        detail_heading="Mentions",
        empty=("Pick a name to see the chunks it appears in,<br>"
               "with the note, date, and provenance of each."),
        extra_style=EXTRA_STYLE,
        script=SCRIPT,
        data=payload,
    )

    out = args.out or src.with_suffix(".html")
    out.write_text(html)
    print(f"{len(entities):,} names · {mentions:,} mentions · {out.stat().st_size / 1e6:.1f} MB")
    print(f"\n  open  {link(out)}")


if __name__ == "__main__":
    main()
