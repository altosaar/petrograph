#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
microlite_waffle — every week's Obsidian hunks as a waffle chart: a square per hunk, or a
square per round number of characters edited.

The weekly review keeps its diff (`microlite.md`) inside the session directory, and the
gitignore says why that matters: File Recovery prunes, so those files are the only surviving
record of how the vault changed. They are also, together, a series — a run of them is a
picture of the vault evolving that no single review contains. This draws that picture.

What counts as a hunk, and what its id is, live in `_hunks.py` — shared with
`hunk_connections.py` and `connection_waffle.py`, which draw arrows between the same squares.

The form is the waffle chart from https://idl.uw.edu/mosaic/examples/athlete-birth-waffle.html:
one column per category, one square per record, squares stacked bottom-up and wrapped at a
fixed width so the column reads as a quantity. Here the category is the week and the record is
a hunk — a single `@@` block, one contiguous stretch of change in one note.

Two views, and a toggle between them, because a hunk is not a unit of work:

    hunks       one square is one hunk, so a week's column counts edits and a typo weighs what a
                rewrite weighs. This is the chart as it was.
    characters  one square is a round number of characters added or removed, so a hunk that
                rewrote a page is a block of squares and the typo is one of them. The number is
                picked off a 1-2-5 ladder — the smallest that keeps the tallest week on screen,
                the way an axis picks its own ticks — and printed under the chart.

A hunk never falls below one square, so the smallest edit is still there to click, and a
whole-content note keeps the single square it has in the other view: there is no diff under it
to measure. Which means the characters view is honest about size and not about totals, and the
two are worth reading against each other.

A hunk's squares are drawn as one piece — each square grows into the gap it shares with another
square of the same hunk, so the hunk tiles a single region and the gap is left to mean a
boundary. Without that, two large edits in the same week are one undifferentiated block of
colour and the only way to find the seam between them is to click around for it. Inside a piece
the seams are still drawn, faintly, because a waffle is a thing you count.

Squares are coloured by what the hunk did, from the shared palette in `_page.py`:

    added    lines only added                 lime
    mixed    added and removed                cyan
    removed  lines only removed               pink, at a third opacity — a week's pruning
                                              should read as lighter than its growth
    full     no diff to draw. The reader emits whole current content instead of a diff for a
             short note (`--full-below`), so there are no `@@` blocks to count; the note gets
             one neutral square rather than vanishing from the week. `--hunks-only` drops these.

The default page draws that itself, in a few hundred `<rect>`s, and inherits the look from
`_page.py` alongside `names_html.py` and `contacts_html.py`: one file, data inlined, no network,
no build step — which is what lets a page holding note text be opened without thinking about it.
Click a square to read the hunk it stands for; in the characters view every other square that
hunk owns lights with it, because what is selected is the edit and not the square.

`--mosaic` draws the same chart, both views of it, with Mosaic itself instead, and clicks the
same way: the rows live in DuckDB-WASM — which is also what fans a hunk out into the squares it
is owed — a click publishes to a Mosaic Selection, and the panel on the right is a MosaicClient
that answers it with a query. That page fetches the library, so it is the one page
here that wants a network — and since it shows diffs it holds the same note text the other does.

Usage:
    ./microlite_waffle.py                                  # every sessions/*/microlite.md
    ./microlite_waffle.py --mosaic                         # the same chart, drawn by Mosaic
    ./microlite_waffle.py --view chars                     # open on the characters view
    ./microlite_waffle.py --chars-per-square 500           # pick the square's size yourself
    ./microlite_waffle.py --out ~/waffle.html
    ./microlite_waffle.py path/to/backup/*/microlite.md    # explicit files, in any order
"""

from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

from _env import REPO_ROOT
from _hunks import load, weekday
from _page import FRAMED, STYLE, embed, palette_css
from _term import link

SESSIONS = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve() / "sessions"

# Pinned, not `@latest`: the page is a record of a week, and it should draw the same next year.
CDN = "https://cdn.jsdelivr.net/npm/@uwdata/vgplot@0.31.0/+esm"

EXTRA_STYLE = """  .layout { grid-template-columns: minmax(320px, auto) minmax(0, 1fr); }
  /* The squares' colours by name, so a framed page can hand them to the host's palette (FRAMED
     in _page.py) — its first three series, in order — where the page on its own is lime, cyan
     and pink. */
  :root {
    --kind-added: var(--lime); --kind-mixed: var(--cyan); --kind-removed: var(--pink);
    --kind-full: var(--paper);
  }
  /* Framed, the squares are painted as the connectome's are — the palette's topic hues at half
     strength, no outline — but three slots along, so an added square is not read as the
     connectome's first topic. The whole-content square takes its neutral grey, since paper on
     paper with no outline would be no square at all. */
  :root.embedded {
    --kind-added: var(--series-4); --kind-mixed: var(--series-5); --kind-removed: var(--series-6);
    --kind-full: var(--series-rest);
  }
  .embedded rect.cell { fill-opacity: 0.5; }
  .embedded .legend i { opacity: 0.5 !important; }
  /* Framed, the legend is drawn as Plot draws the connectome's under it: bare 15px swatches, the
     name in the system face at 10px, capitalised, starting under the first square (alignLegend). */
  .embedded .legend { font: 10px system-ui, sans-serif; gap: 0 10px; min-height: 33px;
                      align-items: center; text-transform: capitalize; }
  .embedded .legend span { gap: 5.5px; }
  /* Framed, the controls sit over the chart on one line, the views and then the box taking the
     rest, from the first square to the chart's right edge (alignLegend); a phone wraps the box. */
  .embedded .controls { flex-direction: row; flex-wrap: wrap; align-items: stretch;
                        padding: 0 0 4px; box-sizing: border-box; }
  /* 12px, so the prompt it holds is whole beside the chips over a chart this narrow. */
  .embedded .controls input[type=search] { flex: 1 1 220px; min-width: 0; font-size: 12px; }
  /* The page's chips are lowercase by rule; in the post, capitalised like the legends. */
  .embedded .chip { text-transform: capitalize; }
  /* Framed, a hunk is drawn as the connectome's ends are (connection_waffle.py): the name and
     day in bold, the box under. Less the caret, since this one never folds; placed by
     alignDetail. */
  .embedded .end { margin-top: 18px; padding-top: 16px; }
  .embedded .end .who { display: block; font-size: 12px; font-weight: 700; margin-bottom: 3px;
                        overflow-wrap: anywhere; }
  .embedded .end .who > span { display: block; }
  /* And the axes in Plot's type, as the connectome's: 11px, regular, tabular figures. */
  .embedded .axis, .embedded .axis.week {
    font: 11px system-ui, sans-serif; font-variant-numeric: tabular-nums;
  }
  .embedded .legend i { width: 15px; height: 15px; border: 0; }
  /* The picked hunk's outline is the connectome's ring: five wide, in the gap (see outlinePath),
     square-capped so the separate sides close their corners as the ring's rect does. */
  .embedded .edge { stroke-width: 0; stroke-linecap: square; }
  /* No outline under the pointer: an outline means picked, as on the connectome's squares. */
  .embedded .piece:hover .edge { stroke-width: 0; }
  .embedded .piece.on .edge { stroke-width: 5; }
  .embedded .seam, .embedded #unit { display: none; }
  /* And the hunk panel is the note and its diff: no label over it, no badge row under the name. */
  .embedded .panel:has(#detail) > h2, .embedded #detail .facts { display: none; }
  /* Framed, the page is the chart and the note a click opens. The roll of every note touched is
     three screens of filenames under it, which in a post is a wall rather than a figure. */
  .embedded #notes-panel { display: none; }
  /* And the hunk sits beside the chart as soon as there is room for both. */
  /* The column is capped at the chart: left to `auto`, the legend's one long line took it wide
     and squeezed the hunk. A phone frame is narrower than the chart, which then scales down. */
  .embedded #waffle { max-width: 100%; height: auto; }
  @media (min-width: 960px) {
    .embedded .layout {
      grid-template-columns: calc(var(--chart-w, 550px) + 32px) minmax(340px, 1fr);
    }
  }
  @media (max-width: 1100px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  .chart { padding: 16px; overflow-x: auto; }
  .chart svg { display: block; }
  .piece { cursor: pointer; }
  /* The squares of one hunk tile a single region, so their shared edges must land on the same
     pixel; without this a fused piece shows anti-aliased hairlines at fractional zoom. */
  .cell { shape-rendering: crispEdges; }
  .edge { fill: none; stroke: var(--ink); stroke-width: 1.5; pointer-events: none; }
  .seam { fill: none; stroke: var(--ink); stroke-width: 1; stroke-opacity: 0.18;
          pointer-events: none; }
  .piece:hover .edge { stroke-width: 3; }
  .piece.on .edge { stroke-width: 4; }
  .axis { font: 11px Hack, ui-monospace, Menlo, monospace; fill: var(--ink); }
  .axis.week { font-size: 12px; font-weight: 700; }
  /* The week-by-week roll of what was touched, under the chart and across both columns. */
  #notes-panel { grid-column: 1 / -1; }
  .notes { padding: 4px 16px 16px; }
  .notes h3 {
    font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
    margin: 20px 0 8px; padding-bottom: 5px; border-bottom: 3px solid var(--ink);
  }
  .notes h3:first-child { margin-top: 8px; }
  .notes ul { margin: 0; padding-left: 22px; }
  .notes li { padding: 1px 0; overflow-wrap: anywhere; }

  .legend { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 16px 14px; font-size: 12px; }
  .legend span { display: flex; align-items: center; gap: 6px; }
  .legend i { width: 14px; height: 14px; border: 2px solid var(--ink); display: block; }
  .unit { padding: 0 16px 14px; font-size: 12px; max-width: 80ch; }
"""

# The detail panel, shared by both pages: the badges, and the hunk as a code block that colours
# like one. Deliberately not the six flat colours the rest of the page is drawn in — a diff has
# a palette people already read without a legend, and green-add / red-delete is it.
DETAIL_STYLE = """  .badge.added { background: var(--lime); }
  .badge.mixed { background: var(--cyan); }
  .badge.removed { background: var(--pink); }
  .badge.full { background: var(--paper); }

  .hunk { border: 3px solid var(--ink); margin-top: 14px; background: var(--card); }
  .hunk pre {
    margin: 0; padding: 12px; max-height: 62vh; overflow: auto; background: var(--card);
    font: 13px/1.55 Hack, ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .hunk code { display: block; white-space: pre; }
  .hunk code > span { display: block; padding: 0 6px; margin: 0 -6px; }
  .hunk .ln-h { color: #8250df; background: #f3eefd; font-weight: 700; }
  .hunk .ln-a { color: #116329; background: #e6ffec; }
  .hunk .ln-d { color: #a40e26; background: #ffebe9; }
  .hunk .ln-c { color: #57606a; }"""


SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__STYLE__
__EXTRA_STYLE__
</style>
</head>
<body>
<header>
  <h1>__HEADING__</h1>
  <div class="meta">__META__</div>
</header>

<div class="layout">
  <section class="panel">
    <h2 id="count">__COUNT__</h2>
    <div class="chart"><svg id="waffle"></svg></div>
    <div class="legend" id="legend"></div>
    <div class="unit" id="unit"></div>
    <div class="controls">
      <div class="chips" id="views" title="swap the views  ( v )">
        <button class="chip" type="button" data-view="hunks">hunks</button>
        <button class="chip" type="button" data-view="chars">characters</button>
      </div>
      <input type="search" id="q" placeholder="dim every square but one note  ( / )" autocomplete="off">
    </div>
  </section>

  <section class="panel">
    <h2>Hunk</h2>
    <div class="detail" id="detail">
      <div class="empty">Click a square to read the hunk it stands for.</div>
    </div>
  </section>

  <section class="panel" id="notes-panel">
    <h2>Notes edited</h2>
    <div class="notes" id="notes"></div>
  </section>
</div>

<script type="application/json" id="data">__DATA__</script>
__FRAMED__
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const { weeks, gap, perRow, kinds, charsPer } = DATA;
let { cell } = DATA;
// Framed by jaan.io (FRAMED in _page.py, which runs first so this can see the class), the chart
// takes the geometry of the connectome under it in the post — the same width, the same margins,
// WEEK_GAP empty columns between weeks — so the two read as one figure drawn twice.
const embedded = document.documentElement.classList.contains("embedded");
const WEEK_GAP = 1;   // connection_waffle.py's, framed (weekGap there)
// A week's date as a reader says it, the way the connectome's axis prints it (`pretty` there).
// Split rather than parsed through `Date`: a bare ISO day parses as UTC midnight, and west of
// Greenwich that formats as the day before. A synthetic week is mm-dd, a real one yyyy-mm-dd.
const MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];
const pretty = (iso) => {
  const m = /^(?:[0-9]{4}-)?([0-9]{2})-([0-9]{2})$/.exec(iso ?? "");
  return m ? `${MONTHS[+m[1] - 1]} ${+m[2]}` : (iso ?? "");
};
// Beside its panel, a square at 0.83 of the size it had filling that room with three empty columns
// between weeks, and one column between them now: the chart is that much narrower (15% on its
// last width), the panel taking the rest. Stacked, the frame's width. connection_waffle.py's
// chartWidth is this, and must stay so.
//
// Sized from the frame's full width in the post (basisWidth), not the frame as it stands: where
// the post pulls the frame's left edge in clear of its TOC rail, the panel beside the chart gives
// up that width and the chart does not — unless the panel would drop under its 340, when the
// chart gives way.
const basisWidth = () => {
  // iframe.wide in jaan.io's Prose.astro: the column's width or three quarters of the viewport,
  // whichever is more. A page on its own has only itself to go by.
  const host = window.frameElement;
  return host ? Math.max(host.parentElement.clientWidth, 0.75 * parent.innerWidth) : innerWidth;
};
const chartWidth = () => {
  if (innerWidth < 960) return Math.max(300, Math.min(900, innerWidth - 32));
  const unit = 0.83 * (Math.min(900, basisWidth() - 392) - 64) / (weeks.length * (perRow + 3) - 3 + 0.8);
  return Math.min(innerWidth - 392,
                  Math.round(64 + unit * (weeks.length * (perRow + WEEK_GAP) - WEEK_GAP + 0.8)));
};
const svg = document.getElementById("waffle");
const detailEl = document.getElementById("detail");
const qEl = document.getElementById("q");
const countEl = document.getElementById("count");
const unitEl = document.getElementById("unit");
const chips = [...document.querySelectorAll("#views .chip")];
const NS = "http://www.w3.org/2000/svg";
const NUM = new Intl.NumberFormat("en-US");

function el(name, attrs) {
  const n = document.createElementNS(NS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
}
// textContent everywhere below: a note's name and a hunk's body are the user's own private
// prose, and prose is not markup.
function badge(text, cls) {
  const s = document.createElement("span");
  s.className = "badge " + (cls || "");
  s.textContent = text;
  return s;
}
// A tick in the characters view runs to tens of thousands, and the axis is a gutter wide.
const short = n => n >= 1e6 ? n / 1e6 + "M" : n >= 1e3 ? n / 1e3 + "k" : String(n);

// The two views. Everything about the chart is the same in both — the columns, the colours, the
// click, the filter — except what one square stands for. So that is all a view is: how many
// squares a hunk gets, what the axis counts, and how to say so out loud.
const VIEWS = {
  hunks: {
    squares: () => 1,
    order: hunks => hunks,
    axis: rows => String(rows * perRow),
    sub: w => w.hunks.length + " hunks",
    count: "one square is one hunk",
    label: "Hunks",
    unit: "One square is one hunk, so a fixed typo and a rewritten page are the same size. "
        + "Characters weighs them instead.",
  },
  chars: {
    squares: h => h.squares,
    // Biggest first, from the floor up — which puts the squares that stand for nothing, the
    // whole-content notes with no diff to measure, in a cap at the top of the column. The axis
    // then reads true against the coloured part of the column instead of through that padding.
    order: hunks => [...hunks].sort((a, b) => b.chars - a.chars),
    axis: rows => short(rows * perRow * charsPer),
    sub: w => NUM.format(w.hunks.reduce((n, h) => n + h.chars, 0)) + " chars",
    count: "one square is " + NUM.format(charsPer) + " characters",
    label: "Characters",
    unit: "One square is " + NUM.format(charsPer) + " characters added or removed — rounded, and "
        + "never below one, so the smallest edit still has a square to click. One hunk's squares "
        + "are fused into one piece and the gap is left to mean a boundary, so two big edits side "
        + "by side read as two shapes rather than one; the faint seams inside a piece are still "
        + "there to count. Biggest hunk at the floor. A whole-content note keeps its single "
        + "square here, since there is no diff under it to measure; those are the neutral ones "
        + "capping a column, above where the axis still counts.",
  },
};

let view = DATA.view;
let selected = null;   // the hunk being read on the right, not the square that was clicked
let pieces = [];

function draw() {
  const V = VIEWS[view];
  svg.textContent = "";
  pieces = [];

  // One entry per square, bottom-up in column order. A hunk appears as many times as it has
  // squares and it is the same object every time, which is what lets one click light them all.
  const columns = weeks.map(w => V.order(w.hunks).flatMap(h => Array(V.squares(h)).fill(h)));

  // Framed, a square's pitch is whatever fits the width over the connectome's x units — the
  // weeks' columns, WEEK_GAP between them and 0.4 of one either side, its xDomain exactly —
  // with its margins. On its own page the chart keeps the fixed cell it was given.
  const padL = 52, padR = 12, padT = 20, padB = 47;   // the y label above; day, date, tally below
  const units = weeks.length * (perRow + WEEK_GAP) - WEEK_GAP + 0.8;
  const pitch = embedded ? (chartWidth() - padL - padR) / units : cell + gap;
  cell = pitch - gap;
  const colW = perRow * pitch - gap;
  const colGap = embedded ? WEEK_GAP * pitch + gap : 34;
  const lead = embedded ? 0.4 * pitch : 0;
  const tallest = Math.max(1, ...columns.map(c => Math.ceil(c.length / perRow)));
  const plotH = tallest * pitch - gap;
  const baseY = padT + plotH;
  const width = embedded ? chartWidth() : padL + weeks.length * (colW + colGap) - colGap + 12;
  const height = baseY + padB + (embedded ? gap : 0);
  if (embedded) document.documentElement.style.setProperty("--chart-w", `${width}px`);

  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  // A row is `perRow` squares, so a tick every whole number of rows is a round count of whatever
  // a square stands for. Every other row while the chart is short, and fewer once it is tall —
  // the characters view stacks several times the rows the hunks view does.
  const step = Math.max(2, Math.ceil(tallest / 10));
  for (let rows = 0; rows <= tallest; rows += step) {
    const y = baseY - rows * pitch;
    // Framed, a tick and no gridline or floor, as Plot draws the connectome's axis under it: six
    // pixels out from the plot's edge, the label three clear of it and centred on it.
    if (embedded)
      svg.appendChild(el("line", { x1: padL - 6, y1: y, x2: padL, y2: y,
                                   stroke: "var(--ink)", "stroke-width": 1 }));
    else
      svg.appendChild(el("line", { x1: padL - 8, y1: y, x2: width, y2: y,
                                   stroke: "var(--ink)", "stroke-width": rows ? 1 : 3,
                                   "stroke-opacity": rows ? 0.15 : 1 }));
    const t = el("text", { class: "axis", x: embedded ? padL - 9 : padL - 14,
                           y: y + (embedded ? 3.5 : 4), "text-anchor": "end" });
    t.textContent = V.axis(rows);
    svg.appendChild(t);
  }

  // What the axis counts, over its top-left corner with an arrow, the way Plot labels the
  // connectome's — so the one word changes with the toggle and nothing else does.
  // Clear of the top tick, whose label is centred on a gridline a gap above padT.
  const yLabel = el("text", { class: "axis ylabel", x: 4, y: padT - 12 });
  yLabel.textContent = "\\u2191 " + V.label;
  svg.appendChild(yLabel);

  weeks.forEach((w, i) => {
    const x0 = padL + lead + i * (colW + colGap);
    // The day of the week above the date. A session folder named `11-13` has no year in it and
    // therefore no weekday anybody can derive, so the writer that dropped the year hands one
    // over in `microlite.md`'s own header and `_hunks` carries it here. The same size as the
    // date, so the two read as one label on two lines.
    // Framed, the two lines sit where Plot sets the connectome's tick label — a baseline 19.3px
    // under the floor, the next 1em below — and no tally under them.
    const line1 = embedded ? 19.3 : 16, line2 = embedded ? 30.3 : 30;
    if (w.weekday) {
      const day = el("text", { class: "axis week", x: x0 + colW / 2, y: baseY + line1,
                               "text-anchor": "middle" });
      day.textContent = w.weekday;
      svg.appendChild(day);
    }
    const label = el("text", { class: "axis week", x: x0 + colW / 2,
                               y: baseY + (w.weekday ? line2 : embedded ? line1 : 18),
                               "text-anchor": "middle" });
    label.textContent = pretty(w.week);
    svg.appendChild(label);
    if (!embedded) {
      const sub = el("text", { class: "axis", x: x0 + colW / 2, y: baseY + (w.weekday ? 43 : 31),
                               "text-anchor": "middle" });
      sub.textContent = V.sub(w);
      svg.appendChild(sub);
    }

    // Bottom-up, wrapping at perRow — the column is the quantity, the way a waffle reads. A
    // hunk's squares are consecutive in that order, which is what makes them a shape.
    let n = 0;
    for (const h of V.order(w.hunks)) {
      const g = piece(w, h, x0, n, n + V.squares(h) - 1, baseY, pitch);
      svg.appendChild(g);
      pieces.push({ g, hunk: h, note: h.note.toLowerCase() });
      n += V.squares(h);
    }
  });

  outline();
  dim();
  alignLegend();
}

// Framed, the legend's first swatch starts where the first column's first square does, as the
// connectome's does under it (align() there). Measured, so it holds if the chart scales down.
function alignLegend() {
  const legend = document.getElementById("legend"), swatch = legend.querySelector("i");
  if (!embedded || !swatch) return;
  legend.style.paddingLeft = "0px";
  const first = Math.min(...[...svg.querySelectorAll("rect.cell")].map(r => r.getBoundingClientRect().left));
  legend.style.paddingLeft = `${first - swatch.getBoundingClientRect().left}px`;
  // The controls over the chart span the chart itself, edge to edge, as the connectome's caption
  // does over its plot.
  const controls = qEl.closest(".controls");
  controls.style.paddingLeft = "0px";
  controls.style.width = "";
  const left = controls.getBoundingClientRect().left;
  controls.style.paddingLeft = `${svg.getBoundingClientRect().left - left}px`;
  controls.style.width = `${svg.getBoundingClientRect().right - left}px`;
  alignDetail();
}

// Framed, and while the panel sits beside the chart: the hunk's name and day level with the
// controls over the chart, middle to middle, and its box running from the top of the y axis to
// the foot of the legend, so the hunk is exactly as tall as the chart it came from. Measured from
// where each rests, every margin cleared first, so a second pass changes nothing. Stacked, the
// box keeps its square.
function alignDetail() {
  const end = detailEl.querySelector(".end");
  if (!embedded || !end) return;
  const who = end.querySelector(".who"), box = end.querySelector(".hunk");
  end.style.marginTop = box.style.marginTop = box.style.height = "";
  box.style.aspectRatio = box.style.boxSizing = "";
  if (detailEl.getBoundingClientRect().left < svg.getBoundingClientRect().right) return;
  const q = qEl.getBoundingClientRect(), wr = who.getBoundingClientRect();
  end.style.marginTop = `${parseFloat(getComputedStyle(end).marginTop)
                           + (q.top + q.bottom) / 2 - (wr.top + wr.bottom) / 2}px`;
  const top = svg.querySelector(".ylabel").getBoundingClientRect().top;
  const bottom = Math.max(...[...document.querySelectorAll("#legend > span")]
                               .map(s => s.getBoundingClientRect().bottom));
  box.style.marginTop = `${parseFloat(getComputedStyle(box).marginTop)
                           + top - box.getBoundingClientRect().top}px`;
  box.style.aspectRatio = "auto";
  box.style.boxSizing = "border-box";
  box.style.height = `${bottom - top}px`;
}

// One hunk, drawn as one thing. A square keeps its place on the pitch grid and simply grows into
// the gap on any side where the square next to it belongs to the same hunk — so the hunk tiles a
// single region, and the gap is left to mean the one thing the eye already reads it as: a
// boundary. Two large edits side by side used to be one undifferentiated block of colour.
//
// The outline is a path around that region rather than a border on each square, so it is the
// piece that thickens when selected. Inside it the seams stay, faintly: a waffle is a thing you
// count, and a hunk you cannot count the squares of is just a bar.
function piece(w, h, x0, first, last, baseY, pitch) {
  const g = el("g", { class: "piece" });
  const mine = n => n >= first && n <= last;
  const seam = [];

  for (let n = first; n <= last; n++) {
    const c = n % perRow, r = (n - c) / perRow;
    // Grid neighbours, not merely the next hunk-square along: a piece that wraps at the end of
    // a row is two blocks in the column, and drawing it as one would join them across the wrap.
    const left = c > 0 && mine(n - 1);
    const right = c < perRow - 1 && mine(n + 1);
    const down = mine(n - perRow);
    const up = mine(n + perRow);

    const x = x0 + c * pitch, top = baseY - r * pitch - cell;
    const x1 = x + cell + (right ? gap : 0);
    const y0 = top - (up ? gap : 0), y1 = top + cell;
    // The square and the gap to its right as one rect, the gap above it as another: the corner
    // those two gaps share is filled only when all four squares round it are this hunk's. One
    // rect up and right at once filled it with three, a tooth in the inner corner of an L.
    const upRight = c < perRow - 1 && mine(n + perRow + 1);
    const paint = { class: "cell", fill: kinds[h.kind].fill, "fill-opacity": kinds[h.kind].opacity };
    g.appendChild(el("rect", { ...paint, x, y: top, width: x1 - x, height: y1 - top }));
    if (up) g.appendChild(el("rect", { ...paint, x, y: y0, height: gap,
                                       width: cell + (right && upRight ? gap : 0) }));

    // Each internal boundary belongs to the square below it or to its left, so it is drawn once.
    if (right) seam.push(`M${x1} ${y0}V${y1}`);
    if (up) seam.push(`M${x} ${y0}H${x1}`);
  }

  if (seam.length) g.appendChild(el("path", { class: "seam", d: seam.join("") }));
  g.appendChild(el("path", { class: "edge",
                             d: outlinePath(first, last, x0, baseY, pitch, embedded ? gap / 2 : 0) }));
  const tip = el("title");
  tip.textContent = `${h.note}\\n${h.kind} · +${h.add} −${h.dele}`
                  + (h.chars ? ` · ${NUM.format(h.chars)} chars` : "");
  g.appendChild(tip);
  g.onclick = () => show(w, h);
  return g;
}

// The outline of one piece, traced round the region its squares fill. The grid is taken at twice
// the square's resolution — each square, and each gap beside one — so a gap the piece's rects
// fill is inside it and a gap they leave is a notch, exactly as `piece` tiles them. Every side of
// that boundary moves out by `o`, and each end with it: out past a corner that turns away from the
// piece, back from one that turns into it, not at all where the side runs on into the next.
//
// On its own page `o` is 0 and this is the piece's own edge. Framed, it is half the gap, and
// stroked five wide with square caps it is the connectome's ring (OUTLINE_WIDTH in
// connection_waffle.py): a band centred in the gap around the hunk, so a picked hunk is outlined
// alike in both charts.
function outlinePath(first, last, x0, baseY, pitch, o) {
  const sq = (c, r) => c >= 0 && c < perRow && r >= 0
                       && r * perRow + c >= first && r * perRow + c <= last;
  // `>>` floors negatives too, so the slots off the left edge read as empty.
  const M = (i, j) => {
    const c = i >> 1, r = j >> 1;
    if (!(i & 1) && !(j & 1)) return sq(c, r);
    if (!(j & 1)) return sq(c, r) && sq(c + 1, r);     // the gap right of a square
    if (!(i & 1)) return sq(c, r) && sq(c, r + 1);     // the gap above one
    return sq(c, r) && sq(c + 1, r) && sq(c, r + 1) && sq(c + 1, r + 1);   // a corner, among four
  };
  const xs = i => x0 + (i >> 1) * pitch + (i & 1 ? cell : 0);
  const xe = i => i & 1 ? x0 + ((i >> 1) + 1) * pitch : xs(i) + cell;
  const yb = j => baseY - (j >> 1) * pitch - (j & 1 ? cell : 0);
  const yt = j => j & 1 ? baseY - ((j >> 1) + 1) * pitch : yb(j) - cell;
  const end = (along, diag) => !along ? o : diag ? -o : 0;
  const d = [];
  const r0 = Math.floor(first / perRow), r1 = Math.floor(last / perRow);
  for (let j = 2 * r0; j <= 2 * r1; j++) for (let i = 0; i < 2 * perRow - 1; i++) {
    if (!M(i, j)) continue;
    if (!M(i - 1, j)) d.push(`M${xs(i) - o} ${yt(j) - end(M(i, j + 1), M(i - 1, j + 1))}`
                           + `V${yb(j) + end(M(i, j - 1), M(i - 1, j - 1))}`);
    if (!M(i + 1, j)) d.push(`M${xe(i) + o} ${yt(j) - end(M(i, j + 1), M(i + 1, j + 1))}`
                           + `V${yb(j) + end(M(i, j - 1), M(i + 1, j - 1))}`);
    if (!M(i, j - 1)) d.push(`M${xs(i) - end(M(i - 1, j), M(i - 1, j - 1))} ${yb(j) + o}`
                           + `H${xe(i) + end(M(i + 1, j), M(i + 1, j - 1))}`);
    if (!M(i, j + 1)) d.push(`M${xs(i) - end(M(i - 1, j), M(i - 1, j + 1))} ${yt(j) - o}`
                           + `H${xe(i) + end(M(i + 1, j), M(i + 1, j + 1))}`);
  }
  return d.join("");
}

// What is selected is a hunk and not a square: in the characters view a hunk owns a block of
// them, and outlining one of that block would say the edit was smaller than it is.
function outline() {
  for (const p of pieces) p.g.classList.toggle("on", p.hunk === selected);
  // Last in the drawing, so its outline lies over the squares beside it, as the connectome's
  // ring lies over its neighbours.
  const on = pieces.find(p => p.hunk === selected);
  if (on) svg.appendChild(on.g);
}

function dim() {
  const q = qEl.value.trim().toLowerCase();
  for (const p of pieces) p.g.style.opacity = !q || p.note.includes(q) ? "" : "0.1";
}

function show(w, h) {
  selected = h;
  outline();
  detailEl.textContent = "";
  const box = hunkBox(h);
  // Framed, the hunk is drawn as the connectome's ends are, so a hunk opened in either chart of
  // the post looks the same: the caret, the note's name and its day in bold, the box open under
  // them. On its own page, the name as a title and the facts as badges.
  if (embedded) {
    // Never folded here — the hunk is the whole of what a click asks for — so no caret.
    const end = document.createElement("div");
    end.className = "end";
    const who = end.appendChild(document.createElement("span"));
    who.className = "who";
    for (const line of [h.note, [w.weekday, pretty(w.week)].filter(Boolean).join(" ")]) {
      who.appendChild(document.createElement("span")).textContent = line;
    }
    end.appendChild(box);
    detailEl.appendChild(end);
    alignDetail();
    return;
  }
  const title = document.createElement("h2");
  title.className = "title";
  title.textContent = h.note;
  detailEl.appendChild(title);
  const facts = document.createElement("div");
  facts.className = "facts";
  facts.appendChild(badge(w.week));
  facts.appendChild(badge(h.kind, h.kind));
  if (h.add || h.dele) facts.appendChild(badge(`+${h.add} −${h.dele}`));
  if (h.chars) facts.appendChild(badge(`${NUM.format(h.chars)} chars`));
  detailEl.appendChild(facts);
  detailEl.appendChild(box);
}

// The diff, one span per line, coloured by what the line did.
function hunkBox(h) {
  const box = document.createElement("div");
  box.className = "hunk";
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.className = "language-diff";
  for (const line of [h.head, ...(h.text ? h.text.split("\\n") : [])]) {
    const span = document.createElement("span");
    span.className = line.startsWith("@@") ? "ln-h"
      : line.startsWith("+") ? "ln-a"
      : line.startsWith("-") ? "ln-d"
      : "ln-c";
    span.textContent = line || " ";
    code.appendChild(span);
  }
  pre.appendChild(code);
  box.appendChild(pre);
  return box;
}

for (const k in kinds) {
  const s = document.createElement("span");
  const i = document.createElement("i");
  i.style.background = kinds[k].fill;
  i.style.opacity = kinds[k].opacity;
  s.appendChild(i);
  // Framed, the name alone, as the connectome's legend under it has; the post says the rest.
  s.appendChild(document.createTextNode(embedded ? k : `${k} — ${kinds[k].note}`));
  document.getElementById("legend").appendChild(s);
}
alignLegend();

// Redrawing from scratch rather than resizing what is there: a hunk's squares are one, then
// several, so there is nothing to animate between and the chart is a few dozen rects anyway.
// The selection survives because it is the hunk, and the filter because it re-reads the box.
function setView(next) {
  view = next;
  for (const b of chips) b.setAttribute("aria-pressed", String(b.dataset.view === view));
  countEl.textContent = VIEWS[view].count;
  unitEl.textContent = VIEWS[view].unit;
  draw();
}
for (const b of chips) b.onclick = () => setView(b.dataset.view);
setView(view);
// A frame is as wide as the post makes it, so a resize can move the chart's width.
let drawnAt = chartWidth(), resizing;
if (embedded) addEventListener("resize", () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => { if (chartWidth() !== drawnAt) { drawnAt = chartWidth(); draw(); } }, 150);
});

// One plain list per week, stacked, each under its date.
const notesEl = document.getElementById("notes");
for (const w of weeks) {
  const h = document.createElement("h3");
  h.textContent = w.week;
  notesEl.appendChild(h);
  const ul = document.createElement("ul");
  for (const note of w.notes) {
    const li = document.createElement("li");
    li.textContent = note;
    ul.appendChild(li);
  }
  notesEl.appendChild(ul);
}

// The box finishes a note's name as it is typed — the finished part selected, so the next
// keystroke replaces it — and Tab or Enter takes the rest. A deletion is never completed, or
// backspacing over a suggestion would put it straight back.
// The `/` shortcut is this page's own; framed in a post the key belongs to the post.
// Framed, the controls move over the chart (laid out by alignLegend and EXTRA_STYLE).
if (embedded) {
  qEl.placeholder = "Select one note to view its snapshots";
  svg.closest(".chart").before(qEl.closest(".controls"));
  alignLegend();
}
const noteNames = [...new Set(weeks.flatMap(w => w.hunks.map(h => h.note)))].sort();
qEl.addEventListener("input", ev => {
  const typed = qEl.value;
  if (typed && !ev.inputType?.startsWith("delete")) {
    const hit = noteNames.find(n => n.toLowerCase().startsWith(typed.toLowerCase()));
    if (hit && hit.length > typed.length) {
      qEl.value = typed + hit.slice(typed.length);
      qEl.setSelectionRange(typed.length, hit.length);
    }
  }
  dim();
});
qEl.addEventListener("keydown", ev => {
  if (ev.key !== "Tab" && ev.key !== "Enter") return;
  const pending = qEl.selectionStart < qEl.selectionEnd && qEl.selectionEnd === qEl.value.length;
  if (ev.key === "Enter" || pending) ev.preventDefault();
  if (pending) qEl.setSelectionRange(qEl.value.length, qEl.value.length);
  dim();
});
document.addEventListener("keydown", ev => {
  if (document.activeElement === qEl) return;
  if (ev.key === "/") { ev.preventDefault(); qEl.focus(); }
  if (ev.key === "v") setView(view === "hunks" ? "chars" : "hunks");
});
</script>
</body>
</html>
"""

# How many characters a square is worth in the characters view. The hunks view has a unit
# handed to it and this one does not: a week runs to tens of thousands of characters, and a
# square each would be a column nobody can see the top of. So the square is sized the way an axis
# sizes its own ticks — a round number off a 1-2-5 ladder, the smallest that keeps the tallest
# week inside ROWS rows — and then said out loud under the chart, because a waffle whose square
# stands for an arbitrary quantity is unreadable unless the quantity is written down.
ROWS = 24


def squares(chars: int, per_square: int) -> int:
    """How many squares one hunk is owed. Never none: the smallest edit stays clickable."""
    return max(1, round(chars / per_square))


def quantum(weeks: list[dict], per_row: int, rows: int = ROWS) -> int:
    """The smallest round number of characters per square that keeps every week inside `rows`."""
    ladder = [n * 10 ** k for k in range(7) for n in (1, 2, 5)]
    for q in ladder:
        if all(sum(squares(h["chars"], q) for h in w["hunks"]) <= per_row * rows for w in weeks):
            return q
    return ladder[-1]


# `fill` is the palette variable, for the page this repo draws itself; `hex` is the same six
# colours written out, because Plot's colour range is read by a library that never sees the CSS.
KINDS = {
    "added":   {"fill": "var(--kind-added)", "hex": "#b6ff3c", "opacity": 1,
                "note": "lines only added"},
    "mixed":   {"fill": "var(--kind-mixed)", "hex": "#4de1ff", "opacity": 1,
                "note": "added and removed"},
    "removed": {"fill": "var(--kind-removed)", "hex": "#ff6ec7", "opacity": 0.35,
                "note": "lines only removed"},
    "full":    {"fill": "var(--kind-full)", "hex": "#f4f1e4", "opacity": 1,
                "note": "whole note, no diff to hunk"},
}


# The same chart, drawn by the library the form was borrowed from — and by the whole library,
# not just its marks: the rows live in DuckDB, DuckDB lays the waffle out, clicking a square
# publishes to a Mosaic Selection, and the panel on the right is a MosaicClient that answers
# that selection with a query of its own. Adapted from `specs/esm/athlete-birth-waffle.js` in
# the Mosaic repository, with the athletes swapped for hunks.
#
# WHY THIS IS NOT `waffleY`. It was, and it could not do what this page is for. Plot draws a
# waffle as one `<path>` per stacked group, filled with an SVG `<pattern>` whose tile is the
# square — so the squares are a repeating fill and not elements, and there is nothing per-unit
# under the pointer to click. `waffleY` also aggregates (`y: count()`), so by the time the mark
# sees the data a week is four rows and an individual hunk is gone. One square per hunk that
# knows which hunk it is means one mark per hunk, which is `rect`, positioned by the layout
# `waffleY` would have done internally: `row_number()` over each week, wrapped at `--per-row`.
#
# That trade takes the example's `round` input with it — it exists only because `waffleY`
# aggregates. `unit` comes back in the second view, though, and comes back meaning what it means
# in the example: a square is worth some number of characters rather than exactly one hunk. Here
# that is a row per square in DuckDB instead of a divisor in the mark, so the square that stands
# for a thousand characters still belongs to exactly one hunk and still has a diff to show.
# `gap` and `radius` survive as `inset` and `rx`, which is what they were anyway.
#
# This page fetches vgplot from a CDN, so it is the one page here that wants a network, and now
# that a click shows a diff it carries the note text to show — the same private prose as the
# page this repo draws itself, in a file that is not inert. Nothing is uploaded (DuckDB runs in
# the tab, and the rows never leave it), but `just waffle` remains the one to reach for.
MOSAIC_STYLE = """  /* The shared layout puts a narrow list on the left; here the left is the chart, and the
     track is the plot's own width plus the panel's 32 of padding and 8 of border — `auto`
     would size to the prose. */
  .layout { grid-template-columns: 800px minmax(0, 1fr); }
  @media (max-width: 1260px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  .chart { padding: 16px 16px 8px; overflow-x: auto; }

  /* Mosaic wraps a plot in `div.plot` and sets `display: flex` on it INLINE, and the legend is
     a sibling of the chart rather than part of it. One nowrap flex line, so the legend stood
     beside the chart and flex-shrank it to three quarters of the size it was asked for — every
     square, tick and label scaled down with it. `display: block` from a stylesheet cannot win
     against an inline style, but `flex-wrap` was never set inline: let the line wrap and the
     chart takes the width it declared, with the legend under it. */
  .chart .plot { flex-wrap: wrap; }
  .chart form { font: inherit; }
  .chart label { font-size: 13px; }
  .chart select, .chart input[type=range] { width: auto; margin: 0 14px 0 4px; }
  .note { padding: 0 16px 16px; font-size: 12px; max-width: 72ch; }
  .note code { background: var(--paper); border: 2px solid var(--ink); padding: 0 4px; }"""

MOSAIC_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__STYLE__
__EXTRA_STYLE__
</style>
</head>
<body>
<header>
  <h1>__HEADING__</h1>
  <div class="meta">__META__</div>
</header>

<div class="layout">
  <section class="panel">
    <h2 id="count">__COUNT__</h2>
    <div class="chart" id="view">loading duckdb-wasm&hellip;</div>
    <p class="note">__NOTE__</p>
  </section>

  <section class="panel">
    <h2>Hunk</h2>
    <div class="detail" id="detail">
      <div class="empty">Click a square to read the hunk it stands for.</div>
    </div>
  </section>

  <section class="panel" id="notes-panel">
    <h2>Notes edited</h2>
    <div class="notes" id="notes"></div>
  </section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script type="module">
import * as vg from "__CDN__";

const { rows, kinds, colors, perRow, charsPer, view, weekdays } =
  JSON.parse(document.getElementById("data").textContent);

// The example omits this because it runs inside Mosaic's own dev server: a coordinator with no
// connector reaches for a socket on ws://localhost:3000, and there is no such server here.
// Naming DuckDB-WASM is what makes the page open on its own, from file:// as well as over http.
vg.coordinator().databaseConnector(new vg.DuckDBWASMConnector());

// The rows are already in the page, so they are inserted rather than fetched — and then DuckDB
// does the arithmetic `waffleY` would have done inside the mark: number each week's squares, and
// wrap them at `perRow` into the column that reads as the quantity. Twice, because the two views
// differ in nothing but how many squares a hunk is owed — one, or one per `charsPer` characters
// it changed — and a table each keeps the mark that reads it on plain `col`/`row` columns.
await vg.coordinator().exec([
  vg.loadObjects("hunks_raw", rows),
  // The layout rule is Plot's own, from `wafflePoints` in its waffle mark: unit k of a column
  // sits at `k % multiple` across and `floor(k / multiple)` up, filled bottom-up and
  // left-to-right, so only the topmost row of a column is ever short. `//` and not `/`:
  // DuckDB's `/` is float division and a cast rounds rather than truncates, which quietly
  // hands three cells to the bottom row and shifts every row above it.
  `CREATE OR REPLACE TABLE squares_hunks AS
     SELECT * EXCLUDE (n, squares),
            ((n - 1) % ${perRow})::INTEGER  AS col,
            ((n - 1) // ${perRow})::INTEGER AS row
     FROM (SELECT *, row_number() OVER (PARTITION BY week ORDER BY rank, ord) AS n
           FROM hunks_raw)`,
  // A row per square rather than per hunk: `generate_series` over the count Python worked out
  // for each hunk, joined laterally, so a hunk that rewrote a page arrives as a block of rows
  // that each carry its diff along. The text repeats across the block, which is what lets a
  // click on any square of it answer with the hunk — and DuckDB is in this tab, holding a few
  // megabytes it does not have to send anywhere. Ordered biggest hunk first so the squares that
  // stand for nothing — a whole-content note has no diff to measure and still gets one — cap the
  // column rather than lifting the coloured squares away from the axis that counts them.
  `CREATE OR REPLACE TABLE squares_chars AS
     SELECT * EXCLUDE (n, squares, unit),
            ((n - 1) % ${perRow})::INTEGER  AS col,
            ((n - 1) // ${perRow})::INTEGER AS row
     FROM (SELECT h.*, u.unit,
                  row_number() OVER (PARTITION BY h.week
                                     ORDER BY h.chars DESC, h.rank, h.ord, u.unit) AS n
           FROM hunks_raw h, generate_series(1, h.squares) AS u(unit))`,
  // The outline of each piece, as the segments a `rule` mark can draw: a square contributes the
  // side it does not share with another square of the same hunk. Which is the whole trick —
  // `NOT EXISTS` a neighbour at `hid`, and what is left is the boundary of the union.
  //
  // The page this repo draws itself fuses a hunk's squares into one shape instead, growing each
  // square into the gap it shares with its own kind. That cannot be done here: Plot's `inset` is
  // one number for every side of every rect, and there is no per-side channel to make a square
  // reach toward one neighbour and not another. So the squares stay as they are and the grouping
  // is drawn over them — same boundary, laid on top rather than left as the shape's own edge.
  `CREATE OR REPLACE TABLE edges_v AS
     SELECT week, col AS x, row AS y1, row + 1 AS y2 FROM squares_chars s
      WHERE NOT EXISTS (SELECT 1 FROM squares_chars n
                         WHERE n.hid = s.hid AND n.col = s.col - 1 AND n.row = s.row)
     UNION ALL
     SELECT week, col + 1, row, row + 1 FROM squares_chars s
      WHERE NOT EXISTS (SELECT 1 FROM squares_chars n
                         WHERE n.hid = s.hid AND n.col = s.col + 1 AND n.row = s.row)`,
  `CREATE OR REPLACE TABLE edges_h AS
     SELECT week, row AS y, col AS x1, col + 1 AS x2 FROM squares_chars s
      WHERE NOT EXISTS (SELECT 1 FROM squares_chars n
                         WHERE n.hid = s.hid AND n.col = s.col AND n.row = s.row - 1)
     UNION ALL
     SELECT week, row + 1, col, col + 1 FROM squares_chars s
      WHERE NOT EXISTS (SELECT 1 FROM squares_chars n
                         WHERE n.hid = s.hid AND n.col = s.col AND n.row = s.row + 1)`
]);

// Not `{ empty: true }`. An empty selection would then match no rows, and `highlight` reads the
// same predicate — so the whole chart would sit dimmed before anyone had clicked anything. This
// way an empty selection filters nothing, the chart rests at full colour, and the panel below
// treats "no clause yet" as its own empty state rather than as a query.
const $hunk = vg.Selection.single();
const $gap = vg.Param.value(1);
const $radius = vg.Param.value(0);
const $view = vg.Param.value(view);

const short = n => n >= 1e6 ? n / 1e6 + "M" : n >= 1e3 ? n / 1e3 + "k" : String(n);

// One plot per view, both built here and swapped in the page — rather than one plot whose table
// is rewritten underneath it, which is a change Mosaic has no way to hear about.
function waffle(table, label, ticks, outlines) {
  return vg.plot(
    vg.rect(
      vg.from(table),
      {
        fx: "week",
        x1: "col", x2: vg.sql`col + 1`,
        y1: "row", y2: vg.sql`row + 1`,
        fill: "kind",
        // A deletion reads lighter than a growth. `identity` keeps these as the opacities they
        // are; the default opacity scale would rescale 0.35 to nothing.
        fillOpacity: vg.sql`CASE WHEN kind = 'removed' THEN 0.35 ELSE 1 END`,
        inset: $gap,
        rx: $radius,
        stroke: "#111111",
        strokeWidth: 1
      }
    ),
    // A week, a column and a row identify one square, and all three are real channels — so the
    // clause this publishes is that square and nothing else, with no synthetic key to carry.
    // Both tables spell those three the same, which is what lets a selection survive the toggle:
    // the outline stays where it is and the panel re-reads whatever now sits under it.
    vg.toggle({ as: $hunk, channels: ["fx", "x", "y"] }),
    // Dimming alone cannot carry the selection, because opacity is already spoken for: a deleted
    // hunk is drawn faint on purpose, and a faint square that is merely unselected looks the
    // same. So the unselected also lose their black outline, which nothing else in the chart
    // uses — the one square still bordered in ink is the one being read on the right.
    vg.highlight({ by: $hunk, fillOpacity: 0.45, stroke: "#c8c8c8" }),
    // After the interactors, deliberately: each one binds to the mark declared before it, so
    // these two are outside the toggle and outside the highlight. The outline of a piece is not
    // a thing to click — the squares under it already are — and it has to stay ink while the
    // squares around it go pale, or the grouping disappears exactly when a hunk is being read.
    outlines ? [
      vg.ruleX(vg.from("edges_v"),
               { fx: "week", x: "x", y1: "y1", y2: "y2", stroke: "#111111", strokeWidth: 2 }),
      vg.ruleY(vg.from("edges_h"),
               { fx: "week", y: "y", x1: "x1", x2: "x2", stroke: "#111111", strokeWidth: 2 })
    ] : [],
    vg.colorDomain(kinds),
    vg.colorRange(colors),
    vg.opacityScale("identity"),
    vg.colorLegend({ label: "" }),
    vg.xAxis(null),
    vg.xDomain([0, perRow]),
    vg.fxLabel(null),
    vg.fxTickSize(0),
    // Weekday over date, one tick label on two lines; Plot splits on the newline itself, and
    // both lines are the same size.
    vg.fxTickFormat(w => weekdays?.[w] ? `${weekdays[w]}\\n${w}` : w),
    vg.fxPaddingInner(0.24),
    vg.yLabel(label),
    // The y scale counts rows, because that is what a square is tall; the reader wants hunks, or
    // the characters those rows of squares are worth.
    vg.yTickFormat(ticks),
    vg.width(760),
    vg.height(430),
    vg.marginLeft(50)
  );
}

const charts = {
  // No outlines in the hunks view: a piece there is one square, and its own border is already
  // the boundary. They are the answer to a question only the characters view raises — whether
  // the block of colour in front of you is one edit or four of them, side by side.
  hunks: waffle("squares_hunks", "hunks", d => d * perRow),
  chars: waffle("squares_chars", "characters", d => short(d * perRow * charsPer), true)
};

/**
 * The panel on the right. Not a click handler reading a JavaScript array — a Mosaic client, so
 * the selection the chart publishes is answered the way every other view in Mosaic answers one:
 * with a query. DuckDB holds the diff text and hands back the one row that matches — out of
 * whichever of the two layouts is on screen, which is the only thing the toggle changes here.
 */
class HunkDetail extends vg.MosaicClient {
  constructor(selection, el, table) {
    super(selection);
    this.el = el;
    this.table = table;
  }
  // The view toggle swaps the table under the question, then asks it again — the same request
  // the coordinator makes when a selection changes, made by hand because nothing else changed.
  setTable(table) {
    this.table = table;
    this.requestQuery();
  }
  query(filter = []) {
    // No clause means nothing is selected, not "every hunk matches" — so ask for nothing and
    // let the empty result render the empty panel. `WHERE false` rather than a null query:
    // the coordinator hands a null straight to DuckDB and it is a parse error there.
    return vg.Query
      .from(this.table)
      .select("week", "note", "kind", "head", "text", "adds", "dels", "chars")
      .where(filter?.length ? filter : vg.sql`false`)
      .limit(1);
  }
  queryResult(data) {
    const [row] = Array.from(data);
    this.el.replaceChildren(row ? detail(row) : empty());
    return this;
  }
}

// textContent everywhere below: a note's name and a hunk's body are the user's own private
// prose, and prose is not markup.
function badge(text, cls) {
  const s = document.createElement("span");
  s.className = "badge " + (cls || "");
  s.textContent = text;
  return s;
}

function empty() {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = "Click a square to read the hunk it stands for.";
  return d;
}

function detail(row) {
  const frag = document.createDocumentFragment();
  const title = document.createElement("h2");
  title.className = "title";
  title.textContent = row.note;
  frag.appendChild(title);

  const facts = document.createElement("div");
  facts.className = "facts";
  facts.appendChild(badge(row.week));
  facts.appendChild(badge(row.kind, row.kind));
  if (row.adds || row.dels) facts.appendChild(badge(`+${row.adds} −${row.dels}`));
  if (row.chars) facts.appendChild(badge(`${row.chars.toLocaleString("en-US")} chars`));
  frag.appendChild(facts);

  const box = document.createElement("div");
  box.className = "hunk";
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.className = "language-diff";
  for (const line of [row.head, ...(row.text ? row.text.split("\\n") : [])]) {
    const span = document.createElement("span");
    span.className = line.startsWith("@@") ? "ln-h"
      : line.startsWith("+") ? "ln-a"
      : line.startsWith("-") ? "ln-d"
      : "ln-c";
    span.textContent = line || " ";
    code.appendChild(span);
  }
  pre.appendChild(code);
  box.appendChild(pre);
  frag.appendChild(box);
  return frag;
}

// A pane each, so switching views is a `display` and not a teardown: the plot that is put away
// keeps its scales, its DuckDB queries and its place in the coordinator, and comes back drawn.
const panes = {};
for (const name in charts) {
  panes[name] = document.createElement("div");
  panes[name].appendChild(charts[name]);
}

const panel = new HunkDetail($hunk, document.getElementById("detail"), `squares_${view}`);
vg.coordinator().connect(panel);

const COUNT = {
  hunks: "one square is one hunk · click one",
  chars: `one square is ${charsPer.toLocaleString("en-US")} characters · click one`
};

function setView(next) {
  for (const name in panes) panes[name].style.display = name === next ? "" : "none";
  document.getElementById("count").textContent = COUNT[next];
  // The panel is pointed at the other table and asked again. The clause the chart published
  // names a square by week, column and row and both tables spell those the same, so the
  // question stays askable — it is now being asked of a different layout, and answers with
  // whichever hunk that square belongs to there.
  panel.setTable(`squares_${next}`);
}

$view.addEventListener("value", setView);

document.getElementById("view").replaceChildren(
  vg.vconcat(
    vg.hconcat(
      // `value` as well as `as`: a menu given only a Param starts on a blank option, and it
      // is bound to one that already knows which view the page opened on.
      vg.menu({ as: $view, label: "View", value: view, options: [
        { value: "hunks", label: "hunks — one square each" },
        { value: "chars", label: `characters — one square per ${charsPer.toLocaleString("en-US")}` }
      ] }),
      vg.menu({ as: $gap, options: [0, 1, 2, 3, 4, 5], label: "Gap" }),
      vg.slider({ as: $radius, min: 0, max: 10, step: 0.1, label: "Radius" })
    ),
    vg.vspace(10),
    panes.hunks,
    panes.chars
  )
);
setView(view);
</script>
</body>
</html>
"""


def mosaic(weeks: list[dict], kinds: dict, meta: str, cdn: str, per_row: int,
           per_square: int, view: str) -> str:
    """The Mosaic page. One row per hunk — DuckDB fans a hunk out into its squares in the tab."""
    html = MOSAIC_SHELL
    for token, value in (
        ("__TITLE__", "hunks — obsidian, week by week (mosaic)"),
        ("__STYLE__", STYLE),
        ("__EXTRA_STYLE__", MOSAIC_STYLE + "\n" + DETAIL_STYLE),
        ("__HEADING__", "Hunks"),
        ("__META__", meta),
        ("__COUNT__", "one square is one hunk · click one"),  # rewritten per view in the page
        ("__CDN__", cdn),
        ("__NOTE__", "Drawn by <code>@uwdata/vgplot</code>, loaded from a CDN and running "
                     "DuckDB-WASM in this tab &mdash; so this page, alone among the pages here, "
                     "needs a network to open, and it holds note text the way the others do. "
                     "<code>just waffle</code> draws the same chart with nothing fetched."),
    ):
        html = html.replace(token, value)

    rank = {k: i for i, k in enumerate(kinds)}
    # `hid` numbers the hunks. The rows are one per hunk here and DuckDB fans them out into
    # squares, so the id is what tells the fanned-out rows which piece they came back from —
    # and it is what the outline of that piece is traced from.
    rows = [
        {"hid": hid, "week": w["week"], "kind": h["kind"], "rank": rank[h["kind"]], "ord": i,
         "note": h["note"], "head": h["head"], "text": h["text"],
         "adds": h["add"], "dels": h["dele"], "chars": h["chars"], "squares": h["squares"]}
        for hid, (w, i, h) in enumerate(
            (w, i, h) for w in weeks for i, h in enumerate(w["hunks"]))
    ]
    # Last, and only once: a note whose text contains __META__ is data, not a template.
    return html.replace("__DATA__", embed({
        "rows": rows,
        "kinds": list(kinds),
        "colors": [kinds[k]["hex"] for k in kinds],
        "perRow": per_row,
        "charsPer": per_square,
        "view": view,
        # The day of the week over each column's date, as on the static page — see `weekday()`.
        "weekdays": {w["week"]: weekday(w) for w in weeks},
    }))

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Every week's Obsidian hunks as a waffle chart — a square per hunk, or a "
                    "square per round number of characters edited.")
    ap.add_argument("files", nargs="*", type=Path,
                    help=f"microlite.md files (default: {SESSIONS}/*/microlite.md).")
    ap.add_argument("--out", type=Path, help="Destination HTML (default: beside the sessions).")
    ap.add_argument("--per-row", type=int, default=5, help="Squares per row in a week (default 5).")
    ap.add_argument("--cell", type=int, default=16, help="Square size in px (default 16).")
    ap.add_argument("--gap", type=int, default=4, help="Gap between squares in px (default 4).")
    ap.add_argument("--view", choices=("hunks", "chars"), default="hunks",
                    help="Which view the page opens on; both are in it either way (default hunks).")
    ap.add_argument("--chars-per-square", type=int, default=0, metavar="N",
                    help="Characters one square stands for in the chars view (default: a round "
                         "number chosen to fit the tallest week).")
    ap.add_argument("--hunks-only", action="store_true",
                    help="Drop the whole-content notes, counting real `@@` hunks alone.")
    ap.add_argument("--mosaic", action="store_true",
                    help="Draw it with Mosaic instead (rect marks, Selection, MosaicClient). "
                         "Needs a network to open.")
    ap.add_argument("--cdn", default=CDN, help=f"Where --mosaic imports vgplot from ({CDN}).")
    args = ap.parse_args()

    files = args.files or sorted(SESSIONS.glob("*/microlite.md"))
    if not files:
        sys.exit(f"No microlite.md found under {SESSIONS} — run `just weekly --only microlite`.")

    # One parser, in _hunks.py, shared with the tools that annotate and draw connections — a
    # second copy of "what counts as a hunk" is a second answer to which square an id means.
    weeks = load(files, hunks_only=args.hunks_only)
    if not weeks:
        sys.exit("Every file parsed, none held a hunk.")

    total = sum(len(w["hunks"]) for w in weeks)
    chars = sum(h["chars"] for w in weeks for h in w["hunks"])
    notes = {h["note"] for w in weeks for h in w["hunks"]}
    kinds = {k: v for k, v in KINDS.items()
             if any(h["kind"] == k for w in weeks for h in w["hunks"])}

    # Both pages want the same squares, so they are counted once, here, and carried on the hunk.
    per_square = args.chars_per_square or quantum(weeks, args.per_row)
    for w in weeks:
        for h in w["hunks"]:
            h["squares"] = squares(h["chars"], per_square)

    meta = (f"{total:,} hunks · {chars:,} chars · {len(notes):,} notes · {len(weeks)} weeks · "
            f"{weeks[0]['week']} → {weeks[-1]['week']}")

    if args.mosaic:
        html = mosaic(weeks, kinds, meta, args.cdn, args.per_row, per_square, args.view)
        out = args.out or SESSIONS / "microlite-waffle-mosaic.html"
        out.write_text(html)
        print(f"{total:,} hunks · {len(weeks)} weeks · one square = {per_square:,} chars "
              f"· {out.stat().st_size / 1e6:.1f} MB · vgplot from {args.cdn}")
        print(f"\n  open  {link(out)}")
        return

    html = SHELL
    for token, value in (
        ("__TITLE__", "hunks — obsidian, week by week"),
        ("__STYLE__", STYLE + "\n" + palette_css()),
        ("__EXTRA_STYLE__", EXTRA_STYLE + "\n" + DETAIL_STYLE),
        ("__FRAMED__", FRAMED),
        ("__HEADING__", "Hunks"),
        ("__META__", meta),
        ("__COUNT__", "one square is one hunk"),
    ):
        html = html.replace(token, value)
    # Last, and only once: a note whose text contains __META__ is data, not a template.
    html = html.replace("__DATA__", embed({
        # Spelled out here rather than in the page: `_hunks` hands over a three-letter
        # abbreviation when it has one at all, and which of those a reader wants is a question
        # about the label and not about the file.
        "weeks": [{**w, "weekday": weekday(w)} for w in weeks],
        "cell": args.cell, "gap": args.gap,
        "perRow": args.per_row, "kinds": kinds,
        "charsPer": per_square, "view": args.view,
    }))

    out = args.out or SESSIONS / "microlite-waffle.html"
    out.write_text(html)
    print(f"{total:,} hunks · {chars:,} chars · {len(notes):,} notes · {len(weeks)} weeks "
          f"· one square = {per_square:,} chars · {out.stat().st_size / 1e6:.1f} MB")
    print(f"\n  open  {link(out)}")


if __name__ == "__main__":
    main()
