"""
_page — the neobrutalist page shared by every extract in this repo.

Not a script. `names_html.py` and `contacts_html.py` import it, and anything that
renders an extract in future should too: the point is that a new extract inherits
the look for free rather than by copy-paste, so the pages stay one system as they
multiply. It was extracted when the second page turned out to be 63% identical to
the first.

A page here is always the same object: a header, a filterable list on the left, and
a detail panel on the right. What differs between extracts is the badge palette, the
sort options, and what a detail panel puts in the right-hand column — so those are
the parameters, and everything else is fixed.

Two rules the pages depend on and must not relax:

  · Every string that came out of a database reaches the DOM as a text node.
    Note text, message bodies, contact names and group names are all untrusted —
    a name in an address book is arbitrary text somebody else chose. `badge()`
    below uses textContent for that reason; do the same in page scripts, and never
    reach for innerHTML.
  · `__DATA__` is substituted last. It is arbitrary private text, and a note or
    message whose body happens to contain `__META__` must not be re-scanned as a
    template. `embed()` also escapes `</` so a pasted `</script>` cannot close the
    tag early.

There are no dependencies here and there is no network: importing this keeps a
tool's `dependencies = []` intact, which is what makes "nothing leaves the machine"
checkable by eye.
"""

from __future__ import annotations
import json

# The palettes a page may be drawn in live in `_series.py`, generated from jaan.io's
# series.css and palettes.css. A page emits every one of them as a `[data-palette]`
# block and switches by setting that attribute, which is how that palette system is
# meant to be driven: the chrome follows in CSS, and a chart re-reads its own colours.
from _series import BY_NAME, DEFAULT, PALETTES


def palette_css() -> str:
    """Every palette as a `[data-palette]` block, plus the default on `:root`.

    The ground travels with the marks. Each palette's contrasts were measured against
    its own background, so painting one palette's hues on another's paper is a claim
    nobody checked — switching has to move both or neither.
    """
    out = []
    for pal in PALETTES:
        rules = "".join(
            f"\n    --series-{i}: {hex};" for i, hex in enumerate(pal["topics"], 1)
        ) + (
            f"\n    --series-link: {pal['link']};"
            f"\n    --bg: {pal['bg']};"
            f"\n    --card: {pal['card']};"
            f"\n    --surface: {pal['surface']};"
            f"\n    --text: {pal['text']};"
            f"\n    --muted: {pal['muted']};"
        )
        out.append(f'  :root[data-palette="{pal["name"]}"] {{{rules}\n  }}')
    return "\n".join(out)


SERIES_REST = "#b9b2a0"


# What a page does when jaan.io frames it (`<iframe src="/files/…">` in a post): take the host's
# palette, and tell the frame how tall it is. Substituted into a page as `__FRAMED__`, just
# before `</body>`.
#
# Only a same-origin frame gets past the first line — `frameElement` is null for a page opened
# from a file, served on its own, or framed by anybody else, and then this does nothing at all.
# The host's switcher sets `data-palette` on its <html>; this watches that attribute and sets
# the same name on its own, so the page recolours by the mechanism its own picker uses. Every
# palette the site has is in `_series.py` — the dark ones, and the site's own as "site", which
# is what no attribute means — so the frame's ground is the host's ground, exactly.
#
# Framed, the post carries the title and the switcher, so the page drops its own; and the
# chrome spends no colour, since the plot's hues are the palette's now and a cyan panel head
# beside them would read as one more series. The few colours the pages fix for a pale ground —
# the diff's green and red, the pale strength badges, the browser's own input text — are
# re-inked under a dark palette, which is any name not ending `-light`.
FRAMED = """<style>
  .embedded { --shadow: none; }
  .embedded header, .embedded #palette-row,
  .embedded .layout > .panel:first-child > h2 { display: none; }
  .embedded .layout { padding: 8px 0; }
  .embedded .panel { border: 0; box-shadow: none; background: transparent; }
  .embedded .panel > h2 { border-bottom: 0; }
  /* The pressed chip is inked rather than lime: card and paper are a step apart on a pale
     palette and nothing apart on a dark one. */
  .embedded .chip[aria-pressed="true"] { background: var(--ink); color: var(--paper); }
  .embedded .chip { box-shadow: none; }
  .embedded .panel > h2, .embedded .sub,
  .embedded .badge.added, .embedded .badge.mixed, .embedded .badge.removed,
  .embedded .badge.full { background: var(--paper); box-shadow: none; }
  /* The box's own border says it has focus; an outline on top of it was a second one. */
  .embedded input[type=search]:focus, .embedded select:focus { outline: none; }
  /* Framed, nothing scrolls: the frame is sized to the page (below). `.chart` was
     `overflow-x: auto`, which makes the y axis scroll too, and a chart a pixel taller than its
     box grew a scrollbar until a click made the page tall enough to hide it. */
  .embedded, .embedded body { overflow: hidden; }
  .embedded .chart { overflow: visible; }
  /* Framed, every diff is the same box — a square as wide as its panel — and scrolls inside
     it, so opening a hunk never changes the page's length by more than one square. Its lines
     wrap, so the only way through a long one is down. */
  .embedded .hunk {
    width: 100%; aspect-ratio: 1; box-sizing: border-box;
    display: flex; flex-direction: column;
  }
  .embedded .hunk pre { flex: 1; min-height: 0; max-height: none; overflow: auto; }
  .embedded .hunk code { white-space: pre-wrap; overflow-wrap: anywhere; }
  .embedded input, .embedded select { color: var(--ink); }
  .embedded:not([data-palette$="-light"]) { color-scheme: dark; }
  .embedded:not([data-palette$="-light"]) .hunk .ln-h { color: #d2a8ff; background: #2b1f45; }
  .embedded:not([data-palette$="-light"]) .hunk .ln-a { color: #aff5b4; background: #0f3a1d; }
  .embedded:not([data-palette$="-light"]) .hunk .ln-d { color: #ffdcd7; background: #5a1219; }
  .embedded:not([data-palette$="-light"]) .hunk .ln-c { color: var(--muted); }
  .embedded:not([data-palette$="-light"]) :is(.badge.s1, .badge.s2, .badge.s3, .badge.s4, mark) {
    color: #111111;
  }
</style>
<script>
(() => {
  const frame = window.frameElement;
  if (!frame) return;
  const root = document.documentElement, host = parent.document.documentElement;
  const names = new Set(__NAMES__);
  root.classList.add("embedded");
  const follow = () => {
    const name = host.dataset.palette || "site";
    root.dataset.palette = names.has(name) ? name : "__DEFAULT__";
  };
  follow();
  new MutationObserver(follow).observe(host, { attributes: true, attributeFilter: ["data-palette"] });
  // A frame has no height of its own to give. The page says, and says again when a click opens
  // a panel or a redraw changes the chart.
  // The body's scroll height as well as its box, so whatever a chart draws past the body's
  // edge is inside the frame rather than clipped by it.
  const fit = () => {
    const body = document.body;
    frame.style.height = Math.ceil(Math.max(body.getBoundingClientRect().height, body.scrollHeight)) + "px";
  };
  new ResizeObserver(fit).observe(document.body);
})();
</script>""".replace("__NAMES__", json.dumps([p["name"] for p in PALETTES])).replace(
    "__DEFAULT__", DEFAULT)

_DEFAULT = BY_NAME[DEFAULT]
_SERIES_VARS = "".join(
    f"\n    --series-{i}: {hex};" for i, hex in enumerate(_DEFAULT["topics"], 1)
) + (
    f"\n    --series-link: {_DEFAULT['link']};"
    f"\n    --series-rest: {SERIES_REST};"
    f"\n    --bg: {_DEFAULT['bg']};"
    f"\n    --card: {_DEFAULT['card']};"
    f"\n    --surface: {_DEFAULT['surface']};"
    f"\n    --text: {_DEFAULT['text']};"
    f"\n    --muted: {_DEFAULT['muted']};"
)

# The whole design system. Page-specific rules (mostly `.badge.<kind>` modifiers)
# are appended by the caller, so the palette below stays the single source of the
# colours and every page's badges are drawn from the same six.
STYLE = """  :root {
    /* Ink and paper are aliases onto the palette's own tokens, so switching palette moves
       the ground and the type along with the marks. The contrasts in series.css were
       measured against that ground; hues on somebody else's paper are an unchecked claim. */
    --ink: var(--text);
    --paper: var(--bg);
    --yellow: #ffe500;
    --cyan: #4de1ff;
    --pink: #ff6ec7;
    --lime: #b6ff3c;
    --orange: #ff8a3d;
    --shadow: 6px 6px 0 var(--ink);
    /* jaan.io's motion tokens, same names and same curve, so the pages move alike. */
    --dur-fast: 200ms;
    --ease-standard: cubic-bezier(0.4, 0, 0.2, 1);
__SERIES__
  }
  /* Hack, jaan.io's monospace (--font-mono), from the files the site serves under /fonts/: the
     same URLs its pages preload, so a framed page finds them already fetched. Opened anywhere
     that does not serve them, the stack falls through to ui-monospace. */
  @font-face { font-family: "Hack"; src: url("/fonts/hack-regular-subset.woff2") format("woff2");
               font-weight: 400; font-style: normal; font-display: optional; }
  @font-face { font-family: "Hack"; src: url("/fonts/hack-bold-subset.woff2") format("woff2");
               font-weight: 700; font-style: normal; font-display: optional; }
  @font-face { font-family: "Hack"; src: url("/fonts/hack-italic-subset.woff2") format("woff2");
               font-weight: 400; font-style: italic; font-display: optional; }
  @font-face { font-family: "Hack"; src: url("/fonts/hack-bolditalic-subset.woff2") format("woff2");
               font-weight: 700; font-style: italic; font-display: optional; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font: 15px/1.5 Hack, ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  h1, h2, h3 { font-family: Helvetica, Arial, sans-serif; font-weight: 900; margin: 0; }
  header {
    border-bottom: 4px solid var(--ink); background: var(--yellow);
    padding: 18px 20px; display: flex; flex-wrap: wrap; gap: 16px; align-items: baseline;
  }
  header h1 { font-size: 30px; letter-spacing: -1px; text-transform: uppercase; }
  header .meta { font-size: 13px; }
  .layout { display: grid; grid-template-columns: minmax(280px, 26%) minmax(0, 1fr); gap: 20px; padding: 20px; align-items: start; }
  @media (max-width: 860px) { .layout { grid-template-columns: minmax(0, 1fr); } }

  /* A grid item defaults to min-width:auto, so one long unbroken line — a URL in a
     note or a message — widens the column past the viewport. Let the column shrink. */
  .panel { background: var(--card); border: 4px solid var(--ink); box-shadow: var(--shadow); min-width: 0; }
  .panel > h2 {
    font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
    padding: 8px 12px; border-bottom: 4px solid var(--ink); background: var(--cyan);
  }
  .controls { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
  input[type=search], select {
    font: inherit; padding: 8px 10px; border: 3px solid var(--ink);
    background: var(--paper); width: 100%;
  }
  input[type=search]:focus, select:focus { outline: 4px solid var(--pink); outline-offset: 2px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    font: inherit; font-size: 12px; padding: 4px 8px; cursor: pointer;
    border: 3px solid var(--ink); background: var(--card); text-transform: lowercase;
  }
  .chip[aria-pressed="true"] { background: var(--lime); box-shadow: 3px 3px 0 var(--ink); }

  /* Tall enough to use the window, and it scrolls past that. The gradients at the
     top and bottom edges signal there is more rather than letting a long list look
     truncated — they are `background-attachment: local`, so they only show on the
     side that actually has more content. */
  #names {
    max-height: 78vh; overflow-y: auto; border-top: 4px solid var(--ink);
    background:
      linear-gradient(var(--card) 30%, rgba(244,241,228,0)) top / 100% 24px no-repeat,
      linear-gradient(rgba(244,241,228,0), var(--paper) 70%) bottom / 100% 24px no-repeat;
    background-attachment: local, local;
  }
  .name-row {
    width: 100%; text-align: left; font: inherit; cursor: pointer; background: var(--card);
    border: 0; border-bottom: 2px solid var(--ink); padding: 8px 12px;
    display: flex; justify-content: space-between; gap: 10px; align-items: center;
  }
  .name-row:hover { background: var(--yellow); }
  .name-row[aria-current="true"] { background: var(--pink); }
  .name-row .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .count { font-size: 12px; border: 2px solid var(--ink); padding: 0 6px; background: var(--paper); }

  .detail { padding: 16px; }
  .detail h2.title { font-size: 32px; letter-spacing: -1px; word-break: break-word; }
  .facts { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 4px; }
  .badge { font-size: 12px; padding: 3px 8px; border: 3px solid var(--ink); background: var(--paper); }

  .hist { display: flex; align-items: flex-end; gap: 3px; height: 60px; margin: 16px 0 6px; }
  .hist div { flex: 1; background: var(--ink); min-height: 2px; }
  .hist-axis { display: flex; justify-content: space-between; font-size: 11px; }

  .mention { border: 3px solid var(--ink); margin-top: 14px; background: var(--paper); }
  .mention .head {
    padding: 6px 10px; border-bottom: 3px solid var(--ink); background: var(--card);
    display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px;
  }
  .mention .head .path { font-weight: 700; word-break: break-all; }
  .mention pre {
    margin: 0; padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere;
    font: inherit; font-size: 13px; max-height: 320px; overflow-y: auto;
  }
  mark { background: var(--yellow); border: 2px solid var(--ink); padding: 0 2px; }
  .empty { padding: 40px 16px; text-align: center; font-size: 14px; }
  kbd { border: 2px solid var(--ink); padding: 0 4px; background: var(--paper); font-size: 12px; }"""

# Substituted once, here, so every page that imports STYLE carries the series set in its own
# `:root` and a chart can read a slot by name instead of by a hex copied into three files.
STYLE = STYLE.replace("__SERIES__", _SERIES_VARS)

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
    <h2 id="tally">__COUNT__</h2>
    <div class="controls">
      <input type="search" id="q" placeholder="__SEARCH__" autocomplete="off">
      <div class="chips" id="kinds"></div>
      <select id="sort">
__SORTS__
      </select>
    </div>
    <div id="names"></div>
  </section>

  <section class="panel">
    <h2>__DETAIL_HEADING__</h2>
    <div class="detail" id="detail">
      <div class="empty">__EMPTY__</div>
    </div>
  </section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const listEl = document.getElementById("names");
const detailEl = document.getElementById("detail");
const qEl = document.getElementById("q");
const sortEl = document.getElementById("sort");
const kindsEl = document.getElementById("kinds");
const tallyEl = document.getElementById("tally");

// textContent, not innerHTML: a badge routinely carries a name straight out of a
// database, and that is somebody else's text.
function badge(text, cls) {
  const s = document.createElement("span");
  s.className = "badge " + (cls || "");
  s.textContent = text;
  return s;
}

__SCRIPT__

document.addEventListener("keydown", ev => {
  if (ev.key === "/" && document.activeElement !== qEl) { ev.preventDefault(); qEl.focus(); }
});
</script>
</body>
</html>
"""


def embed(payload: object) -> str:
    """Serialize the data block. `</` is escaped so a pasted `</script>` can't close the tag."""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render(*, title: str, heading: str, meta: str, count: str, search: str,
           sorts: list[tuple[str, str]], detail_heading: str, empty: str,
           script: str, data: object, extra_style: str = "") -> str:
    """
    Assemble a page.

    `meta` and `empty` are interpolated as raw HTML — they are page furniture the
    tool authors itself, never anything read out of a database. Everything that came
    from a database belongs in `data`, which is embedded as JSON and reaches the DOM
    only through text nodes.
    """
    options = "\n".join(
        f'        <option value="{value}">{label}</option>' for value, label in sorts
    )
    html = SHELL
    for token, value in (
        ("__TITLE__", title),
        ("__STYLE__", STYLE),
        ("__EXTRA_STYLE__", extra_style),
        ("__HEADING__", heading),
        ("__META__", meta),
        ("__COUNT__", count),
        ("__SEARCH__", search),
        ("__SORTS__", options),
        ("__DETAIL_HEADING__", detail_heading),
        ("__EMPTY__", empty),
        ("__SCRIPT__", script),
    ):
        html = html.replace(token, value)
    # Last, and only once: a note or message whose text contains __META__ is data,
    # not a template.
    return html.replace("__DATA__", embed(data))


