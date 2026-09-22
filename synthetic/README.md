# synthetic — a second vault, with nobody in it

Every chart in this repo draws private notes, which is why none of them can be shown to anybody.
This directory is the same four weeks of pipeline output with a life in it that does not exist:
a `microlite.md` of note edits, a sleep report, a ledger, the paragraph the writer types before
the week is read, and the letter Opus writes back. Same formats, byte for byte, as what the real
providers produce.

Point the rest of the repo at it with one environment variable:

```sh
PETROGRAPH_OUT=synthetic ./tools/connection_waffle.py
```

Every tool here already reads `PETROGRAPH_OUT` to find `sessions/`, so nothing downstream was
changed to make this work and nothing downstream can tell the difference. `just synth-pages`
does the four calls for you.

## The guarantee

**No text from the real vault ever reaches the model that writes this one.** Not a note, not a
title, not a name, not a sentence. What crosses the wall is sixty numbers.

The wall is a return type rather than a promise. `synth_profile.py` reads the real hunks and
asks Haiku for `HunkShape` — six integers, three booleans — and `baml_src/synth_profile.baml`
gives a name nowhere to go even if a model decided to be helpful. Everything else measurable is
measured in Python, which cannot be helpful. The counts are then averaged over every hunk in the
run before they are written down, so what survives describes a habit and not any hunk.

`baml_src/synth_write.baml` is the other side of that wall and has never seen the real vault.
Its inputs are `profile.json`, `style-guide.md`, a list of subjects, and — under `--story` — the
cast and the acts in `tools/_story.py`. Every name in this corpus was either invented by the
first call `synth_corpus.py` makes or is Shakespeare's.

Those first two are the only things here measured from a real notebook, and neither is committed:
they are rates about how one person writes, which is nobody else's to publish. `just
synth-profile` writes them from whatever vault you point it at, and the generators say which one
is missing rather than failing obscurely. The corpus in `vault/` and `sessions/` is committed and
regenerates from it — so what ships is the invented notebook, not the measurement behind it.

## Where the corpus comes from

`vault/` is the corpus. One file per note, holding the text that note held before the four weeks
began and then its full text at the end of each week it was touched in. Nothing in it is a diff:
`tools/synth_vault.py` runs `difflib` over consecutive states and hands the result to the same
`assemble` the generator used, so every `@@` header is arithmetic and every hunk id is a real
position in a real file. Edit a note, run `just synth-vault`, and the chart redraws.

The vault is composed against one fixed calendar — a real one, with real weekdays in it, which is
what lets a note say "tuesday is gothersgade" and be checkable — and shifted onto a live calendar
on the way out. The shift is always a whole number of weeks, so every day-name survives it, and
it lands the newest week about two months ahead of whenever you ran it. Then the year comes off:
the session folders are `mm-dd`, so are the sleep report and the ledger inside them, and so is
every date in every note. Nothing in the assembled corpus names a year. That is what stops a page
opened a year from now reading as an archive — and `pretty()` on the chart had always thrown the
year away anyway, so nothing downstream lost anything.

Two consequences worth knowing. The sleep and spending figures are drawn from the *reference*
date and only labelled with the live one, because the notes quote them to two decimal places and
they must not move when the dates do. And moving the dates invalidates the letters, which were
read off the old ones — `just synth-vault` says so, and `just synth-letters --force` fixes it.

Not every week is read on its Friday, because nobody's are. `_synth.HELD` holds the first read to
the Saturday and the second to the Sunday; the last two stay on the Friday, since week three's
opener is written the night before the drive to Roskilde and week four's letter is the one the
connectome reads. A held read names its folder, its header and its openers, and nothing else:
the sleep report and the ledger are still counted to the Friday, so their figures do not move,
and the edits keep the days they fell on — except the next week's earliest, which are pushed
past the read, because anything written by then was in it.

It is written by hand, and that is a change. `tools/synth_corpus.py` planned the first version
with a model, one note at a time, and got the *shape* right — the number of squares, the mix of
added and mixed and removed, filenames a person would really type. What a planner with no memory
cannot get right is continuity, and a connectome makes continuity extremely visible: the roof
footage was dated 4 March in six notes and 2 March in a seventh, the inquest was two years past
in one file and next month in another, and a `revise` landed a paragraph beside a near-identical
copy of itself often enough that clicking a square at random was a coin flip. None of that is
fixable by asking better. So the facts live in one place now, and the place can hold facts.

The generator is still here and still works. It is what measured the shape in the first place,
and a topic-by-topic corpus is the honest null hypothesis to compare a plotted one against —
if the connectome finds as many arrows in a vault with no story in it, the arrows are not
finding the story.

The four openers each week (`vault/_context-<date>.md`) are hand-written too, because they are
the interior of the week and the read downstream has nothing else to work from. The sleep report
and the ledger are still generated arithmetic, seeded, so the figures the notes cite are the
figures the attachments actually carry. **The letters are still written by Opus** from the
assembled week, with no idea what it is reading. That is the experiment and handing it the answer
would end it.

Only the fourth week gets one. The first three are what it is read against — Acts I to III, laid
down in the notes with enough in them that the fourth week's read has something to see coming —
and a letter for each of them was a letter nothing downstream used, and a place for the read to
have said already what the fourth ought to have to arrive at by itself. If the premonitory half
of that letter comes back thin, it is edited by hand, and the hand edit is the one that ships.

And of the connections that letter's strongest sentences draw, the page opens on ten, chosen
rather than sampled — two touching the ledger and two the sleep report, so the attachments are on
the first screen as well as the plot: each joins two different notes, both ends grounded in what their square
actually says, under two different topics where that can be had, ranked by the names and threads
in `_story.SALIENT` so the first screen is the one a reader would know the play by. The letter is
read against the hunks twice, at two excerpt lengths, and the two readings pooled — a model pins
a sentence on the wrong square often enough that one reading alone left too few clean arrows.
The rest are all still in `connections-<date>.json`, and the slider still reaches them.

## No content filter, on purpose

There used to be a gitignored `topics.conf` here: an allow list the corpus was written from, and
a deny list — psychiatry, mental health, grief, therapy — that no generated string could contain.
It is gone, and the reasoning for removing it is worth more than the reasoning for having had it.

The wall that keeps the real vault out of the generator is `synth_profile.py`'s **return type**,
which has no string field in it. That wall does not need help and never did. What the deny list
actually accomplished was stopping the synthetic writer from saying how they felt — which is the
one thing a private journal is for, and precisely the material the read downstream is asked to
interpret. A vault whose author may not be upset is not a safer test of the connectome. It is a
thinner one, and it was producing a month of admin with a person-shaped hole in the middle.

So the corpus is now free to be as plainly emotional as the person writing it would be. It is
still nobody: a modern Hamlet, four acts, four weeks, keeping a journal and a log of himself. The
only register rule left is in the prompts themselves — say feeling the way a person says it about
themselves in private, not the way a clinician describes them — and that one is there because a
corpus that hands the read its own diagnosis has nothing left to be read.

The subjects a run writes about come from `tools/_story.py` under `--story` and from
`_synth.SUBJECTS` otherwise. They are material, not a filter: nothing is checked against them,
and a note that wanders off the list is kept.

The weeks are anchored at a fixed date rather than counted back from today, and deliberately
nowhere near the real sessions'. A committed corpus that moves when you regenerate it is a diff
nobody can read, and a synthetic week sharing a date with a real one is a confusion waiting to
happen the first time somebody has both roots open.

## What is committed

Everything here. The corpus is the point: clone the repo and the connectome
opens without an API key, without a vault, and without anybody's week in it.
