# Getting the week's Obsidian hunks

```sh
just hunks out.md              # a window to a path of your choosing
just hunks out.md 30           # a different window
just weekly --only microlite   # into this week's session, as the `diff:` key
```

Nothing needs to be running and nothing needs to be pressed: this reads Obsidian's **File
Recovery** snapshots off disk, works while Obsidian is open or closed, and takes about a second.

## One renderer, two front ends

There is a single implementation of "a week of edits as diffs" — `hunking_obsidian.py`, which
lives in [obsidian-microlite](https://github.com/altosaar/obsidian-microlite) and is pinned
here as the `vendor/obsidian-microlite` submodule. The Obsidian plugin is a TypeScript port of
it, and that repo's test suite pins the port to this script's output as a **golden oracle**
(`test/fixtures/synthetic.expected.md` is what the Python produced). So pressing the ribbon
icon in Obsidian and running `just hunks` give you the same review, and neither can quietly
drift from the other without a test failing.

`tools/microlite_hunks.py` is a thin wrapper that reimplements none of it: it sets the window
and the output path, and passes any other flag straight through.

## The 26 seconds, and where they went

This used to take 26 seconds a run. Almost none of it was I/O — copying the 113 MB IndexedDB is
0.14s on APFS. Profiled, **51 of 100 seconds sat inside `ccl_simplesnappy.decompress`**, across
82 million single-byte reads: Obsidian's snapshots live in a Chromium IndexedDB, which is a
LevelDB whose blocks are Snappy-compressed, and the reader's decompressor is pure Python walking
the compressed stream one byte at a time through a `BytesIO`.

The codec was the whole cost, not the approach. [cramjam](https://pypi.org/project/cramjam/)
ships a Rust Snappy behind prebuilt wheels — no system library, no compiler — and `ccl_leveldb`
calls `ccl_simplesnappy.decompress` by module attribute, so replacing that one name reaches
every call site. That fix lives in the reader itself, upstream in obsidian-microlite, so anyone
running the script standalone gets it too — and `load_snapshots()` applies it, so importers do
as well. It is why `cramjam` appears in the dependency headers here.

| | |
| --- | --- |
| pure-Python Snappy | **24.55s** |
| native Snappy (cramjam) | **0.86s** |
| speedup | **28.5×**, with the loaded snapshots comparing equal — 1986 snapshots across 213 notes, byte for byte |

End to end, `just hunks` went from 27.4s to about 1.4s. If cramjam is missing the reader keeps
its own decompressor and stays correct, only slow — it is an accelerator, never a dependency.

## Every week at once

```sh
just waffle                    # → sessions/microlite-waffle.html
just waffle --view chars       # open on the characters view instead
just waffle --hunks-only       # real @@ blocks alone, no whole-content notes
```

`just hunks` shows one week. `microlite_waffle.py` shows all of them: it reads the archived
`microlite.md` in every session directory and draws a
[waffle chart](https://idl.uw.edu/mosaic/examples/athlete-birth-waffle.html) — a column per
week, one square per unit, stacked bottom-up and wrapped five wide so the column reads as a
quantity. Squares are coloured by what the hunk did: lime for lines only added, cyan for a hunk
that both added and removed, and pink at a third opacity for a hunk that only deleted, so a
week's pruning reads lighter than its growth. A short note comes back from the reader as whole
content rather than a diff, so it has no `@@` blocks to count; it gets one neutral square
instead of vanishing, and `--hunks-only` drops those. Click a square to read the hunk; `/`
focuses the filter, which dims every square but one note's.

**Two views, and a chip or `v` swaps them**, because a hunk is not a unit of work:

| view | a square is | what a column says |
| --- | --- | --- |
| `hunks` | one hunk | how many times the week was edited |
| `characters` | a round number of characters added or removed | how much was edited |

In the characters view a hunk that rewrote a page is a block of squares and a fixed typo is one
of them, so the week that looks busiest in one view is often not the one that looks biggest in
the other. The square's worth is picked off a 1-2-5 ladder — the smallest that keeps the tallest
week on screen, the way an axis picks its own ticks — printed under the chart, and settable with
`--chars-per-square N`. Only the changed lines are counted, without the `+`/`-` that marks them:
the context a diff carries is what the change sits next to, and nobody edited it.

**A hunk is drawn as one piece.** Each square grows into the gap it shares with another square
of the same hunk, so the hunk tiles a single region — a tetromino, more or less — and the gap is
left to mean the one thing the eye already reads it as: a boundary. Without it, two large edits
in the same week are one block of colour and the only way to find the seam is to click around for
it. The outline belongs to the piece rather than to each square, so it is the whole shape that
thickens when selected; inside, the seams stay faintly drawn, because a waffle is a thing you
count. A piece that wraps at the end of a row is two blocks in the column, correctly: the squares
either side of a wrap are not neighbours.

A hunk never falls below one square, so the smallest edit is still there to click, and a
whole-content note keeps its single square: there is no diff under it to measure. Those unmeasured
squares are stacked at the top of each column, above the axis's honest range, with the biggest
hunk on the floor — so the coloured part of a column still reads against the ticks. Which makes
the characters view truthful about size and not about totals; the week's real total is printed
under its column either way. The selection survives a swap, and a click lights every square its
hunk owns rather than just the one under the pointer.

It parses the archive rather than calling the reader, and it has to: `--since N` is a trailing
window, so there is no way to ask File Recovery for a week it has already pruned past. The
session directories are the record, and a week that was never reviewed has no square.

The page is one file with the data inlined — no network, no build step, and the same
neobrutalist look as the other extracts, from `_page.py`. It lands under `sessions/`, which is
gitignored whole, because every square in it is a piece of a private note.

### The same chart, drawn by Mosaic

```sh
just waffle-mosaic             # → sessions/microlite-waffle-mosaic.html
```

Adapted from
[`specs/esm/athlete-birth-waffle.js`](https://github.com/uwdata/mosaic/blob/main/specs/esm/athlete-birth-waffle.js)
with the athletes swapped for hunks, and clickable the way the page above is: the rows live in
DuckDB, DuckDB lays the waffle out, clicking a square publishes to a Mosaic `Selection`, and the
panel on the right is a `MosaicClient` that answers that selection with a query of its own.

Both views are here too, on a `View` menu beside `Gap` and `Radius`. The page inserts one row per
hunk and DuckDB builds a layout table for each: `row_number()` over the week for the hunks view,
and for the characters view a lateral `generate_series` that fans each hunk out into the squares
it is owed before numbering those. A plot each, swapped by `display`, rather than one plot whose
table is rewritten underneath it — which is a change Mosaic has no way to hear about. The panel
is pointed at the other table and asked again; the published clause names a square by week,
column and row, and both tables spell those the same, so the outline stays where it is and the
panel re-reads whatever hunk now sits under it.

The pieces are outlined here rather than fused, and that is Plot's doing: `inset` is one number
for every side of every rect, so there is no way to make a square reach toward one neighbour and
not another. Instead DuckDB derives the boundary — a square contributes the side it does not
share with another square of the same `hid` — and two `rule` marks draw it over the squares.
Those marks are declared *after* the interactors, because an interactor binds to the mark
declared before it: the outline is not a thing to click, and it has to stay ink while the squares
around it go pale, or the grouping vanishes exactly when a hunk is being read.

**It is not `waffleY`, and it cannot be.** Plot draws a waffle as one `<path>` per stacked group,
filled with an SVG `<pattern>` whose tile is the square — the squares are a repeating fill, not
elements, so there is nothing per-unit under the pointer. `waffleY` also aggregates (`y: count()`),
so by the time the mark sees the data a week is four rows and the individual hunk is gone. One
square per hunk that knows *which* hunk it is means one mark per hunk, which is `rect`, positioned
by the layout `waffleY` would have done internally: `row_number()` over each week, wrapped at
`--per-row`. That takes the example's `round` input with it, since it exists only because of the
aggregation; `gap` and `radius` survive as `inset` and `rx`, which is what they always were. The
example's `unit` comes back in the characters view, meaning what it means there — a square worth
some number of records rather than exactly one — except that here it is a row per square in
DuckDB instead of a divisor in the mark, so the square worth a thousand characters still belongs
to one hunk and still has a diff to show.

Two more things had to differ from the published spec. `loadObjects` replaces `loadParquet`,
because the rows are already in the page and there is no file to serve. And the connector has to
be named: a coordinator with none reaches for a socket on `ws://localhost:3000`, which is Mosaic's
own dev server and not anything here, so the page hangs on "loading" until you hand it
`DuckDBWASMConnector`.

There is one piece of state: the connection, and the arrow is the only thing that answers a
click — a square carries no reason of its own, and one that highlights under the pointer
promises a click it will not honour. The panel always holds a pair the letter joined, the
sentence that joined them, and both diffs. A clicked arrow keeps the weight it took under the
pointer: the selected connection is drawn a second time by a `filterBy` mark at the hover
weight, from the same constant the hover rule is generated from, so what a hover promises is
what a click keeps. That overlay is `pointer-events: none`, or the selected arrow would be the
one arrow you could no longer hover or click. Both are drawn **keyed by hunk,
never by note**: the two ends are often the same note on two different dates, and that is the
connection's whole point rather than a duplicate to collapse. The one thing that *is* deduped is
the pair itself, since two sentences can join the same two hunks — the panel shows both
sentences and one pair of diffs.

Selection is shown by outline rather than by dimming alone, because opacity is already spoken
for — a deleted hunk is drawn faint on purpose, and a faint square that is merely unselected
looks the same. The unselected lose their black border; the two squares still outlined in ink
are the pair being read on the right.

It wants a network. `import` fetches `@uwdata/vgplot` (pinned, not `@latest`, so the page draws
the same next year) and DuckDB-WASM fetches its own bundles, which tells a CDN that somebody
opened it. Nothing is uploaded — DuckDB runs in the tab — but this is the one page here that is
not inert, and now that it shows diffs it holds the same private note text the other one does.
`just waffle` draws the same chart, and reads the same diffs, with nothing fetched.

## Connections: what the letter knew that the chart could not show

```sh
just connect                   # ask Haiku, then draw
just connections --dry-run     # price the call at zero first
just connections-page          # redraw from the JSON already on disk
```

The waffle draws every hunk as an island, and honestly so: the vault has no links in it, the
writer tags nothing, and two edits made a fortnight apart in notes that share no word look
exactly as unrelated as two that are about the same thing. The weekly letter already knows
better — it was written from those same hunks, and its prose does the connecting.

So `hunk_connections.py` does not ask a model to find connections from scratch. It reads the
letter back against the hunks it came from and asks only which pairs a sentence joined, which
means every arrow on the chart can be traced to a sentence the week already produced. The
contract is BAML's: `baml_src/connections.baml` declares the return type, so the answer arrives
as typed objects. Ids are checked on top of that — both must resolve to real hunks and must
differ — because a type system can promise a string and not that the string names something.
Whether a connection is within one week or across weeks is computed from the ids, never asked:
asking would only produce a second answer that can disagree with the first.

**The week's own sources are units.** The letter reads a note against the sleep report and the
ledger constantly, and while only hunks could be named, a model had to either drop that sentence
or point at a hunk that was half of what it meant — the rationales showed it, citing figures
nothing on the chart stood for. Each attachment now carries an id (its filename) and can stand
at either end of a connection, with a square of its own stacked in the column of the week it
belongs to and ordered by kind like every other square — only the colour sets it apart. Half the
connections on the current run reach one.

**Each end carries its own rationale.** One rationale for the pair kept coming back vague about
which half was which: a figure quoted without saying which unit held it, a clause that could
have described either end. `from_rationale` is about `from_unit` alone and `to_rationale` about
`to_unit` alone, and the panel shows each under the name of the unit it describes. Splitting the
field is what made the answers specific: a figure the letter quotes is now attributed, in the
rationale, to the source that actually holds it.

**Strength, and the slider.** Each connection is scored 1-5 and the slider shows only those at
or above it, 5 by default. Mosaic's slider publishes either an equality on a column or an
interval bounded from *above*, and neither says "at least this strong" — so the arcs are
expanded once per threshold they clear, and `threshold = 4` already means "4 or better". An arc
is as strong as the best sentence behind it (a MAX, not an average: one unmistakable link is not
weakened by a second, looser reading of the same pair). `vg.desc` is not exported from vgplot;
order with an expression instead.

**Naming the dates in the prompt is what found the connections.** The hunk list led with the
note title, so a note edited in four different weeks read as four near-identical entries and the
model had to reconstruct the week from the id to tell them apart. Putting the date at the front
of each header line, and saying plainly that a title recurs across weeks, took one run from 12
connections to 70.

Watch `max_tokens` when adding a field: two rationales across two dozen connections overran
8192, and a truncated JSON array surfaces from the bridge as an opaque type-decode failure
rather than as "the model ran out of room".

Weeks qualify when they have both hunks and a written letter. The letter that gets *read* is one
week's; the hunks it may reach into are every qualifying week's, which is what lets an arrow
cross a column. The context the model gets is the session's declared `attachments:` — the sleep
report and the ledger — read off `edit-me.md` rather than by globbing the folder, because the
folder also holds a week of browsing history that the letter itself never saw.

### The call is cached

```sh
just connect                   # second run: nothing billed
just connections --dry-run     # says hit or miss before you spend
just connections --no-cache    # ask again anyway
```

The answer lands in `share/.baml-cache`, beside the TTS chunk cache and under the same
gitignored directory, keyed by the letter, the hunks, the context **and the text of
`baml_src/connections.baml` itself**. The prompt is in the key because a cache that survived a
prompt change would hand back the old prompt's answer as if it were new — the one failure worth
designing against. Editing the prompt misses; reverting the edit hits again.

A hit needs neither the API key nor the generated client, which is why the cache is checked
before either is reached for: a warm `just connect` is **0.1s and imports nothing**, against 27s
and one billed call cold. What is written is what the model actually said, before this file
validates or drops anything — validation is an opinion that may change, the answer will not.

`cache_path` and `write_atomic` were two functions inside `_speech.py`, written for TTS chunks
and described in terms of audio. A model call is the same kind of thing — paid for once,
identical every time the inputs are — so they moved to `_cache.py` and `_speech.py` imports them
back in, leaving both engines reaching for them where they always have. The keys are byte-for-
byte what they were, so nothing already in the TTS cache was orphaned by the move.

### Three things the plot needed that the faceted waffle did not

**No facets.** `just waffle-mosaic` puts each week in an `fx` facet, and a Plot mark renders once
per facet, so a mark cannot span two of them — a faceted chart can draw an arrow inside a week
and never between weeks. Since the connections worth showing are exactly the ones that reach
across weeks, the facet is what gives: the weeks share one continuous x scale, offset by
`--week-gap`, and the axis is ticked at the middle of each block.

**Interactor order.** vgplot binds an interactor to `plot.marks[plot.marks.length - 1]` — the
mark most recently added when the directive runs. Collected at the end the way plot options
usually are, two toggles both bind to the last mark: clicking a square does nothing and clicking
an arrow publishes to both selections. Each toggle sits immediately after its own mark.

**`data-index` counts rendered marks.** A toggle finds its mark by `[data-index="<mark.index>"]`,
and the outline mark that marks a selected arrow's two endpoints draws nothing until an arrow is
picked. Anywhere but last, it shifts every later mark's rendered index out from under its
toggle — the arrow group comes back as index 1 while its toggle looks for 2, and clicking an
arrow silently does nothing. It goes last, where it can shift no one and draws on top anyway.

And two things the interaction needed.

Mosaic's `Toggle` clears its selection on any pointerdown outside its own mark. Each selection
here is published to by two toggles — a square and an arrow each name a hunk, and each name a
connection — so whichever listener runs second would wipe what the first just set, and clicking
a square would clear the very connection it was meant to select. Each live pick is mirrored into
a selection that only moves forward: a pick pins, a clear is ignored, and everything downstream
reads the pinned copy.

Saying two things with one click needs the mark to carry both. A rect's own channels are its
geometry, so the clickable ends carry the arrow's coordinates as extra `channels: {…}` — which
Mosaic supports, and which `channelField` finds — under names that do not collide with the
rect's own `x1`/`y2`. Two toggles then bind to that one mark, one publishing the hunk and one
the connection. Watch the column names: a custom channel reading column `x2` is silently
dropped, because the rect already aliases its computed right edge to `x2`. The arrow's
coordinates are `cx1…cy2` for that reason.

`className` is not available for styling a mark — Mosaic reads a string mark option as a column
reference, so it becomes a SQL error rather than a class. Hover and cursor are addressed by
`data-index` instead, which is the mark's own position. A frame around the annotated week is a
one-row table drawn as another `rect`; it goes after the arrows, since a mark that always
renders and is inserted earlier pushes every later mark's rendered index along. Its whitespace
is a negative `inset`, which Plot measures in pixels — the box itself is the plain cell bounds,
and `CELL_INSET - FRAME_GAP` states the relationship once. Padding it in data units instead
looks right and is not: a unit is a different number of pixels across x than down y, so the
gap comes out uneven on two of the four edges.

### The legend was shrinking the chart

Both Mosaic pages here drew at about three quarters of the size they asked for. Mosaic wraps a
plot in `div.plot` and sets `display: flex` on it **inline**, and `colorLegend()` makes the
legend a *sibling* of the chart rather than part of it. One nowrap flex line, so the legend
stood beside the chart and flex-shrank it — every square, tick and label scaled down together,
which is why it looked deliberate rather than broken. `display: block` from a stylesheet cannot
beat an inline style, but `flex-wrap` was never set inline, so `.chart .plot { flex-wrap: wrap }`
is the whole fix; the grid column then has to be the plot's width plus the panel's 32 of padding
and 8 of border, or `max-width: 100%` quietly clamps it again.

### Topics

```sh
just topics                    # → sessions/<date>/topics-<date>.json
just topics-page               # → sessions/connections-<date>-topics.html
```

The connectome colours a square by what the edit *did* — added, removed, mixed — which is a
property of the diff and not of the writer: a week spent pruning one note and a week spent
building another look alike. Topic is the other axis, and nothing in the vault records it. There
are no tags, the folder is a date, and a note's title is a name its author chose years ago.

So it is read. Two topics are fixed because two of the week's sources are — `finance` for the
ledger, `physiology` for what the ring records — and the rest are discovered: each unit gets its
own Haiku call, shown the topics found so far, and either picks one or names a new one. The calls
run **in order**, not in parallel, because that is the mechanism: a unit classified against a
fuller list reuses a topic instead of inventing one, and reuse is the whole point of a
vocabulary. 203 units took 4½ minutes and produced 16 topics; the pass is cached whole.

The first run returned two topics that were not topics at all — `i need` and `i don't`, sixteen
units between them. The function returned a bare `string`, so the model sometimes answered with
the unit's own prose, and a normalizer that truncated long answers to two words turned that prose
into something that *looked* like a topic. Both halves were wrong. The return type is a class now,
so the model fills in a described field rather than writing into a blank, and an answer longer
than two words is refused rather than trimmed.

**Colour follows the topic, never its rank.** The palettes live in `_series.py`, generated from
jaan.io's `series.css` and `palettes.css` and baked in so a page can be rendered on a machine
that has never seen that repo. Only the `-light` half is there — those are the palettes whose
values are marks on a pale ground; the unsuffixed ones are stepped for a dark surface and would
be wrong here. Each carries its own background, card and text tokens, and **switching palette
moves the ground with the marks**: the contrasts in the source are measured against that ground,
so one palette's hues on another's paper is a claim nobody checked. `--ink` and `--paper` are
aliases onto those tokens, so the whole page follows.

A picker above the chart switches between all 22. The chrome repaints in CSS off a
`[data-palette]` attribute; the chart is rebuilt, because its colours are baked into an SVG and
series.css says so explicitly — a transitioning token would hand a chart a colour part-way
between two palettes.

**The connector's slot is a measurement, per palette.** One slot is held back from the
categorical set for the connectors, which encode a relationship rather than a kind of thing, so
no topic can ever wear it. Which slot was computed for each of the 22 against that palette's own
card colour: of the twelve, the one that clears 3:1 against the surface and then sits furthest
from its nearest neighbour among the ten that remain. For the default that is `--series-10`
(ΔE 10.8, 5.76:1); the obvious spare, the twelfth, was the *worst* of the twelve at 6.7. Ink
beats all of them at 16.3 and 18.9:1 and is the honest answer when the connector is allowed to
sit outside the scheme at all.

Squares are sorted by topic so each topic is one contiguous band in a column. That is not
decoration: scattered, any two hues could land side by side, and the guarantee that matters is
the one for *adjacent* pairs.

**One arrow is one connected *pair*, unordered.** A sentence saying A belongs with B and one
saying B belongs with A describe the same tie, and with no arrowhead on either end they drew the
same line twice — two strokes stacked, reading as one darker arc for a reason no one could
guess. Sorting an arc's two endpoints by position gives both rows the same key, so they group
into one arc and one selection: 50 ordered pairs became 42. Only the drawing key is sorted;
which end is the from-end still lives in `from_note` and `from_rationale`, so the panel keeps
saying what the model said about which unit. That collapse is also why the two ends are keyed
off the arc rather than off from/to — once both directions share an arc, "the from end" is no
longer a property of it, and joining on it put four hunks under a pair that has two.

**The selection outline sits under the connectors and on the square's edge.** Plot renders marks
in declaration order and SVG has no z-index, so the outline landed on top of the arrows — and it
has to be declared last, since it draws nothing until something is picked and an empty mark
anywhere earlier shifts every later mark's rendered index out from under its toggle. It is moved
back down after each render instead, guarded, because moving it is itself a mutation and an
unguarded observer spins on its own work. Its path runs half a stroke outside the square so the
ring's inner edge lands exactly on the square's edge rather than floating off it.

**The ring and the lit arrow ease in and out over 200ms**, on jaan.io's own curve
(`cubic-bezier(0.4, 0, 0.2, 1)`). That is a plain CSS transition, and it is only possible
because neither mark is filtered by the selection: both draw every candidate, always, and
`highlight` turns the unselected invisible. Everything follows from that choice.

A mark whose *data* changes makes Plot rebuild the plot's whole SVG. With a `filterBy` on the
selection, every click therefore replaced every node — so the ring could only ever animate in,
its outgoing node having been destroyed in the same frame; and the week's frame, rebuilt
alongside it, **flashed**, because it re-ran the same entrance. Hidden by `highlight` instead, a
click sets attributes on nodes that stay put: the ring cross-fades, the frame never moves, and
the ring can be declared before the arrows rather than last, which retires the DOM-reordering
observer that used to keep it underneath them.

Two things that look like the rule failing to match, and are not. `highlight` matches the
selection's clause against the columns a mark actually fetched, and a mark fetches only what its
channels name — so the ring carries `cx1…cy2` as channels named *exactly* as their columns,
because under any other alias the clause finds nothing and every ring stays dark. And do not
name keyframes `ease-in`: that is a timing-function keyword, so `animation: ease-in …` reads it
as the curve, leaves the animation nameless, and silently does nothing.

**The selector is sized from the unit outline, not chosen.** Measuring inward from a cell's
boundary: a unit's outline is a 1px stroke centred on a path `CELL_INSET` in, so it spans 1.5 to
2.5. The ring reaches the inner edge of that outline on its own square, and the inner edge of the
neighbouring square's outline the far side of the shared boundary — a band from −2.5 to +2.5,
five pixels wide, centred on the boundary, which is why its inset is zero. It swallows the gap
and both outlines facing into it, so a selected square reads as one solid block of ink rather
than a ring floating in a moat, and it wears the same ink as the outline it thickens.

**The units are drawn at half strength**, and the legend's swatches with them, so the key still
matches the chart. The squares are the field the connectors are read against, and at full weight
a five-pixel line laid over them is one more thing in a crowd rather than the thing on top of it.
The legend's words stay at full strength; fading them helps nothing.

**Nothing outside the plot spends colour.** The shared style paints a yellow header, cyan panel
heads and badges in four of the palette's six; on these two pages they are ink and paper, the
diff carries added and removed by ground and strike rather than by green and red, and the slider
is told its accent — the one colour a stylesheet forgets, because the browser paints it. The
only hue off the chart is the swatch in the topic table, which is the plot's own colour quoted
back. Checked by walking every rendered element and counting saturated colours: zero outside the
plot, its legend and those swatches. The panels are flat too — no lift, two-pixel rules — since
a shadow was the loudest thing on a page whose job is to show a chart.

### BAML

`baml.toml` declares the generator; `just baml` regenerates `tools/baml_sdk`, which is
gitignored because 250-odd emitted files are not source. The toolchain is pinned to **0.17.0**
in that recipe, matching the `baml-bridge==0.17.0` the tools depend on. That pin is the whole
point: `brew install baml` selects the canary channel, whose only matching bridge on PyPI is a
dated nightly, and a mismatched pair fails at *import* with a version-skew error rather than at
generate time. 0.17.0 is the newest pair where both halves are real releases. (One syntax
consequence: on 0.17 `ctx.output_format` is a string, not a function.)

## Showing it to somebody: the synthetic vault

Every chart above draws private notes, which is why none of them can be shown to anybody. So
there is a second vault, in `synthetic/`, with a life in it that does not exist — four weeks of
`microlite.md`, a sleep report, a ledger, the paragraph the writer types before the week is read,
and the letter Opus writes back, in the same formats byte for byte. `PETROGRAPH_OUT=synthetic`
points every tool here at it, because every tool here already reads that variable to find
`sessions/`. Nothing downstream was changed and nothing downstream can tell.

**No text from the real vault reaches the model that writes the fake one.** The wall is a return
type rather than a promise: `synth_profile.py` asks Haiku for `HunkShape` — six integers and
three booleans — and `baml_src/synth_profile.baml` gives a name nowhere to go even if a model
decided to be helpful. Everything else measurable is measured in Python, which cannot be
helpful, and the counts are averaged over every hunk before they are written down, so what
survives describes a habit and not any hunk. `baml_src/synth_write.baml` is the far side of that
wall and its inputs are sixty numbers, a style guide written from those numbers, and an allow
list. Every name in the corpus was invented by the first call `synth_corpus.py` makes.

`synthetic/topics.conf` is the gate: an exhaustive `allow` list the corpus is made *of*, and a
`deny` list checked against every string as it returns and every file as it is written. It is
gitignored permanently — a list of what a synthetic journal must avoid describes the real one
fairly precisely — and `topics.example.conf` is the tracked copy.

Three things about the generator are worth knowing, because each was the difference between a
corpus that draws a good connectome and one that draws a boring one.

**The sources are written before the prose, and handed to it.** A note about a bad night cites
the night the ring actually recorded; a note about money cites the ledger's own number. A
hunk-to-source arrow is a third of what the connectome draws, and it can only exist if the two
documents are about the same week rather than merely filed under it.

**The notes are planned week by week before any of them are written.** A note with a beat in
week 1 and a beat in week 4 is the inter-week arrow the whole page exists to show. Generating
four independent weeks and hoping the letter finds something reaching between them does not
work, and asking for the thread up front is the only way to be sure there is one to find.

**A model is never asked for a diff.** It writes prose; the tool splices the prose into the note
it already had and runs `difflib` over the before and the after. Every id on the connectome is a
position in one of these files, so an `@@` header's line counts have to be arithmetic rather
than something that looks like arithmetic. What the tool controls instead is *where* the change
lands — appended, prepended, a rewritten passage, a deleted one — and that choice is the only
lever on the profile's mix of added, mixed and removed hunks. A model asked to choose would
append every time.

**The letter is one pass, not two.** A real week runs the ACT read into `claude-analysis.md` and
then compacts it into the letter, because a real letter gets narrated and one written straight
off a hundred kilobytes of diff reads like a report. A synthetic week wants the report: it is
read on a page, and it is what `hunk_connections.py` reads back against the hunks. So
`synth_letter.py` makes one Opus call with `prompts/act-analysis.md` and lands it in
`lifelog-<date>.md` — importing `build_bundle` and `run_claude` from `weekly_review.py` rather
than copying them, so a synthetic week is assembled by exactly the code that assembles a real
one. Switching to the single pass was worth about two and a half times the connections: 35 in 14
arrows against the compaction's 14 in 11, because a read names figures and dates where a letter
alludes to them, and a connection is only found when a sentence names both of its ends.

**And the arrows that cross a week are planted.** The first four-week corpus produced thirty-five
connections and exactly one that left its own column, because a planner writing each beat to
stand on its own leaves two beats with no phrase in common. `synth_links.py` scores every
current-week hunk against every earlier hunk on shared uncommon vocabulary, then has Haiku write
a callback into the current week's note and a sentence into the letter's open loops — both, since
a callback nothing reports draws no arrow and a sentence the notes do not support is a claim. The
scoring compares each week's *change*, not the note as it stands: a note's content is cumulative,
so scoring notes made every note match its own earlier self at 1.000 and buried every real
cross-note thread.

`just synth` runs the five stages end to end; `synthetic/README.md` has the details.

## Why not drive the plugin

It would be the same code, but it is the wrong shape for a pipeline. The plugin only runs inside
Obsidian, so it would need the app running and focused, could not run under cron or over ssh, and
would have to be triggered through a URI handler that does not exist yet. Reading the note it
leaves behind has the same problem one step removed — that note is only as fresh as the last time
somebody pressed the button, so `just weekly` would either review last week or stop and ask for a
keystroke. Generating the hunks here needs nothing running, and produces the same bytes.

## Working with the submodule

```sh
git submodule update --init            # first checkout
git submodule update --remote          # pull the newest reader
just publish-gist                      # push that copy to the public gist
```

`vault_corpus.py` uses the same submodule and the same accelerator, for the same reason at a
different scale: it needs a 12-month window of raw snapshot text for entity extraction, which no
single rendered review contains.

`oura_metrics.ts` follows the same pattern with a second submodule, `vendor/obsidian-oura-metrics`:
it imports the plugin's `buildDays` and `renderNote` rather than porting them, and adds only a
`fetch` transport in place of Obsidian's `requestUrl`. `git submodule update --remote` pulls the
newest metrics for it too.

## The other tools here

Each is a `uv run --script` with its own inline dependencies, runnable from anywhere.

The underscore-prefixed ones are imported rather than run — they hold what more than one tool
needs to agree on. Every one of them declares no dependencies, so importing them leaves a tool's
`dependencies = []` intact; that is why the naming rule lives in `_names.py` and not in
`mix_music.py`, which writes the master but drags in pedalboard, numpy and scipy. Several are
also runnable, because the justfile cannot import Python and would otherwise keep its own copy
of what they know: `_names.py --sh`, `_session.py --stub-marker`, and `_overwrite.py <path>...`.
`_numbers.py 'we spent $2,480'` is runnable for a different reason — to see what the voice will
actually be given, before a render is spent finding out.

| | |
| --- | --- |
| `_names.py` | what a session's audio is called |
| `_numbers.py` | figures spelled into English, so no engine has to guess at "$2,480" |
| `_overwrite.py` | the yes asked for before a render replaces a finished one |
| `_session.py` | what a session directory is made of, and the stub marker `just render` refuses to narrate |
| `_term.py` | clickable paths, flushed banners, the timing table |
| `_env.py` | where the repo is, and API keys from the environment or `.env` |
| `_db.py` | opening another application's SQLite without taking a lock |
| `_dates.py` | calendar-accurate month arithmetic |
| `_cache.py` | one name for a thing that was expensive to make |
| `_hunks.py` | what a hunk is, and what its id is |
| `_synth.py` | where the synthetic vault lives, and the allow/deny gate everything passes |
| `_synth_sources.py` | a week's sleep report and ledger, generated by arithmetic |
| `_page.py` | the shared page for every HTML extract |
| `microlite_hunks.py` | the week's Obsidian hunks |
| `microlite_waffle.py` | every archived week's hunks, as a waffle chart |
| `hunk_connections.py` | the connections a week's letter drew between hunks, via Haiku |
| `connection_waffle.py` | those connections, drawn as arrows between the squares |
| `unit_topics.py` | a topic for every unit, discovered one unit at a time |
| `synth_profile.py` | the real vault's habits as numbers, and nothing else |
| `synth_corpus.py` | four weeks of a vault that never existed, in the same formats |
| `synth_letter.py` | one Opus pass with the ACT prompt per synthetic week |
| `synth_links.py` | the long-range connections, planted into the notes and the letter |
| `weekly_context.py` | runs `providers.conf` and assembles a session |
| `weekly_review.py` | the unattended run: hunks → ACT read → letter → narration |
| `eleven_tts.py` | narrate a markdown file (chunked, cached, resumable) |
| `mix_music.py` | lay a narration over a ducked, looping backing bed |
| `browsing_history.py` | a week of Chrome / Firefox / Safari history as markdown |
| `vault_corpus.py` | long-window vault text for entity extraction |
| `extract_names.py` · `names_html.py` | entity extraction and its browsable page |
| `extract_contacts.py` · `contacts_html.py` · `contacts_diff.py` | who you talk to, and how that changes |
| `vault_snapshot.sh` | commit the vault's markdown to the local git mirror |

Run any of them with `--help`. The recipes in the root `justfile` are the intended entry points.
