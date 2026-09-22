#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
connection_waffle — the week's hunks, with the connections a model found drawn between them.

`microlite_waffle.py --mosaic` draws one square per hunk and lets you read the diff behind it.
Every square there is an island: the vault has no links in it, the writer tags nothing, and two
edits made a fortnight apart in notes that share no word look exactly as unrelated as two edits
that are about the same thing. That is the honest picture of the raw material, and it is also
the thing worth arguing with.

This page adds the arrows. `hunk_connections.py` reads the week's letter back against the hunks
it was written from and reports which pairs a sentence joined; each of those becomes one arrow
here. Nothing in the vault produced these links — no tag, no backlink, no folder — which is the
whole point.

The week's own sources are units too. The letter reads a note against the body and against the
ledger constantly — a night's sleep figure set beside what was written that day — and while only
hunks could be named, a sentence like that had to be dropped or pinned to a hunk that was half of
what it meant. The
sleep report and the ledger get a square each, stacked in the column of the week they belong to
and ordered by kind like every other square. Only the colour sets them apart, which is the one
thing about them worth setting apart.

The arrow is the only thing that answers a click, because the arrow is the only thing that
carries a reason. There is one piece of state on this page — the connection — so the view is
never a unit sitting on its own: what is on screen is always a pair the letter joined, the
sentences that joined them, and both ends. A clicked arrow keeps the weight it took under the
pointer, so the chart says which connection the panels are describing. Squares are inert and are
all drawn alike until an arrow is picked: an outline always means "this is one end of what you
are reading", and never anything else.

Each end of a connection carries its own rationale, shown under the name of the unit it is
about. One rationale for the pair kept coming back vague about which half was which, so the
schema asks for one about each end and the panel never makes you work out which is which.

Every connection carries a strength from 1 to 5, and the slider above the chart shows only
those at or above it — 5 by default, which is the handful the letter names unmistakably. An arc
is as strong as the best sentence behind it. The slider is a point selection over a column
expanded once per threshold an arc clears, because Mosaic's slider can publish an equality or an
interval bounded from above, and neither of those says "at least this strong".

One arrow is one connected pair, not one sentence. Two sentences can say the same two things
belong together, and drawing that arrow twice only makes it look darker for a reason no reader
could guess; the panel lists every sentence that made the link.

Both, always, and keyed by hunk rather than by note. The two ends are often the same note on two
different dates — a journal touched twice, a checklist revised a week apart — and that is not a
duplicate to be collapsed: it is the connection saying the writer came back to the same page
with something new, which is the most legible kind of connection this page can show.

Arrows carry no head. With only two ends and a rationale naming both, a head asserted a
direction the connection does not have. They thicken under the pointer instead, which is both
the affordance and the way to pick one arrow out of a knot of them.

A black frame marks the week whose letter was read, a pixel heavier than the outline on a square
and clearing the squares by a fixed number of pixels on every edge — pixels and not data units,
because a unit is not the same size across x and y and even whitespace is a claim about the page
rather than about the data. Arrows leave that frame and land in earlier weeks, which is the one
thing this whole page exists to show.

WHY THE FACETS HAD TO GO. `--mosaic` puts each week in its own `fx` facet, and a Plot mark is
rendered once per facet, so a mark cannot span two of them: a faceted chart can draw an arrow
inside a week and can never draw one between weeks. Since the connections worth showing are
exactly the ones that reach across weeks, the facet is what gives. The weeks are laid out on one
continuous x scale instead, each week's columns offset by `--week-gap`, and the x axis is
labelled at the middle of each block — so a week still reads as a block, and an arrow is just a
line between two points in one coordinate space.

Everything is one mark per row, because everything here is clickable: `rect` for hunks, `arrow`
for connections — Plot's own arrow mark, which vgplot already exposes, one `<path>` per datum.

Modular on purpose. The page reads `conns` as an opaque set of (from, to, kind, and some prose),
so a later view that connects hunks by extracted topic rather than by a letter's sentences is a
different `conns` table and a different colour domain, not a different page.

Usage:
    ./connection_waffle.py                          # newest connections-*.json
    ./connection_waffle.py --week 2026-06-06
    ./connection_waffle.py --connections path.json --out ~/connections.html
"""

from __future__ import annotations
import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

from _env import REPO_ROOT
from _hunks import by_id, load, weekday
from _page import FRAMED, SERIES_REST, STYLE, embed, palette_css
from _series import BY_NAME, DEFAULT, PALETTES
from _term import link

MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")


def pretty(iso: str) -> str:
    """`2026-06-06` or `11-13` → `September 5` / `November 13`.

    These pages are read inside one year and the year was always noise here; the synthetic
    sessions now drop it before it reaches disk, so both shapes arrive and both mean a day.
    """
    parts = iso.split("-")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return iso
    month, day = parts[-2:]
    return f"{MONTHS[int(month) - 1]} {int(day)}"

SESSIONS = Path(os.environ.get("PETROGRAPH_OUT") or REPO_ROOT).expanduser().resolve() / "sessions"

# What an arrow weighs when pointed at or picked. Written here because both the stylesheet and
# the chart script need it and they must not disagree — the weight a hover promises is the
# weight a click keeps.
HOVER_WIDTH = 7.5

# The outline on a square, and the thinnest line the page draws. The stylesheet borrows it for
# the divider between one end of a connection and the next, so the two cannot drift apart.
UNIT_STROKE = 1

CDN = "https://cdn.jsdelivr.net/npm/@uwdata/vgplot@0.31.0/+esm"

# Reusing microlite_waffle's four, so a square means the same thing on both pages.
KINDS = {
    "added":   {"hex": "#b6ff3c", "note": "lines only added"},
    "mixed":   {"hex": "#4de1ff", "note": "added and removed"},
    "removed": {"hex": "#ff6ec7", "note": "lines only removed"},
    "full":    {"hex": "#f4f1e4", "note": "whole note, no diff to hunk"},
    # Not a hunk and not pretending to be one: the week's sleep report and its ledger, which
    # the letter reads a note against constantly. Yellow because it is the one palette colour
    # no hunk uses, so a source never reads as an edit.
    "context": {"hex": "#ffe500", "note": "the week's own sources — sleep, spending"},
}

# Topic is the other axis, and it needs a categorical scale rather than the page's own six.
# The set is `_page.SERIES_CATEGORICAL` — jaan.io's `ghibli-master-light`, in its order of use,
# with the connector's slot lifted out — and the order is the point: a hue follows a topic and
# never its rank, so a colour means one subject for the life of the page. The tenth is the last
# slot; an eleventh topic is not given a generated hue but folds into a neutral, because the
# absence of an identity is not another one.
TOPIC_HUES = list(BY_NAME[DEFAULT]["topics"])
OTHER_HUE = SERIES_REST
OTHER = "other"

# One colour for every arrow, and it is the slot the categorical set holds back — see
# `_page.SERIES_LINK`, where which slot is a measurement and not a preference. No topic can wear
# it, so a line in this colour is never read as a square, and it sits further from its nearest
# topic hue than any other slot could while still holding 6.57:1 against the surface. It was two
# hues once, violet within a week and orange across, and that spent two slots on a distinction
# the panel already makes in words. Kept as a mapping so the two remain nameable in a badge.
ARROW_HUE = BY_NAME[DEFAULT]["link"]
CONN_KINDS = {
    "intra": {"note": "within one week"},
    "inter": {"note": "across weeks"},
}


EXTRA_STYLE = """  /* Chart left at its own width, the two reading panels stacked on the right. */
  /* The plot is 900 wide; the panel adds 8 of border and 32 of padding. */
  .layout { grid-template-columns: 944px minmax(0, 1fr); align-items: start; }
  @media (max-width: 1400px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  /* Flat panels. The shadow and the four-pixel rule were the loudest things on a page whose
     job is to show a chart; two pixels and no lift is enough to say "this is a panel". */
  .panel { box-shadow: none; border-width: 2px; }
  /* Framed (FRAMED in _page.py), the post says what the arrows are, and the connection sits
     beside the chart as soon as there is room — the chart narrows to make it; see chartWidth. */
  .embedded .note, .embedded #topics-panel, .embedded .panel:has(#conn) > h2 { display: none; }
  .embedded .chart .legend { text-transform: capitalize; }
  /* Framed, a caption centred over the plot (align() gives it the plot's width): the prompt, and
     once an arrow is picked, Claude's sentence in its place. It is as tall as the tallest thing it
     can hold (sizeExcerpt), with what it holds set at its top, so nothing under it ever moves;
     and its top is level with the first hunk's name in the panel beside it (align), so the
     quotation and the hunks start on one line. The prompt is centred across; a sentence is set
     left, in ink, under its label, with the quotation's rule. */
  .caption { display: none; }
  .embedded .caption { display: flex; flex-direction: column; justify-content: flex-start;
                       text-align: center; font-size: 13px; line-height: 1.5;
                       font-family: system-ui, sans-serif;
                       min-height: var(--caption-h, 0px); }
  .embedded .caption .excerpt { text-align: left; color: var(--ink); }
  .embedded .caption .said-from { margin: 0 0 4px; }
  /* No rule between the ends: the space and the names already part them. */
  .embedded .end { border-top: 0; }
  /* Two hunks open at once here, so each box is half the waffle's square (FRAMED in _page.py):
     as wide, half as tall, still scrolling. Scoped to the panel to outrank the shared rule. */
  .embedded #conn .hunk { aspect-ratio: 2; }
  @media (min-width: 960px) {
    /* The chart's own width, set by mount() as the waffle sets it: left to `auto`, the legend's
       one long line took the column wide and squeezed the panel, so a hunk here was drawn
       smaller than the same hunk in the waffle above it. */
    .embedded .layout { grid-template-columns: calc(var(--chart-w, 550px) + 32px) minmax(340px, 1fr); }
  }
  .panel > h2, .sub { border-bottom-width: 2px; }
  .sub { border-top-width: 2px; }

  .chart { padding: 16px 16px 8px; overflow-x: auto; }
  /* Plot paints its own ground white unless told otherwise; the palette owns it here. */
  .chart .plot, .chart figure { --plot-background: var(--card); }

  /* Half-strength units. The squares are the field the connectors are read against, and at
     full weight a five-pixel line laid over them is one more thing in a crowd rather than the
     thing on top of it. The legend's swatches drop with them so the key still matches the
     chart; the words stay at full strength, since fading them helps nothing. */
  .chart svg g[aria-label="rect"] rect { fill-opacity: 0.5; }
  .chart .legend svg rect, .chart .legend svg path { opacity: 0.5; }
  .trow i { opacity: 0.5; }

  /* Mosaic wraps a plot in `div.plot` and sets `display: flex` on it INLINE, and the legend is
     a sibling of the chart rather than part of it. One nowrap flex line, so the legend stood
     beside the chart and flex-shrank it to 72% of the size it was asked for — every square,
     tick and label scaled down with it. `display: block` from a stylesheet cannot win against
     an inline style, but `flex-wrap` was never set inline: let the line wrap and the chart
     takes the width it declared, with the legend under it. */
  .chart .plot { flex-wrap: wrap; }
  /* Plot sets 10px through a `:where()` rule, which carries no specificity, so naming the
     element at all is enough to raise it. Ten per cent, on the chart and on the legend that
     is its sibling rather than part of it. */
  .chart .plot svg, .chart .plot .legend, .chart .plot .legend svg { font-size: 11px; }

  /* Only the arrows answer the pointer. Squares are inert, and at rest they are all drawn
     alike: nothing is outlined until an arrow is picked, so the heavier border always means
     "this is one end of the connection you are reading" and never anything else. Because the arrow is the only thing on this chart that carries a
     reason, and a square that highlights under the pointer promises a click it will not honour.
     Addressed by `data-index`, which is the mark's own position: 0 every hunk, 1 the week's
     frame, 2 the arrows, 3 the ends, 4 the picked arrow again. `className` would read better and is not available — Mosaic reads a string mark
     option as a column reference, so it becomes a SQL error rather than a class. Kept as CSS
     rather than classes applied after render, so it survives Plot re-rendering a mark. */
  .chart g[aria-label="rect"] rect { cursor: default; }
  /* The ring and the lit arrow ease in AND out. A transition and not an animation, which is
     only possible because neither mark is filtered by the selection: `highlight` restyles the
     nodes that are already there, so there is a before and an after for the browser to
     interpolate between. Filtered, they were replaced on every click and only an animation
     could run — one that fired on the way in and never on the way out.

     Scoped to these two marks by index, deliberately. A rule broad enough to catch every
     non-unit mark also caught the week's frame, and since that was rebuilt on every click too,
     it flashed. Nothing about the frame should move when a connector is clicked.

     Same curve and duration as jaan.io's own tokens. */
  .chart g[data-index="3"] rect,
  .chart g[data-index="4"] path {
    transition: stroke-opacity var(--dur-fast) var(--ease-standard);
  }
  .chart g[data-index="2"] path {
    transition: stroke-width var(--dur-fast) var(--ease-standard),
                stroke-opacity var(--dur-fast) var(--ease-standard);
  }
  .chart g[data-index="2"] path { cursor: pointer; }
  .chart g[data-index="2"] path:hover { stroke-opacity: 1; stroke-width: __HOVER_WIDTH__px; }

  /* The redrawn selected arrow sits on top of the real one. Letting it swallow the pointer
     would make the selected arrow the one arrow you cannot hover or click, so it is invisible
     to the mouse and every event still reaches the mark that owns the toggle. Matched by "an
     arrow group that is not the interactive one", which stays true wherever it lands. */
  .chart g[aria-label="arrow"]:not([data-index="2"]) path { pointer-events: none; }
  /* The ring is drawn over the arrows, and must not take a click meant for one. */
  .chart g[data-index="3"] rect { pointer-events: none; }
  /* `highlight` hides the unpicked by writing stroke-opacity="0" on them, and "restores" the
     picked by writing back the channel value they never had, the string "null". Chrome drops
     that as invalid and inherits 1; not every browser need agree. Anything not hidden is solid,
     so the ring is the same ink as the week's frame and the axes. */
  .chart g[data-index="3"] rect:not([stroke-opacity="0"]),
  .chart g[data-index="4"] path:not([stroke-opacity="0"]) { stroke-opacity: 1; }
  .note { padding: 0 16px 16px; font-size: 12px; max-width: 78ch; }
  .note code { background: var(--paper); border: 2px solid var(--ink); padding: 0 4px; }
  #palette-row { padding: 12px 16px 0; flex-direction: row; align-items: center; gap: 10px;
                 flex-wrap: wrap; }
  #palette-row label { font-size: 13px; display: flex; align-items: center; gap: 8px;
                       max-width: 100%; min-width: 0; }
  #palette-row select { width: auto; max-width: 100%; min-width: 0; border-width: 2px; }
  /* A select is as wide as its longest option, and `max-width: 100%` cannot help when the
     percentage resolves against a box the select is itself sizing. On a phone the answer is the
     ordinary one for a form control: give it the line. */
  @media (max-width: 560px) {
    #palette-row label { width: 100%; }
    #palette-row select { width: 100%; }
  }

  /* The topic table. Legend and table view at once: the swatch carries identity, the word and
     the count carry it again for anyone the hue does not reach. */
  .sub {
    font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
    padding: 8px 12px; border-top: 4px solid var(--ink); border-bottom: 4px solid var(--ink);
    background: var(--cyan);
  }
  .topics { padding: 10px 16px 16px; columns: 2; column-gap: 28px; }
  .trow {
    display: flex; align-items: center; gap: 8px; padding: 3px 0;
    break-inside: avoid; font-size: 13px;
  }
  .trow i { width: 14px; height: 14px; border: 2px solid var(--ink); flex: none; }
  .trow .tname { flex: 1; }
  .trow .tcount { font-variant-numeric: tabular-nums; }

  /* ── Everything below spends no colour ──────────────────────────────────────────────────
     The plot is the only thing on this page that means anything by hue: a square is its topic
     and a line is a connection. So the chrome gives its colour up. The shared style paints a
     yellow header, cyan panel heads and badges in four of the palette's six; here they are ink
     and paper, and the page reads as one chart with a page around it rather than as a chart
     competing with one. The only hue outside the plot is the swatch in the topic table, which
     is the plot's own colour quoted back. */
  header { background: var(--card); }
  .panel > h2, .sub { background: var(--paper); }
  input[type=search]:focus, select:focus { outline: 4px solid var(--ink); }
  /* The slider paints itself in the browser's accent — the one colour a stylesheet forgets. */
  .chart, .chart input[type=range] { accent-color: var(--ink); }

  .badge.added, .badge.mixed, .badge.removed, .badge.full { background: var(--paper); }
  /* No hue on these either: within a week or across it is a word, not a colour. */
  .badge.intra { background: var(--paper); }
  .badge.inter { background: var(--ink); color: var(--card); }
  /* Strength as a shade rather than a number to squint at: the darker the badge, the firmer
     the tie. Five is the only one that gets ink behind it. */
  .badge.s1 { background: #ffffff; }
  .badge.s2 { background: #f0eee6; }
  .badge.s3 { background: #ded9c8; }
  .badge.s4 { background: #b4b4b4; }
  .badge.s5 { background: #111111; color: #ffffff; }

  /* The sentence is evidence, so it is set as a quotation rather than as prose. */
  .said { border-left: 6px solid var(--ink); margin: 0; padding: 2px 0 2px 12px;
          font-size: 15px; line-height: 1.5; }
  /* Whose words these are, over the quote: the letter is Claude's, not the writer's. */
  .said-from { margin: 14px 0 4px; font-size: 13px; letter-spacing: 0.06em;
               font-variant-caps: all-small-caps; }
  .why { margin: 12px 0 0; font-size: 13px; line-height: 1.55; }
  /* The unit a rationale is about, named ahead of it, so no sentence has to be read twice to
     work out which half it describes. */
  .why .who {
    display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
    font-weight: 700; margin-bottom: 2px; overflow-wrap: anywhere;
  }
  /* The pager, when an arrow carries more than one sentence. Kept quiet: this is a way through
     the alternatives, not a thing to look at. It earns its place on the right of the badge row
     because that row is already the connection's own header. */
  .pager { margin-left: auto; display: flex; align-items: center; gap: 2px; }
  .pager button {
    font: inherit; line-height: 0; cursor: pointer;
    background: none; border: 0; padding: 0 4px;
  }
  .pager button:focus-visible { outline: 3px solid var(--ink); outline-offset: 2px; }
  .pager .of { font-size: 11px; font-variant-numeric: tabular-nums; min-width: 3ch;
               text-align: center; }

  /* One end of the connection: who it is, why it is here, and the hunk itself folded away
     behind the caret. Both ends are always drawn, even when they are the same note on two
     different dates — that repetition is the connection's point, not a duplicate. */
  /* One hairline between ends, the same weight as the outline on a square in the chart. The
     page's heavy rules are structure — a panel, a heading, the frame around the annotated week
     — and a divider inside one box is not structure, it is a pause. */
  .end { margin-top: 18px; padding-top: 16px; border-top: __UNIT_STROKE__px solid var(--ink); }
  .end summary {
    cursor: pointer; list-style: none; display: grid;
    grid-template-columns: 26px minmax(0, 1fr); column-gap: 6px;
  }
  .end summary::-webkit-details-marker { display: none; }
  /* The caret, drawn rather than inherited, so it can sit on the first line of a two-line
     summary and turn instead of being swapped for another glyph. */
  /* One triangle for every direction on this page: the caret that opens a hunk, and the two
     that step through the sentences behind an arrow. Same character, same weight, turned — so a
     reader learns the shape once and it goes on meaning "there is more this way". Full ink and
     big enough to aim at: these are the only controls inside the box. */
  .end summary::before, .pager button::before {
    content: "\\25b8";
    font-size: 23px; line-height: 0.85; font-weight: 700; color: var(--ink);
    transition: transform var(--dur-fast) var(--ease-standard);
  }
  .end summary::before { grid-column: 1; grid-row: 1 / span 2; align-self: start; }
  .end[open] summary::before { transform: rotate(90deg); }
  /* `display` because a transform does nothing to an inline box, and the grid item above is
     blockified already so it needs no help. */
  .pager button::before { display: block; }
  /* Mirrored rather than turned. U+25B8 does not sit centred in its em box, so rotating it
     half a turn moves the ink as well as pointing it the other way, and the two buttons ended
     up a pixel and a half out of line with each other. A flip in x points it left and leaves
     every vertical measurement alone. */
  .pager .prev::before { transform: scaleX(-1); }
  .end summary > * { grid-column: 2; }
  .end .who {
    font-size: 12px; font-weight: 700; margin-bottom: 3px; overflow-wrap: anywhere;
  }
  .end .who > span { display: block; }
  .end .why { font-size: 13px; line-height: 1.55; }
  .end .facts { margin-left: 32px; }
  .end .mention { margin-left: 32px; }

  /* Every word on this page is the same ink. The plot is the only thing here that means
     anything by colour — a square is its topic, a line is a connection — so type that is paler
     than other type reads as a third encoding that does not exist. Plot draws its axis text in
     its own grey by default; this takes it back. Size is still allowed to say which of two
     lines is the subordinate one. */
  .chart svg text, .chart svg text tspan { fill: var(--ink); fill-opacity: 1; }
  /* The day above the date, from the newline Plot splits into tspans for us. */

  .hunk { border: 3px solid var(--ink); margin-top: 14px; background: var(--card); }
  .hunk pre {
    margin: 0; padding: 12px; max-height: 34vh; overflow: auto; background: var(--card);
    font: 13px/1.55 Hack, ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .hunk code { display: block; white-space: pre; }
  .hunk code > span { display: block; padding: 0 6px; margin: 0 -6px; }
  /* A diff in the colours a diff is read in, and the same ones the waffle's hunk panel uses
     (DETAIL_STYLE in microlite_waffle.py), so a hunk looks alike wherever it is opened. Framed
     under a dark palette, both are re-inked by FRAMED in _page.py. */
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
    <h2>__COUNT__</h2>
    <div class="controls" id="palette-row">
      <label>Palette
        <select id="palette"></select>
      </label>
    </div>
    <div class="caption" id="caption">Click an arrow to read why these two edits belong together.</div>
    <div class="chart" id="view">loading duckdb-wasm&hellip;</div>
    <p class="note">__NOTE__</p>
    <div id="topics-panel" hidden>
      <h2 class="sub">Topics</h2>
      <div class="topics" id="topics"></div>
    </div>
  </section>

  <div>
    <section class="panel">
      <h2>Connection</h2>
      <div class="detail" id="conn">
        <div class="empty">Click an arrow to read why these two edits belong together.</div>
      </div>
    </section>
  </div>
</div>

<script type="application/json" id="data">__DATA__</script>
<script type="module">
import * as vg from "__CDN__";

const { hunks, conns, kinds, colors, perRow, weekGap: drawnGap, weeks, weekdays,
        annotatedWeek, annotatedSlot, colorBy, topics, topicCounts, palettes, palette } =
  JSON.parse(document.getElementById("data").textContent);
// Framed, one empty column between weeks rather than --week-gap's three, so the chart gives up
// width to the panel beside it without its squares shrinking (chartWidth). The waffle framed above
// it draws the same (WEEK_GAP in microlite_waffle.py). FRAMED has run by now: a module waits.
const weekGap = document.documentElement.classList.contains("embedded") ? 1 : drawnGap;

vg.coordinator().databaseConnector(new vg.DuckDBWASMConnector());

// DuckDB lays the waffle out, exactly as it does on the faceted page — but into one continuous
// x, because an arrow has to be able to leave its week. `slot` is the week's position, so a
// week occupies `perRow` columns and then `weekGap` empty ones.
//
// `//` and not `/`: DuckDB's `/` is float division and a cast rounds rather than truncates,
// which hands the bottom row too few cells and shifts every row above it.
await vg.coordinator().exec([
  vg.loadObjects("hunks_raw", hunks),
  vg.loadObjects("conns_raw", conns),
  `CREATE OR REPLACE TABLE hunks AS
     SELECT * EXCLUDE (n),
            slot * ${perRow + weekGap} + ((n - 1) % ${perRow})::INTEGER AS x,
            ((n - 1) // ${perRow})::INTEGER                             AS y
     FROM (SELECT *, row_number() OVER (PARTITION BY slot ORDER BY rank, ord) AS n
           FROM hunks_raw)`,
  // An arrow needs the two squares' positions, which only exist after the layout above. The
  // join is what turns a pair of ids into a pair of points; a connection whose ids did not
  // resolve was already dropped upstream, so an inner join cannot silently lose one here.
  `CREATE OR REPLACE TABLE conns AS
     SELECT c.*,
            -- The arc's key, and it is UNORDERED. A sentence saying A belongs with B and one
            -- saying B belongs with A describe the same tie, and with no arrowhead on either
            -- end they draw the same line twice — two strokes stacked, reading as one darker
            -- arc for a reason no one could guess. Sorting the two endpoints by position gives
            -- both rows the same key, so they group into one arc and one selection.
            --
            -- Only the drawing key is sorted. Which end is the from-end still lives in from_note
            -- and from_rationale, so the panel still says what the model said about which unit.
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN a.x + 0.5 ELSE b.x + 0.5 END AS cx1,
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN a.y + 0.5 ELSE b.y + 0.5 END AS cy1,
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN b.x + 0.5 ELSE a.x + 0.5 END AS cx2,
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN b.y + 0.5 ELSE a.y + 0.5 END AS cy2,
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN a.id ELSE b.id END AS id1,
            CASE WHEN a.x * 100 + a.y <= b.x * 100 + b.y
                 THEN b.id ELSE a.id END AS id2,
            a.note AS from_note, b.note AS to_note,
            a.week AS from_week, b.week AS to_week,
            -- The whole hunk on both sides. It used to live in a second panel with a query of
            -- its own; one box means one query, and the diff is three joins cheaper here than
            -- it was as a separate round trip keyed off the arc.
            a.head AS from_head, b.head AS to_head,
            a.text AS from_text, b.text AS to_text,
            a.kind AS from_kind, b.kind AS to_kind,
            a.topic AS from_topic, b.topic AS to_topic,
            a.adds AS from_adds, b.adds AS to_adds,
            a.dels AS from_dels, b.dels AS to_dels
     FROM conns_raw c
     JOIN hunks a ON a.id = c.from_id
     JOIN hunks b ON b.id = c.to_id`,
  // The two ends of every connection, one row each — the only table the right-hand side and
  // the clickable squares both need. It carries the end's own cell (so it can be drawn and
  // clicked), the arrow's coordinates (so a click can name the connection, and so the pinned
  // connection can filter it), and the whole hunk (so the panel can show both diffs without a
  // second round trip). `end_ix` keeps from before to.
  //
  // A hunk joined by two connections appears twice, which is what lets either be picked; and
  // both ends of one connection can be the same note on two different dates, which is exactly
  // why this is keyed by hunk and never collapsed by note.
  // Keyed off the arc's own two ends, not off from/to. Once both directions of a pair collapse
  // into one arc, "the from end" is no longer a property of the arc — two sentences can
  // disagree about which unit that is — and joining on it puts four hunks under a pair that
  // has two. The canonical first and second end are properties of the arc, and there are
  // exactly two of them however many sentences point along it.
  `CREATE OR REPLACE TABLE conn_ends AS
     SELECT DISTINCT 0 AS end_ix, c.cx1, c.cy1, c.cx2, c.cy2,
            h.x, h.y, h.note, h.week, h.kind, h.topic, h.head, h.text, h.adds, h.dels
       FROM conns c JOIN hunks h ON h.id = c.id1
     UNION
     SELECT DISTINCT 1, c.cx1, c.cy1, c.cx2, c.cy2,
            h.x, h.y, h.note, h.week, h.kind, h.topic, h.head, h.text, h.adds, h.dels
       FROM conns c JOIN hunks h ON h.id = c.id2`,
  // The frame around the week whose letter was read. Measured off the laid-out squares rather
  // than recomputed from slot arithmetic, so it stays true if the layout ever changes; one row,
  // always present, which is what keeps it from disturbing anyone's rendered mark index.
  `CREATE OR REPLACE TABLE week_box AS
     SELECT MIN(x) AS bx1, MAX(x) + 1 AS bx2,
            MIN(y) AS by1, MAX(y) + 1 AS by2
     FROM hunks WHERE slot = ${annotatedSlot}`,
  // One arc per connected pair, not one per sentence. Two sentences can say the same two
  // things belong together, and drawing that arrow twice makes it look darker than its
  // neighbours for a reason no reader could guess. The panel still shows every sentence —
  // the selection is a pair of points, so it finds all of them. An arc is as strong as the
  // best sentence behind it, which is why the strength is a MAX and not an average: one
  // unmistakable link is not weakened by a second, looser reading of the same pair.
  `CREATE OR REPLACE TABLE conn_arcs AS
     SELECT cx1, cy1, cx2, cy2, kind, MAX(strength) AS strength
     FROM conns GROUP BY cx1, cy1, cx2, cy2, kind`,
  // The same arcs, once per threshold they clear. Mosaic's slider publishes an equality on a
  // column, and an interval one bounded from ABOVE — neither of which says "at least this
  // strong". Expanding the rows so that `threshold = 4` already means "strength 4 or better"
  // turns the question the slider can ask into the question worth asking, at the cost of at
  // most five rows per arc.
  `CREATE OR REPLACE TABLE conn_arcs_at AS
     SELECT a.*, t.threshold
     FROM conn_arcs a, (SELECT UNNEST([1, 2, 3, 4, 5]) AS threshold) t
     WHERE t.threshold <= a.strength`
]);

// A square is inset from its cell by this much on every side, so the gap between two
// neighbouring squares is twice it. The frame around the annotated week clears the squares by
// FRAME_GAP on every edge — in pixels, because a data unit is not the same size across x and y
// and "the same whitespace on each edge" is a statement about the page, not about the data.
// Plot's `inset` is pixels and a negative one grows the rect, so the frame's inset is the
// square's inset minus the gap, and the relationship is stated here rather than in two places.
const CELL_INSET = 2;
const FRAME_GAP = 7;
// A unit's own outline, and the ring drawn on the two squares of the selected arc.
//
// The ring is sized from the other two rather than chosen. Measuring inward from a cell's
// boundary: a unit's outline is a 1px stroke centred on a path CELL_INSET in, so it spans
// 1.5 to 2.5. The ring has to reach the inner edge of that outline on its own square — 2.5 in
// — and, going the other way, the inner edge of the neighbouring square's outline, which is
// 2.5 the far side of the shared boundary. That is a band from -2.5 to +2.5: five pixels wide,
// centred exactly on the cell boundary, which is why its inset is zero. It swallows the gap
// and both outlines that face into it, so a selected square reads as one solid block of ink
// rather than a ring floating in a moat.
const UNIT_STROKE = __UNIT_STROKE__;
const OUTLINE_WIDTH = 2 * CELL_INSET + UNIT_STROKE;
// What an arrow weighs when it is being pointed at or has been picked. The CSS hover rule is
// generated from the same number, so the weight a hover promises is the weight a click keeps.
const ARROW_WIDTH = 5;
const HOVER_WIDTH = __HOVER_WIDTH__;

// What a square's fill means, decided once. In topic mode the layout is already sorted by
// topic rank, so the scale's domain order and the column order are the same order.
// Dates as a reader says them. Split rather than parsed through `Date`: a bare ISO day string
// parses as UTC midnight, and formatting that anywhere west of Greenwich shows the day before.
const MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];
const pretty = (iso) => {
  // Spelled out rather than with the digit shorthand: this JS lives inside a Python
  // string, where a backslash-d is an invalid escape.
  // A real session's week is yyyy-mm-dd; a synthetic one's is mm-dd, because those folders
  // drop the year before they reach disk. Both are a day, and the year was never printed.
  const m = /^(?:[0-9]{4}-)?([0-9]{2})-([0-9]{2})$/.exec(iso ?? "");
  return m ? `${MONTHS[+m[1] - 1]} ${+m[2]}` : (iso ?? "");
};

// The spelled-out day for a week, decided in Python — see `weekday()` there. A week named
// `11-13` has no year in it and therefore no weekday anybody can compute, so it is handed over
// rather than derived. The axis tick format puts one above each date, separated by a newline,
// which Plot renders as two tspans on its own — checked, rather than assumed and then
// hand-rolled: an earlier version of this file carried a MutationObserver that split the label
// itself and never once had anything to split.
const dayName = (w) => weekdays?.[w] ?? "";

// Framed, too: the post's waffle above this one says what each edit did, and this one says what
// it was about, in the same palette's hues from its first slot where the waffle's kinds start
// three along (--kind-* in microlite_waffle.py).
const byTopic = colorBy === "topic";
// The palette in force. Its hues are assigned to topics in the stored topic order, so a hue
// follows a subject and not its rank; `other` keeps its grey in every palette, because the
// absence of an identity should not change colour when the identities do.
// `data-palette` may already be set — by the host page, when this one is framed (FRAMED in
// _page.py) — and then that is the palette in force rather than the stored default.
const named = (n) => palettes.find(p => p.name === n);
let pal = named(document.documentElement.dataset.palette) ?? named(palette) ?? palettes[0];
const hueFor = (t) => {
  const i = topics.indexOf(t);
  return i < 0 || i >= pal.topics.length ? OTHER_HUE : pal.topics[i];
};
const OTHER_HUE = "__OTHER_HUE__";
const fillField = byTopic ? "topic" : "kind";
const unitDomain = byTopic ? topics : kinds;
// The waffle's slots for each kind (--kind-* in microlite_waffle.py), plus one more along for
// the week's sources, which only this chart has. Read off the palette at draw time, so a
// palette change, which redraws, picks up the new values.
const KIND_SLOTS = { added: "--series-4", mixed: "--series-5", removed: "--series-6",
                     context: "--series-7", full: "--series-rest" };
const unitRange = () => byTopic ? topics.map(hueFor)
  : embedded ? kinds.map(k => getComputedStyle(document.documentElement)
                                .getPropertyValue(KIND_SLOTS[k] ?? "--series-rest").trim())
  : colors;

const $conn = vg.Selection.single();

/**
 * A selection that only ever moves forward: a real pick pins, a clear is ignored.
 *
 * Mosaic's Toggle clears its selection on ANY pointerdown outside its own mark, and two
 * toggles publish here — a square and an arrow both name a connection. Whichever listener runs
 * second would otherwise wipe what the first just set, so clicking a square would clear the
 * very connection it was meant to select. Everything downstream reads the pinned copy instead.
 *
 * `{ empty: true }` because these also filter marks: with the default resolution an empty
 * selection filters nothing, and the endpoint outlines would be drawn for every connection at
 * once before anyone had clicked anything. Here an empty selection matches no rows, which is
 * what "nothing is picked" should mean.
 */
function pin(source) {
  const target = vg.Selection.single({ empty: true });
  source.addEventListener("value", () => {
    const [clause] = source.clauses;
    if (clause?.predicate) target.update(clause);
  });
  return target;
}
const $connPin = pin($conn);

// The threshold, as a selection rather than a plain param, because a mark filters by one.
// The slider publishes its starting value on construction, so the chart opens already filtered
// rather than showing every arc for one frame.
const $strength = vg.Selection.single();
// Where the slider opens, and so which arrows a framed page ever shows: its slider is hidden.
const SHOWN_AT = 5;
const strengthSlider = vg.slider({
  as: $strength, field: "threshold", label: "Min strength",
  min: 1, max: 5, step: 1, value: SHOWN_AT
});

// Week names live under the middle of each block. A text mark would need its own row in the
// y domain; ticking the x axis at the midpoints keeps the label on the axis where it belongs.
const midpoints = weeks.map((w, i) => i * (perRow + weekGap) + perRow / 2);
const blockAt = new Map(midpoints.map((m, i) => [m, weeks[i]]));

// On its own page the chart keeps the 900 it was laid out at. Framed, it gives up width to the
// connection panel beside it (the 960px breakpoint in EXTRA_STYLE, less that panel's 340, the
// gap and the chart's own padding), or fills the frame once the two are stacked; the height
// follows so a square keeps its shape.
const embedded = document.documentElement.classList.contains("embedded");
// Beside the panel, a square at 0.83 of the size it had filling that room with three empty columns
// between weeks, and weekGap's one between them now: the chart is that much narrower, the panel
// taking the rest. microlite_waffle.py's chartWidth is this, and must stay so.
// Sized from the frame's full width in the post, not the frame as it stands — see basisWidth in
// microlite_waffle.py: the panel gives up what the post takes back for its TOC rail.
const basisWidth = () => {
  const host = window.frameElement;
  return host ? Math.max(host.parentElement.clientWidth, 0.75 * parent.innerWidth) : innerWidth;
};
const chartWidth = () => {
  if (!embedded) return 900;
  if (innerWidth < 960) return Math.max(300, Math.min(900, innerWidth - 32));
  const unit = 0.83 * (Math.min(900, basisWidth() - 392) - 64) / (weeks.length * (perRow + 3) - 3 + 0.8);
  return Math.min(innerWidth - 392,
                  Math.round(64 + unit * (weeks.length * (perRow + weekGap) - weekGap + 0.8)));
};
// Square cells. x runs over X_UNITS data units and y over `tallest` rows, so the height that
// makes a unit as tall as it is wide follows from the width, less the fixed-pixel margins. The
// waffle framed above this in the post draws with the same numbers (microlite_waffle.py), which
// is what gives the two the same aspect.
const X_UNITS = weeks.length * (perRow + weekGap) - weekGap + 0.8;
const tallest = Math.max(1, ...weeks.map(w =>
  Math.ceil(hunks.filter(h => h.week === w).length / perRow)));
const chartHeight = () => Math.round(20 + 47 + tallest * (chartWidth() - 52 - 12) / X_UNITS);

const buildChart = () => vg.plot(
  // Every hunk, as the backdrop. No interactor: a square nothing connects to has nothing to
  // say beyond its own diff, and offering a click that leads nowhere is worse than not
  // offering one. These are drawn first so the clickable ends sit on top of them.
  vg.rect(vg.from("hunks"), {
    x1: "x", x2: vg.sql`x + 1`,
    y1: "y", y2: vg.sql`y + 1`,
    fill: fillField,
    // The deletion fade is a fact about the diff, so it stays out of the topic view.
    fillOpacity: byTopic ? 1 : vg.sql`CASE WHEN kind = 'removed' THEN 0.35 ELSE 1 END`,
    // Framed, no square is outlined until an arrow picks it: the ring below is the only border.
    inset: CELL_INSET, stroke: embedded ? "none" : "var(--ink)", strokeWidth: UNIT_STROKE
  }),
  // The frame around the week whose sentences these are: solid black, square-cornered, and a
  // pixel heavier than the outline on a square, so it reads as the boundary of the week rather
  // than as another unit. Second, over the squares and under every connector, since the arrows
  // leaving the week cross it. It can sit this early because it always renders — one row, see
  // week_box — so every later mark's rendered index is fixed, and the CSS addresses them by it.
  vg.rect(vg.from("week_box"), {
    x1: "bx1", x2: "bx2", y1: "by1", y2: "by2",
    // Black on its own page; framed, the palette's ink, since the ground may be dark.
    fill: "none", stroke: embedded ? "var(--ink)" : "#000000", strokeWidth: 2,
    inset: CELL_INSET - FRAME_GAP
  }),
  // Plot's own arrow mark, one <path> per connection. `headLength: 0` is how Plot documents
  // turning the head off: with only two ends and a rationale naming both, a head asserted a
  // direction the connection does not have.
  vg.arrow(vg.from("conn_arcs_at", { filterBy: $strength }), {
    x1: "cx1", y1: "cy1", x2: "cx2", y2: "cy2",
    // Framed, every arrow at half strength and the picked one, redrawn below, at full: the lit
    // arrow is the one the panel is reading, and nothing else competes with it.
    stroke: pal.link, strokeWidth: ARROW_WIDTH, strokeOpacity: embedded ? 0.5 : 0.9,
    // Plot's arrow mark caps its stroke round by default; flat here, so a connector ends
    // where it ends rather than in a bead.
    strokeLinecap: "butt",
    bend: 18, headLength: 0, inset: 5
  }),
  vg.toggle({ as: $conn, channels: ["x1", "y1", "x2", "y2"] }),
  // The ring on the two squares of the selected arc: over every other arrow, so the squares it
  // names are never hidden by a connector crossing them, and under the picked arrow's redraw
  // below, which is the one line allowed to cross it. It takes no pointer (see the CSS), so an
  // arrow under it is still clickable.
  //
  // Every connected end, always drawn, with `highlight` turning the unselected invisible — not
  // a `filterBy` that renders two and nothing else. Everything good here follows from that. A
  // mark whose DATA changes makes Plot rebuild the plot's whole SVG, so with a filter on the
  // selection every click replaced every node: the ring could only ever animate IN (its old
  // node was already gone), and the week's frame, rebuilt alongside, flashed as it re-ran the
  // same animation. Hidden by `highlight` instead, a selection sets attributes and nothing is
  // rebuilt — so the ring can TRANSITION both ways, the frame never moves, and no later mark's
  // rendered index shifts.
  vg.rect(vg.from("conn_ends"), {
    x1: "x", x2: vg.sql`x + 1`,
    y1: "y", y2: vg.sql`y + 1`,
    fill: "none", stroke: "var(--ink)", strokeWidth: OUTLINE_WIDTH,
    // The arc's own coordinates, named exactly as their columns are. `highlight` matches the
    // selection's clause against the columns a mark actually fetched, and a mark fetches only
    // what its channels name — under any other alias the clause finds nothing and every ring
    // stays dark.
    channels: { cx1: "cx1", cy1: "cy1", cx2: "cx2", cy2: "cy2" },
    // Centred on the cell boundary: see OUTLINE_WIDTH for why that is the whole of it.
    inset: 0
  }),
  vg.highlight({ by: $connPin, strokeOpacity: 0 }),
  // The arrow that is selected, drawn a second time at the weight it takes under the pointer.
  // `filterBy` means this mark is empty until something is picked and then holds exactly one
  // arrow, so the highlight the hover promised is the highlight that stays — the chart says
  // which connection the panels on the right are describing without dimming anything else.
  // Unfiltered for the same reason as the ring above.
  vg.arrow(vg.from("conn_arcs"), {
    x1: "cx1", y1: "cy1", x2: "cx2", y2: "cy2",
    stroke: pal.link, strokeWidth: HOVER_WIDTH, strokeOpacity: 1,
    // Plot's arrow mark caps its stroke round by default; flat here, so a connector ends
    // where it ends rather than in a bead.
    strokeLinecap: "butt",
    bend: 18, headLength: 0, inset: 5
  }),
  vg.highlight({ by: $connPin, strokeOpacity: 0 }),
  vg.colorDomain(unitDomain),
  vg.colorRange(unitRange()),
  vg.opacityScale("identity"),
  vg.colorLegend({ label: "" }),
  vg.xDomain([-0.4, weeks.length * (perRow + weekGap) - weekGap + 0.4]),
  vg.xTicks(midpoints),
  // Two lines, the day above the date. Plot turns the newline into two tspans itself.
  vg.xTickFormat(d => `${dayName(blockAt.get(d))}\\n${pretty(blockAt.get(d))}`),
  vg.xTickSize(0),
  vg.xLabel(null),
  vg.yLabel("Hunks"),
  vg.yTickFormat(d => d * perRow),
  vg.width(chartWidth()),
  vg.height(chartHeight()),
  vg.yDomain([0, tallest]),
  vg.yTicks(Array.from({ length: Math.floor(tallest / 2) + 1 }, (_, i) => 2 * i)),
  vg.marginTop(20),
  vg.marginRight(12),
  vg.marginLeft(52),
  vg.marginBottom(47)
);

// textContent everywhere below: note names, diff bodies, the letter's own sentence and the
// model's reasoning about it are all the writer's private prose, and prose is not markup.
function badge(text, cls) {
  const s = document.createElement("span");
  s.className = "badge " + (cls || "");
  s.textContent = text;
  return s;
}

function empty(el, text) {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = text;
  el.replaceChildren(d);
}

function diffBlock(row) {
  const box = document.createElement("div");
  box.className = "hunk";
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.className = "language-diff";
  // A source is markdown, not a patch. Colouring it as one would paint every bullet red,
  // because a list item and a deleted line both begin with a hyphen.
  const plain = row.kind === "context";
  for (const line of [row.head, ...(row.text ? row.text.split("\\n") : [])]) {
    if (plain && !line) continue;
    const span = document.createElement("span");
    span.className = plain ? "ln-c"
      : line.startsWith("@@") ? "ln-h"
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

/**
 * One connection at a time: the sentence, and both ends of what it joined.
 *
 * One box rather than two. It used to be a panel of sentences over a panel of diffs, and the
 * reader had to hold the pairing in their head — three sentences above, two hunks below, and no
 * line drawn between them. Here a connection is a unit: its quotation, then each end named and
 * explained, then the hunk itself behind a caret for anyone who wants to check the explanation
 * against the file. An arrow joined by several sentences is several of these, cycled through
 * rather than stacked, because they are alternatives and not a list.
 */
// Framed, the caption over the plot: the prompt until an arrow is picked, its sentence after.
const caption = document.getElementById("caption");
const PROMPT = caption.textContent;

// The quotation: whose words, then the words. `null` gives the box alone, for the prompt that
// stands in it before anything is picked; framed, the box's height is fixed (sizeExcerpt).
function excerpt(sentence) {
  const box = document.createElement("div");
  box.className = "excerpt";
  if (sentence === null) return box;
  const from = box.appendChild(document.createElement("div"));
  from.className = "said-from";
  from.textContent = "Large language models connect changes in notes across time";
  const said = box.appendChild(document.createElement("blockquote"));
  said.className = "said";
  said.textContent = sentence;
  return box;
}

// Framed, an arrow reads as one sentence: the strongest behind it, and among equals one picked
// by a hash of their text — arbitrary, but the same one every time that arrow is clicked.
function one(rows) {
  const best = Math.max(...rows.map(r => Number(r.strength)));
  const top = rows.filter(r => Number(r.strength) === best);
  if (top.length < 2) return top;
  let h = 2166136261;
  for (const ch of top.map(r => r.sentence).sort().join("\\n")) {
    h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  }
  return [top[(h >>> 0) % top.length]];
}

class ConnDetail extends vg.MosaicClient {
  constructor(selection, el) { super(selection); this.el = el; this.rows = []; this.ix = 0; }
  query(filter = []) {
    // No limit. Two sentences may join the same pair of hunks, and both are worth showing —
    // the selection is a pair of points, so it cannot tell them apart and should not have to.
    return vg.Query.from("conns")
      .select("kind", "strength", "sentence", "from_rationale", "to_rationale",
              "from_note", "to_note", "from_week", "to_week",
              "from_head", "to_head", "from_text", "to_text", "from_kind", "to_kind",
              "from_topic", "to_topic", "from_adds", "to_adds", "from_dels", "to_dels")
      // `vg.desc` is not exported from vgplot; an expression says the same thing.
      .orderby(vg.sql`strength DESC`)
      .where(filter?.length ? filter : vg.sql`false`);
  }
  queryResult(data) {
    this.rows = embedded ? one(Array.from(data)) : Array.from(data);
    // Back to the first one whenever the arrow changes. Keeping the index across selections
    // meant clicking a one-sentence arrow after a three-sentence one showed nothing at all.
    this.ix = 0;
    return this.draw();
  }
  draw() {
    if (!this.rows.length) {
      if (!embedded) {
        empty(this.el, "Click an arrow to read why these two edits belong together.");
        return this;
      }
      // Framed, the caption over the plot holds the prompt until an arrow is picked, and here an
      // invisible stand-in for the first end holds a name line where the name will be, so the
      // first filename's place — which the caption is set level with — is known before anything
      // is clicked and does not move after.
      caption.replaceChildren(PROMPT);
      const ghost = document.createElement("details");
      ghost.className = "end";
      ghost.style.visibility = "hidden";
      const who = ghost.appendChild(document.createElement("summary"))
                       .appendChild(document.createElement("span"));
      who.className = "who";
      who.appendChild(document.createElement("span")).textContent = "\\u00a0";
      this.el.replaceChildren(ghost);
      align();
      return this;
    }
    const row = this.rows[Math.min(this.ix, this.rows.length - 1)];
    const frag = document.createDocumentFragment();

    const facts = document.createElement("div");
    facts.className = "facts";
    // Framed, the post shows the strongest arrows only, so neither says anything there.
    if (!embedded) {
      facts.appendChild(badge(row.kind === "inter" ? "across weeks" : "within one week", row.kind));
      facts.appendChild(badge(`strength ${row.strength}/5`, `s${row.strength}`));
    }
    // Only when there is somewhere to go. A pager on a single connection is a control that
    // does nothing, which is worse than no control.
    if (this.rows.length > 1) {
      const pager = document.createElement("div");
      pager.className = "pager";
      const step = (by) => {
        this.ix = (this.ix + by + this.rows.length) % this.rows.length;
        this.draw();
      };
      // No text content: the glyph is the caret, drawn by CSS so that all three triangles on
      // the page are one declaration. The label is what a screen reader gets.
      const mk = (cls, label, by) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = cls;
        b.title = label;
        b.setAttribute("aria-label", label);
        b.addEventListener("click", () => step(by));
        return b;
      };
      pager.appendChild(mk("prev", "Previous sentence for this arrow", -1));
      const count = document.createElement("span");
      count.className = "of";
      count.textContent = `${this.ix + 1}/${this.rows.length}`;
      pager.appendChild(count);
      pager.appendChild(mk("next", "Next sentence for this arrow", 1));
      facts.appendChild(pager);
    }
    if (facts.childNodes.length) frag.appendChild(facts);

    // Framed, the sentence is the caption over the plot, in the prompt's place.
    if (embedded) caption.replaceChildren(excerpt(row.sentence));
    else frag.appendChild(excerpt(row.sentence));

    // One block per end, each under the name of the unit it is about. A single rationale for
    // the pair kept coming back vague about which half was which — a figure quoted without
    // saying which unit held it — so the two are asked for and shown apart. Both are always
    // drawn, even when they are the same note on two different dates: that repetition is the
    // connection's point, not a duplicate to be collapsed.
    const ends = [
      { note: row.from_note, week: row.from_week, why: row.from_rationale,
        head: row.from_head, text: row.from_text, kind: row.from_kind,
        topic: row.from_topic, adds: row.from_adds, dels: row.from_dels },
      { note: row.to_note, week: row.to_week, why: row.to_rationale,
        head: row.to_head, text: row.to_text, kind: row.to_kind,
        topic: row.to_topic, adds: row.to_adds, dels: row.to_dels },
    ];
    // Earliest file first, so the pair reads in the order it was written. Both calendars are
    // fixed-width (`2026-06-12` or `11-13`), so string order is date order; a tie keeps from
    // before to, since sort is stable.
    ends.sort((a, b) => (a.week < b.week ? -1 : a.week > b.week ? 1 : 0));
    for (const end of ends) {
      const box = document.createElement("details");
      box.className = "end";

      const sum = document.createElement("summary");
      const who = document.createElement("span");
      who.className = "who";
      // Name, day, topic: a line each, so none needs a separator.
      for (const line of [end.note, `${dayName(end.week)} ${pretty(end.week)}`,
                          ...(end.topic ? [`topic: ${end.topic}`] : [])]) {
        who.appendChild(document.createElement("span")).textContent = line;
      }
      sum.appendChild(who);
      const why = document.createElement("span");
      why.className = "why";
      // The label is part of the sentence rather than a heading above it, because it is read
      // as one line: `Rationale: the journal from 11-13 records …`.
      why.appendChild(document.createElement("b")).textContent = "Rationale: ";
      why.appendChild(document.createTextNode(end.why ?? ""));
      sum.appendChild(why);
      box.appendChild(sum);

      const facts2 = document.createElement("div");
      facts2.className = "facts";
      facts2.appendChild(badge(end.kind, end.kind));
      if (end.adds || end.dels) facts2.appendChild(badge(`+${end.adds} \u2212${end.dels}`));
      // Framed, the diff under it already says what the edit did, in green and red.
      if (!embedded) box.appendChild(facts2);
      box.appendChild(diffBlock(end));
      frag.appendChild(box);
    }

    this.el.replaceChildren(frag);
    if (embedded) align();
    return this;
  }
}

vg.coordinator().connect(new ConnDetail($connPin, document.getElementById("conn")));

const view = document.getElementById("view");
// Framed, the arrows on show are the chosen ones and no others: the slider stays at 5, out of
// sight, still filtering — it has to be on the page to publish.
if (embedded) strengthSlider.style.display = "none";
const mount = () => {
  document.documentElement.style.setProperty("--chart-w", `${chartWidth()}px`);
  view.replaceChildren(embedded
    ? vg.vconcat(vg.hconcat(strengthSlider), buildChart())
    : vg.vconcat(vg.hconcat(strengthSlider), vg.vspace(6), buildChart()));
};
mount();
// A frame is as wide as the post makes it, so a resize can move the chart's width.
let drawnAt = chartWidth(), resizing;
addEventListener("resize", () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => {
    if (chartWidth() !== drawnAt) { drawnAt = chartWidth(); mount(); }
    if (embedded) sizeExcerpt();
  }, 150);
});

// Framed, the caption is fixed at the height of the tallest thing it can be asked to hold: the
// prompt, or for each arrow on show the one sentence `one` picks for it, set in the caption's
// width and measured. Asked once, measured again whenever the width may have changed.
let shownSentences = null;
async function sizeExcerpt() {
  if (!shownSentences) {
    const rows = Array.from(await vg.coordinator().query(
      `SELECT c.cx1, c.cy1, c.cx2, c.cy2, c.strength, c.sentence
         FROM conns c
         JOIN (SELECT DISTINCT cx1, cy1, cx2, cy2 FROM conn_arcs_at
                WHERE threshold = ${SHOWN_AT}) a
           ON c.cx1 = a.cx1 AND c.cy1 = a.cy1 AND c.cx2 = a.cx2 AND c.cy2 = a.cy2`));
    const byArc = new Map();
    for (const r of rows) {
      const k = [r.cx1, r.cy1, r.cx2, r.cy2].join();
      byArc.set(k, [...(byArc.get(k) ?? []), r]);
    }
    shownSentences = [...byArc.values()].flatMap(one).map(r => r.sentence);
  }
  align();   // gives the caption the plot's width, which is the width the sentences wrap in
  const probe = document.createElement("div");
  probe.className = "caption";
  probe.style.cssText = "position: absolute; visibility: hidden; min-height: 0; width: "
    + caption.getBoundingClientRect().width + "px";
  caption.after(probe);
  let tallest = 0;
  for (const s of [null, ...shownSentences]) {
    probe.replaceChildren(s === null ? PROMPT : excerpt(s));
    tallest = Math.max(tallest, probe.getBoundingClientRect().height);
  }
  probe.remove();
  caption.style.setProperty("--caption-h", `${Math.ceil(tallest)}px`);
  align();
}
if (embedded) sizeExcerpt();

// Framed, two alignments, both measured off what is on screen rather than worked out from the
// margins, so they hold whatever Plot and the panel decide. The legend starts under the first
// column's first square, as the waffle's above it does. The caption spans the plot, so it is
// centred over it. And while the panel sits beside the chart, the caption's top and the first
// hunk's filename's are set level. The caption is one height whatever it holds (sizeExcerpt), and
// before any click a stand-in holds the name's place, so this comes out the same every time and
// the chart never moves once drawn.
function align() {
  const plot = [...view.querySelectorAll("svg")]
    .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
  if (!plot) return;
  const legend = view.querySelector(".legend"), swatch = legend?.querySelector("svg");
  const squares = [...plot.querySelectorAll('g[data-index="0"] rect')];
  if (swatch && squares.length) {
    legend.style.marginLeft = "0px";
    const first = Math.min(...squares.map(r => r.getBoundingClientRect().left));
    legend.style.marginLeft = `${first - swatch.getBoundingClientRect().left}px`;
  }
  caption.style.marginLeft = "0px";
  caption.style.width = `${plot.getBoundingClientRect().width}px`;
  caption.style.marginLeft =
    `${plot.getBoundingClientRect().left - caption.getBoundingClientRect().left}px`;
  const conn = document.getElementById("conn");
  const name = conn.querySelector(".who > span");
  const beside = conn.closest(".panel").getBoundingClientRect().left
                 >= view.getBoundingClientRect().right;
  if (!name || !beside) { caption.style.marginTop = conn.style.marginTop = "0px"; return; }
  // Top to top, whichever is higher coming down to meet the other; each measured where it rests,
  // before this pass's own margin.
  const capTop = caption.getBoundingClientRect().top - parseFloat(getComputedStyle(caption).marginTop);
  const nameTop = name.getBoundingClientRect().top - parseFloat(getComputedStyle(conn).marginTop);
  caption.style.marginTop = `${Math.max(0, nameTop - capTop)}px`;
  conn.style.marginTop = `${Math.max(0, capTop - nameTop)}px`;
}
// Plot renders after its query returns, and again on a remount; align once it has.
if (embedded) new MutationObserver(() => requestAnimationFrame(align))
  .observe(view, { childList: true, subtree: true });

// The palette picker. Setting the attribute repaints the page's chrome in CSS; the chart has
// its colours baked into an SVG, so it is rebuilt — which is what series.css asks for, since a
// transitioning token would otherwise hand a chart a colour part-way between two palettes.
const picker = document.getElementById("palette");
for (const p of palettes) {
  // On its own the page offers the light half only: its chrome is drawn for a pale ground. The
  // dark ones travel too, for when a host page is showing one (FRAMED in _page.py).
  if (!p.name.endsWith("-light")) continue;
  const o = document.createElement("option");
  o.value = p.name;
  o.textContent = p.name.replace(/-light$/, "").replace(/-/g, " ");
  picker.appendChild(o);
}
picker.value = pal.name;
document.documentElement.dataset.palette = pal.name;
picker.onchange = () => { document.documentElement.dataset.palette = picker.value; };
// The attribute is the palette, whoever set it: this picker, or the host page when framed.
new MutationObserver(() => {
  const next = named(document.documentElement.dataset.palette);
  if (!next || next === pal) return;
  pal = next;
  picker.value = pal.name;
  mount();
  paintTopics();
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-palette"] });

// Three of the eight topic hues sit under 3:1 against this surface, and the palette documents
// that as needing visible labels or a table view rather than a legend alone. This is both: the
// swatch carries the identity, the words and the count carry it again without relying on colour.
function paintTopics() {
  if (!byTopic) return;
  const table = document.getElementById("topics");
  table.replaceChildren();
  topics.forEach((t, i) => {
    const row = document.createElement("div");
    row.className = "trow";
    const sw = document.createElement("i");
    sw.style.background = hueFor(t);
    const name = document.createElement("span");
    name.className = "tname";
    name.textContent = t;
    const n = document.createElement("span");
    n.className = "tcount";
    n.textContent = topicCounts[i];
    row.append(sw, name, n);
    table.appendChild(row);
  });
  document.getElementById("topics-panel").hidden = false;
}
paintTopics();
</script>
__FRAMED__
</body>
</html>
"""


def arcs_of(rows: list[dict]) -> dict[frozenset, int]:
    """The arrows behind a set of connections, each as strong as the best sentence behind it.

    Unordered on purpose: a pair connected in both directions is one arrow, the same collapse
    the chart itself does before it draws anything.
    """
    arcs: dict[frozenset, int] = {}
    for r in rows:
        key = frozenset((r["from_id"], r["to_id"]))
        arcs[key] = max(arcs.get(key, 0), r["strength"])
    return arcs


def thin(rows: list[dict], fraction: float, seed: int, *,
         among=None, protect=None) -> list[dict]:
    """Keep `fraction` of the ARROWS, evenly across the strengths.

    An arrow, not a sentence: the page's own heading says one arrow is one connection, and two
    sentences joining the same pair were always one arrow — so thinning sentences would have
    dropped half the evidence in the panel and left the chart exactly as dense.

    Stratified, so this is not a strength filter wearing a different name. The slider already
    answers "show me only the strong ones"; this answers "show me half as many", and a naive
    sample would quietly do the first while claiming the second. The strength mix that comes out
    is the strength mix that went in.

    `among` narrows what is eligible — a predicate on the arrow, so a caller can thin only the
    ones that cross a week and leave every arrow inside a column alone. `protect` is a second
    predicate for arrows that survive whatever `among` selected: an exemption, not a filter.
    Anything neither selected nor protected is kept untouched, which is what makes two passes of
    this composable rather than a single fraction pretending to be two.

    Seeded, so the page is the same page every time it is built. A chart that reshuffles itself
    on every run cannot be compared with the one you were looking at a minute ago.
    """
    arcs = arcs_of(rows)
    eligible = {k: v for k, v in arcs.items()
                if (among is None or among(k)) and not (protect and protect(k))}
    keep = set(arcs) - set(eligible)
    for strength in sorted({v for v in eligible.values()}):
        band = sorted((tuple(sorted(k)), k) for k, v in eligible.items() if v == strength)
        rng = random.Random(f"{seed}-{strength}")
        rng.shuffle(band)
        keep.update(k for _, k in band[:round(len(band) * fraction)])
    return [r for r in rows if frozenset((r["from_id"], r["to_id"])) in keep]


def salience(story: str | None) -> dict[str, int]:
    """What `pick` ranks by: a story's own lexicon of names and threads, or nothing."""
    if not story:
        return {}
    import _story
    if story != _story.NAME:
        sys.exit(f"--salience {story}: the only story here is {_story.NAME!r}.")
    return _story.SALIENT


# Words too common to count as two texts agreeing — see `pick`'s grounding test.
COMMON = set("""about above after again against almost already although always among another
anything around because become before being below between could doesn't during either enough
every everything first fourth going might never nothing other others second should since
something still their there these thing things third those though three through today tomorrow
under until which while whole would yourself""".split())


def pick(rows: list[dict], band: int, count: int, note_of: dict, topic_of: dict,
         week_of: dict, lexicon: dict[str, int], seed: int, text_of: dict | None = None,
         fill_same_topic: bool = False, quotas: dict[str, int] | None = None):
    """Keep `count` arrows of strength `band`, CHOSEN — every other band untouched.

    `thin` samples, and a sample of the strongest band is a fair picture of it. This is for when
    a fair picture is not what the first screen is for: the slider opens on the top band, and a
    reader meets those arrows before anything else. So each one has to say something by itself.

    Eligible: the two ends are different notes AND different topics. An arrow from a note to its
    own later self, or between two notes filed under the same topic, is a true connection and a
    dull one — the page already shows that a topic hangs together by colouring it.

    With `fill_same_topic`, the topic rule is the one that gives way, and only last: once no
    arrow that passes it is left, the rest may join two different notes filed under one topic. A
    topic pass can put a third of a story under a single word, and the arrows it strands on one
    side of that word are sometimes exactly the ones worth seeing.

    And grounded, when `text_of` is given: each end has to share at least two of the sentence's
    own content words with what that square actually says. The model sometimes pins a sentence
    on the wrong square — a line about a drowning landed on a note about a lease — and a reader
    who clicks one of the first ten and finds that has been shown the page is guessing. A figure
    counts as a word: `115.96` in the sentence and in the ledger is as good a match as two names,
    and the sleep report and the ledger are mostly figures.

    `quotas` — {title prefix: n} — makes at least n of the picks touch a unit whose title starts
    with that prefix, taken first and from the same eligible arrows in the same order. For a
    page that should open with the ledger and the sleep report on it as well as the plot.

    Ranked by `lexicon` — the names and threads a reader would recognise, read off the sentence,
    both rationales and both note titles — plus a little for crossing a week, since an arrow
    that reaches back is a throughline and one inside a column is an observation. Ties go by
    seed, not by id, so the order a file happened to be written in decides nothing.

    Then taken greedily, best first, and spread. Never the same pair of notes twice or the same
    sentence twice — one sentence joining three units is one thing a reader sees, not two — and
    a fragment of a picked sentence is that sentence: the model sometimes quotes half of one,
    and the half comes back joining a different pair. A
    note sits in at most two picks unless that runs dry before `count`. A pair of topics that
    is already on screen costs a little rather than being refused: refusing it outright let an
    arrow about the grocery bill past the one about the poisoned cup, because the cup happened to
    join two notes filed under the same pair.
    """
    arcs = arcs_of(rows)
    said: dict[frozenset, list[str]] = {}
    lead: dict[frozenset, tuple[int, str]] = {}
    for r in rows:
        k = frozenset((r["from_id"], r["to_id"]))
        said.setdefault(k, []).extend(
            (r["sentence"], r.get("from_rationale") or "", r.get("to_rationale") or ""))
        if r["strength"] > lead.get(k, (0, ""))[0]:
            lead[k] = (r["strength"], r["sentence"])
    patterns = {t: re.compile(rf"\b{re.escape(t[:-1])}" if t.endswith("*")
                              else rf"\b{re.escape(t)}\b") for t in lexicon}

    def score(k: frozenset) -> int:
        a, b = sorted(k)
        text = " ".join(said[k] + [note_of[a].replace("-", " "),
                                   note_of[b].replace("-", " ")]).lower()
        found = sum(w for t, w in lexicon.items() if patterns[t].search(text))
        return found + (2 if week_of.get(a) != week_of.get(b) else 0)

    def words(text: str) -> set[str]:
        low = text.lower()
        return ({w for w in re.findall(r"[a-zà-ÿ']{5,}", low) if w not in COMMON}
                | set(re.findall(r"\d+\.\d+|\d{3,}", low)))

    def grounded(k: frozenset) -> bool:
        if text_of is None:
            return True
        said_words = words(lead[k][1])
        return all(len(said_words & words(f"{text_of.get(u, '')} {note_of[u].replace('-', ' ')}"))
                   >= 2 for u in k)

    strict, same_topic = [], []
    for k, s in arcs.items():
        if s != band or len(k) != 2:
            continue
        a, b = sorted(k)
        if note_of.get(a) == note_of.get(b) or not grounded(k):
            continue
        if topic_of.get(a) and topic_of.get(b) and topic_of[a] != topic_of[b]:
            strict.append(k)
        elif fill_same_topic:
            same_topic.append(k)

    def ranked(ks: list[frozenset]) -> list[frozenset]:
        out = sorted(ks, key=lambda k: tuple(sorted(k)))
        random.Random(f"{seed}-pick-{band}").shuffle(out)
        out.sort(key=score, reverse=True)             # stable, so the shuffle breaks ties
        return out

    def pair(k: frozenset, of: dict) -> frozenset:
        a, b = sorted(k)
        return frozenset((of.get(a), of.get(b)))

    chosen: list[frozenset] = []
    note_pairs, topic_pairs, sentences, per_note = set(), set(), set(), Counter()

    def flat(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", s.lower()))

    def repeats(s: str) -> bool:
        f = flat(s)
        return any(f in g or g in f for g in sentences)

    def take(order: list[frozenset], limit: int) -> None:
        for cap in (2, count):                        # at most two picks a note, unless dry
            while len(chosen) < limit:
                open_ = [k for k in order if k not in chosen
                         and pair(k, note_of) not in note_pairs
                         and not repeats(lead[k][1])
                         and all(per_note[n] < cap for n in pair(k, note_of))]
                if not open_:
                    break
                # `max` keeps the first of equals, and `order` is already best-first.
                k = max(open_, key=lambda k: score(k) - 4 * (pair(k, topic_of) in topic_pairs))
                chosen.append(k)
                note_pairs.add(pair(k, note_of))
                topic_pairs.add(pair(k, topic_of))
                sentences.add(flat(lead[k][1]))
                per_note.update(pair(k, note_of))

    def touches(k: frozenset, prefix: str) -> bool:
        return any(str(note_of.get(u, "")).startswith(prefix) for u in k)

    tiers = (ranked(strict), ranked(same_topic))      # every rule first, then the fill
    for prefix, n in (quotas or {}).items():
        for order in tiers:
            need = n - sum(touches(k, prefix) for k in chosen)
            if need > 0:
                take([k for k in order if touches(k, prefix)], min(count, len(chosen) + need))
    for order in tiers:
        take(order, count)

    keep = {k for k, s in arcs.items() if s != band} | set(chosen)
    return [r for r in rows if frozenset((r["from_id"], r["to_id"])) in keep], chosen, score


def main() -> None:
    ap = argparse.ArgumentParser(
        description="The week's hunks, with the connections a model found drawn between them.")
    ap.add_argument("--week", help="Session date whose connections are drawn (default: newest).")
    ap.add_argument("--connections", type=Path, help="An explicit connections-*.json.")
    ap.add_argument("--out", type=Path, help="Destination HTML (default: beside the sessions).")
    ap.add_argument("--per-row", type=int, default=5, help="Squares per row in a week (default 5).")
    ap.add_argument("--week-gap", type=int, default=3,
                    help="Empty columns between weeks (default 3).")
    ap.add_argument("--color-by", choices=["kind", "topic"], default="kind",
                    help="Colour squares by what the edit did (default), or by their topic.")
    ap.add_argument("--topics", type=Path, help="An explicit topics-*.json for --color-by topic.")
    ap.add_argument("--sample", type=float, default=1.0, metavar="FRACTION",
                    help="Draw only this fraction of the arrows, taken evenly across the "
                         "strengths (default 1.0 — all of them). For a corpus dense enough that "
                         "every square has a line through it. Not a strength filter: the slider "
                         "is that, and this leaves the strength mix alone.")
    ap.add_argument("--sample-inter", type=float, default=1.0, metavar="FRACTION",
                    help="A second pass over the arrows that CROSS a week, thinning only those "
                         "and leaving everything inside a column alone. Applied after --sample, "
                         "so the two compose. Stratified by strength like --sample.")
    ap.add_argument("--keep-week", action="append", metavar="DATE|first",
                    help="A week whose arrows --sample-inter never touches, in or out. Repeat "
                         "for several. `first` means the earliest week on the chart, so a "
                         "recipe does not have to name a date that moves.")
    ap.add_argument("--sample-strength", metavar="N=FRACTION", action="append",
                    help="Thin one strength band and leave the others alone — `5=0.5` draws "
                         "half the arrows the letter names unmistakably. Repeat for several "
                         "bands. This is the one to reach for when the page opens crowded: the "
                         "slider starts at 5, so the strongest band is the only one a reader "
                         "sees until they move it, and it is the only band whose density "
                         "decides the first impression. Applied after --sample and "
                         "--sample-inter, so all three compose.")
    ap.add_argument("--pick-strength", metavar="N=COUNT",
                    help="Keep COUNT arrows of strength N, chosen rather than sampled — see "
                         "`pick`: each joins two different notes under two different topics, "
                         "no pair of notes repeats, and among those the most recognisable win "
                         "(--salience). The other bands are left alone. Applied last, after "
                         "every --sample pass. Needs the topics file.")
    ap.add_argument("--salience", metavar="STORY",
                    help="Rank --pick-strength by a story's names and threads — `hamlet` reads "
                         "_story.SALIENT. Without it every eligible arrow ties and the seed picks.")
    ap.add_argument("--fill-same-topic", action="store_true",
                    help="When fewer than COUNT arrows pass every --pick-strength rule, fill the "
                         "rest from arrows that join two different notes filed under the same "
                         "topic — still grounded, still spread. The topic rule gives way last.")
    ap.add_argument("--pick-with", metavar="PREFIX=N", action="append",
                    help="Make at least N of the --pick-strength picks touch a unit whose title "
                         "starts with PREFIX — `finances-=2` puts two ledger arrows on the first "
                         "screen. Taken before the rest, under the same rules. Repeatable.")
    ap.add_argument("--show-picks", action="store_true",
                    help="Print the picked arrows' note titles and topics. Off by default, "
                         "because on the real vault those titles are the one thing that must "
                         "not be printed anywhere a model can read them.")
    ap.add_argument("--sample-seed", type=int, default=7,
                    help="Which half the sampling keeps. Fixed, so the page is reproducible.")
    ap.add_argument("--cdn", default=CDN, help=f"Where the page imports vgplot from ({CDN}).")
    args = ap.parse_args()

    src = args.connections
    if src is None:
        found = sorted(SESSIONS.glob("*/connections-*.json"))
        if args.week:
            found = [p for p in found if p.parent.name == args.week]
        if not found:
            sys.exit(f"No connections-*.json under {SESSIONS} — run: just connections")
        src = found[-1]
    found = json.loads(src.read_text())

    weeks = load([SESSIONS / w / "microlite.md" for w in found["weeks"]])
    hunks = by_id(weeks)
    slot = {w["week"]: i for i, w in enumerate(weeks)}
    rank = {k: i for i, k in enumerate(KINDS)}

    hunk_rows = [
        {"id": h["id"], "week": h["week"], "slot": slot[h["week"]], "ord": h["ord"],
         "kind": h["kind"], "rank": rank[h["kind"]], "note": h["note"],
         "head": h["head"], "text": h["text"], "adds": h["add"], "dels": h["dele"]}
        for w in weeks for h in w["hunks"]
    ]
    # The week's own sources, as units in the same table and in the same column: a square each,
    # stacked with that week's hunks and ordered by kind the way every other square is, so they
    # sit together at the top of the week they belong to. They are what the letter reads a note
    # *against* — a night's sleep figure set beside what was written that day — and until they
    # were addressable, such a sentence had to be dropped or pinned to a hunk that was half of
    # what it meant.
    # Only the colour sets them apart, which is the one thing about them worth setting apart.
    sources = found.get("sources", [])
    hunk_rows += [
        {"id": src["id"], "week": found["week"], "slot": slot[found["week"]],
         "ord": len(weeks[slot[found["week"]]]["hunks"]) + i,
         "kind": "context", "rank": rank["context"], "note": src["id"],
         "head": "", "text": src["text"], "adds": 0, "dels": 0}
        for i, src in enumerate(sources)
    ]
    # Every row carries a topic column, even in kind mode, so one query serves both pages.
    for row in hunk_rows:
        row.setdefault("topic", "")
    # `from` and `to` are SQL keywords, and a quoted identifier in every query that touches
    # them is a trap left for later. They are renamed once, here, on the way into the page.
    known = set(hunks) | {r["id"] for r in hunk_rows}
    conn_rows = [
        {"from_id": c["from"], "to_id": c["to"], "kind": c["kind"],
         "sentence": c["sentence"], "strength": c["strength"],
         "from_rationale": c["from_rationale"], "to_rationale": c["to_rationale"]}
        for c in found["connections"]
        if c["from"] in known and c["to"] in known
    ]
    if not conn_rows:
        sys.exit(f"{src.name} holds no connection whose hunks are still on the chart.")

    # Which week each unit belongs to, so an arrow can be asked whether it crosses one. A source
    # file has no ordinal in its id and belongs to the week whose letter was read.
    unit_week = {r["id"]: r["week"] for r in hunk_rows}
    first_week = weeks[0]["week"]
    kept_weeks = {first_week if w == "first" else w for w in (args.keep_week or [])}

    def crosses(arc) -> bool:
        return len({unit_week.get(u) for u in arc}) > 1

    def spared(arc) -> bool:
        return bool(kept_weeks & {unit_week.get(u) for u in arc})

    if args.sample < 1:
        conn_rows = thin(conn_rows, args.sample, args.sample_seed)
    if args.sample_inter < 1:
        # A second, narrower pass. Only arrows that leave their column are eligible, and an
        # arrow with an end in a spared week is exempt — so a week can be held at full density
        # while the rest of the chart thins around it, which is what makes one column readable
        # as the thing the others are being compared against.
        conn_rows = thin(conn_rows, args.sample_inter, args.sample_seed + 1,
                         among=crosses, protect=spared)
    for n, spec in enumerate(args.sample_strength or []):
        band, _, fraction = spec.partition("=")
        if not fraction or not band.strip().isdigit():
            sys.exit(f"--sample-strength {spec}: expected N=FRACTION, as in 5=0.5.")
        band, fraction = int(band), float(fraction)
        # `among` is a predicate on the arrow and strength is a property of the arrow, so the
        # band is read off the arcs as they stand at this point in the pipeline rather than off
        # the original rows — which is what makes this compose with the two passes above instead
        # of quietly re-selecting arrows they have already dropped.
        strengths = arcs_of(conn_rows)
        conn_rows = thin(conn_rows, fraction, args.sample_seed + 2 + n,
                         among=lambda a, b=band, s=strengths: s.get(a) == b)

    if args.pick_strength:
        band, _, count = args.pick_strength.partition("=")
        if not (band.strip().isdigit() and count.strip().isdigit()):
            sys.exit(f"--pick-strength {args.pick_strength}: expected N=COUNT, as in 5=10.")
        tpath = args.topics or (SESSIONS / found["week"] / f"topics-{found['week']}.json")
        if not tpath.is_file():
            sys.exit(f"--pick-strength wants topics and there is no {tpath} — run: just topics")
        topic_of = json.loads(tpath.read_text())["units"]
        note_of = {r["id"]: r["note"] for r in hunk_rows}
        quotas = {}
        for spec in args.pick_with or []:
            prefix, _, n = spec.rpartition("=")
            if not prefix or not n.isdigit():
                sys.exit(f"--pick-with {spec}: expected PREFIX=N, as in finances-=2.")
            quotas[prefix] = int(n)
        conn_rows, chosen, score = pick(conn_rows, int(band), int(count), note_of, topic_of,
                                        unit_week, salience(args.salience), args.sample_seed,
                                        text_of={r["id"]: r["text"] for r in hunk_rows},
                                        fill_same_topic=args.fill_same_topic,
                                        quotas=quotas)
        print(f"picked   {len(chosen)} of the strength-{band} arrows"
              + (f", ranked by {args.salience}" if args.salience else ""))
        for k in chosen if args.show_picks else []:
            a, b = sorted(k)
            print(f"  {score(k):>3}  {note_of[a]} ({topic_of.get(a)}, {unit_week[a]})  ·  "
                  f"{note_of[b]} ({topic_of.get(b)}, {unit_week[b]})")

    if not conn_rows:
        sys.exit("Sampling left nothing to draw.")

    # ── topic colouring, when asked for. A topic is a fact about a unit, so it rides on the
    # same row; what changes is which column the fill reads and which column the layout sorts
    # by. Sorting by topic is not decoration: it puts each topic's squares together, so only
    # topics adjacent in the fixed order ever touch — which is the pairlist the palette was
    # validated against. Scattered, any two hues could land side by side and the guarantee
    # would be the wrong one.
    topics: list[str] = []
    palette: dict[str, str] = {}
    if args.color_by == "topic":
        tpath = args.topics or (SESSIONS / found["week"] / f"topics-{found['week']}.json")
        if not tpath.is_file():
            sys.exit(f"No topics file at {tpath} — run: just topics")
        tdata = json.loads(tpath.read_text())
        # One slot per hue the palette holds back for categories, then the tail folds into a
        # single neutral. How many that is comes from the palette and is not a number to write
        # down here: upstream moved from twelve series to ten, one per hue family plus two
        # shades, and the count of what is left after the link slot went with it.
        head = tdata["topics"][:len(TOPIC_HUES)]
        palette = dict(zip(head, TOPIC_HUES)) | {OTHER: OTHER_HUE}
        assign = {u: (t if t in palette else OTHER) for u, t in tdata["units"].items()}
        seen = [t for t in head if t in assign.values()]
        topics = seen + ([OTHER] if OTHER in assign.values() else [])
        trank = {t: i for i, t in enumerate(topics)}
        for row in hunk_rows:
            row["topic"] = assign.get(row["id"], OTHER)
            row["rank"] = trank.get(row["topic"], len(topics))
        counts: dict[str, int] = {}
        for row in hunk_rows:
            counts[row["topic"]] = counts.get(row["topic"], 0) + 1

    intra = sum(1 for c in conn_rows if c["kind"] == "intra")
    hunk_count = len(hunk_rows) - len(sources)
    # Sentences and arrows are not the same count: two sentences can join the same pair, and
    # that is one arrow carrying two rationales rather than two arrows drawn on top of it. Via
    # `arcs_of`, so the pair is unordered — the chart collapses A→B and B→A into one arrow
    # before it draws anything, and a heading that counted them separately was claiming more
    # arrows than the page has on it.
    arcs = len(arcs_of(conn_rows))
    meta = (f"{len(conn_rows)} connections in {arcs} arrows · {intra} within a week · "
            f"{len(conn_rows) - intra} across weeks · from the {pretty(found['week'])} letter · "
            f"{hunk_count:,} hunks over {len(weeks)} weeks"
            + (f" · {len(sources)} sources" if sources else ""))

    html = SHELL
    for token, value in (
        ("__TITLE__", "connections — hunks the week's letter joined"
                        + (" (topics)" if args.color_by == "topic" else "")),
        ("__STYLE__", STYLE + "\n" + palette_css()),
        ("__EXTRA_STYLE__", EXTRA_STYLE),
        ("__FRAMED__", FRAMED),
        ("__HEADING__", "Connections"),
        ("__META__", meta),
        ("__COUNT__", "one square is one unit · one arrow is one connection"
                        + (" · coloured by topic" if args.color_by == "topic" else "")),
        ("__CDN__", args.cdn),
        ("__HOVER_WIDTH__", str(HOVER_WIDTH)),
        ("__UNIT_STROKE__", str(UNIT_STROKE)),
        ("__OTHER_HUE__", OTHER_HUE),
        ("__NOTE__", "Nothing in the vault links these notes &mdash; no tag, no backlink, no "
                     "folder. Every arrow is one sentence from that week's letter, found by "
                     "<code>claude-haiku-4-5</code> reading the letter back against the hunks "
                     "it was written from, together with the week's own sleep report and "
                     "ledger &mdash; those are units too, and the letter reads a note against "
                     "them constantly. Click an arrow: it stays lit, and the two units it joins "
                     "open on the right with their squares outlined. The arrows are the only "
                     "thing to press, since a square carries no reason of its own."),
    ):
        html = html.replace(token, value)
    # Last, and only once: a note whose text contains __META__ is data, not a template.
    html = html.replace("__DATA__", embed({
        "colorBy": "topic" if args.color_by == "topic" else "kind",
        # Every palette travels with the page: switching is a local act, and re-reading the
        # colours out of the stylesheet would mean trusting getComputedStyle to have caught up.
        "palettes": [{"name": p["name"], "topics": p["topics"], "link": p["link"]}
                     for p in PALETTES],
        "palette": DEFAULT,
        "topics": topics,
        "topicColors": [palette[t] for t in topics],
        "topicCounts": [counts[t] for t in topics] if topics else [],
        "hunks": hunk_rows,
        "conns": conn_rows,
        "kinds": list(KINDS),
        "colors": [k["hex"] for k in KINDS.values()],
        "perRow": args.per_row,
        "weekGap": args.week_gap,
        "weeks": [w["week"] for w in weeks],
        # Spelled-out day names, keyed by week. The panel puts one in front of every date and
        # the x axis stacks one above every column label, and neither can work it out for
        # itself once the year has gone.
        "weekdays": {w["week"]: weekday(w) for w in weeks},
        "annotatedWeek": found["week"],
        # The frame keys off the slot rather than the week, because the week's sources carry
        # that same date and sit in a column of their own — keyed by week it would stretch
        # across the gap and enclose them too.
        "annotatedSlot": slot[found["week"]],
    }))

    # A separate page, not a replacement: the two views answer different questions about the
    # same connectome, and either is worth having open while reading the other.
    suffix = "-topics" if args.color_by == "topic" else ""
    out = args.out or SESSIONS / f"connections-{found['week']}{suffix}.html"
    out.write_text(html)
    print(f"{len(conn_rows)} connections in {arcs} arrows · {hunk_count:,} hunks "
          f"+ {len(sources)} sources · {len(weeks)} weeks "
          f"· {out.stat().st_size / 1e6:.1f} MB")
    print(f"\n  open  {link(out)}")


if __name__ == "__main__":
    main()
