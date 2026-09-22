# petrograph — recipes for tools, publishing, and audio.
# Run `just` to list.

gist_id := "274192aae91c915fccfbcef8594f9351"

# Audio lands beside the markdown it was made from, in sessions/<date>/. share/ holds only
# what belongs to no single session: the TTS chunk cache, and a sentinel `just eleven` drops
# on success. `just mix` is gated on that sentinel so you can't mix before a narration has
# been generated (enforces eleven-first). justfile_directory() keeps it repo-relative
# wherever just is invoked from.
share := justfile_directory() / "share"
eleven_ran := share / ".eleven-ran"

default:
    @just --list

# Narrate a compaction markdown file → an mp3 beside the markdown
tts file voice="katie":
    ./tools/compact_tts.py "{{file}}" --voice "{{voice}}"

# List available Cartesia voices to map to a UI
voices:
    ./tools/compact_tts.py --list-voices

# Narrate a markdown file via ElevenLabs — chunked + leveled, beside the markdown
# 2nd arg = backing track(s), comma-separated for several — they play in order, crossfade into
# each other, and the sequence loops (also writes the ducked master through the chain);
# 3rd arg = sentence-pause dashes (default 1 = a little breathing room; 0 = off; 2 = more);
# 4th arg = voice id; 5th arg = mixer engine, oss (default) or vst (the licensed plug-ins).
eleven file music="" pause="1" voice="WeAAwKYcS06VmXw086yZ" engine="oss":
    ./tools/eleven_tts.py "{{file}}" --voice "{{voice}}" --chunk 300 --normalize --sentence-pause {{pause}} --mix-engine {{engine}} {{ if music != "" { "--music '" + music + "'" } else { "" } }}
    @mkdir -p "{{share}}" && touch "{{eleven_ran}}"

# Same output, same name, same hand-off to the mixer as `just eleven`; the difference is that
# the letter never leaves the disk. Slower, and the first run downloads the model weights.
# 2nd arg = backing track(s), comma-separated, exactly as in `just eleven`;
# 3rd arg = a reference clip to clone the voice from (default: share/voices/victoria.wav if
# you have captured one, otherwise Chatterbox's built-in speaker — which is not Victoria);
# 4th arg = mixer engine, oss (default) or vst.
# Pauses are real silence rather than pause hyphens, so there is no pause argument here — pass
# --header-gap/--bullet-gap/--newline-gap/--sentence-gap to the tool directly to retune them.
# Narrate a markdown file on this machine — nothing is sent anywhere. The privacy path.
chatterbox file music="" sample="" engine="oss":
    ./tools/chatterbox_tts.py "{{file}}" --normalize --mix-engine {{engine}} {{ if sample != "" { "--voice-sample '" + sample + "'" } else { "" } }} {{ if music != "" { "--music '" + music + "'" } else { "" } }}
    @mkdir -p "{{share}}" && touch "{{eleven_ran}}"

# Chatterbox has no voice catalogue: any voice other than its built-in one is a 10-20s clip of
# somebody speaking, and this lifts one out of a recording you already have.
# 2nd arg = seconds to take; 3rd = where to start, to skip a quiet or noisy opening.
# Read tools/chatterbox_tts.py's header first on where a reference clip should come from —
# cloning a commercial voice you licensed from somebody else is a decision, not a default.
# Cut a reference clip for `just chatterbox` out of existing audio → share/voices/victoria.wav
voice-sample audio seconds="20" start="0":
    ./tools/chatterbox_tts.py --capture-reference "{{audio}}" --reference-seconds {{seconds}} --reference-start {{start}}

# Lay a narration (mp3/wav/flac) over a ducked, looping backing bed → the master beside it
# 2nd arg takes several tracks, comma-separated: they play in order and the sequence loops.
# Gated: refuses to run until `just eleven` or `just chatterbox` has produced a narration.
# 4th arg = engine, oss (default) or vst. `out` is positional, so pass it empty to reach it:
#   just mix narration.mp3 track.flac "" vst
mix voice music out="" engine="oss":
    @test -f "{{eleven_ran}}" || { echo "Refusing to mix: run 'just eleven' or 'just chatterbox' first — mixing is gated on a narration having been generated." >&2; exit 1; }
    ./tools/mix_music.py "{{voice}}" --music "{{music}}" --engine {{engine}} {{ if out != "" { "--out '" + out + "'" } else { "" } }}

# Calls nothing, bills nothing, and needs no API key. Chunks already in the cache are measured
# off the audio rather than estimated, so re-checking a letter you have rendered is exact
# rather than approximate.
# 2nd arg = sentence-pause dashes; match the `just render` you mean to run, since the pause
# hyphens are spoken time and the answer moves with them. Ignored under chatterbox, whose
# beats are silence with their own defaults.
# 3rd arg = optional backing track(s), comma-separated as in `just render` — reports the bed's
# length and how many times it would loop under the letter, which is the number you want when
# deciding how much music to line up.
# 4th arg = which engine to price, as in `just render`. Under chatterbox there is nothing to
# bill and the answer is wall-clock instead, so what you get back is the length and the chunk
# count rather than a cost.
# How long a markdown file will run as narration, and what it would cost to render
estimate md pause="1" music="" tts="":
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(./tools/_speech.py --sh "{{tts}}")"
    if [ "$engine" = "eleven" ]; then
        pause_arg=(--chunk 300 --sentence-pause {{pause}})
    else
        pause_arg=()
    fi
    "$tts_tool" "{{md}}" ${pause_arg[@]+"${pause_arg[@]}"} --dry-run {{ if music != "" { "--music '" + music + "'" } else { "" } }}

# Writes three files beside the markdown it narrates, and prints every one at the end:
#   lifelog-<date>.mp3            the master — voice + ducked music, through the tape. Play this.
#   lifelog-<date>-voice.wav      the narration on its own, 24-bit — the master is the one encode
#   lifelog-<date>-stem.wav       colored voice on its own, 24-bit, for re-mixing
# 1st arg = a markdown file, normally sessions/<date>/lifelog-<date>.md. Inside sessions/ the
# folder names the audio, so every week's letter gets its own mp3 and the whole
# session — what you fed Claude, the letter, the audio — stays one flat folder. The letter
# carries that name already, since the audio is the one thing that leaves the folder.
# Any other markdown in the session is a second thing to narrate rather than the letter, and
# brings its own name along: lifelog-<date>-metaphors.md → lifelog-<date>-metaphors.mp3, beside
# the markdown as always. Refuses to run while the file is still the stub `just weekly` left
# there — and asks, loudly, before replacing a render already in the folder. Audio is
# gitignored, so a master written over is gone; PETROGRAPH_OVERWRITE=1 answers yes in advance
# for an unattended run that means to replace what is there.
# 2nd arg = backing track(s). Comma-separate for several: they play in order, each crossfading
# into the next, and the whole sequence loops if the narration outlasts it — so two tracks go
# A, B, A, B rather than stranding you in B. Quote the whole list; paths may contain spaces,
# and commas too — the separator is resolved against the disk, so the longest run that names
# a real file wins, which is what album folders like `{Label Co., Ltd. TECD-1}` need.
# 3rd arg = sentence-pause dashes; 4th = voice id; 5th = mixer engine, oss (default) or vst.
# Header/bullet/newline pauses use eleven_tts.py's own defaults (3/2/2) — call the tool
# directly to override them.
# 6th arg = who narrates: eleven (the default, and what every session so far was rendered
# with) or chatterbox, which synthesises on this machine and sends the letter nowhere. Leave
# it empty for whatever $PETROGRAPH_TTS_ENGINE says, and eleven if it says nothing. Under
# chatterbox the 3rd and 4th args do not apply — pauses are silence rather than hyphens, and
# the voice is share/voices/victoria.wav if you have captured one; `just chatterbox` takes a
# different clip.
# Synthesised chunks are cached under share/.tts-cache as they come back, so a run that dies
# partway (quota, network) keeps what it paid for: re-run the same command and only the
# missing chunks are billed. `./tools/eleven_tts.py <md> --chunk 300 --dry-run` prices it first.
# END-TO-END: markdown → narration → voice chain → ducked backing track → tape master buss
render md music pause="1" voice="WeAAwKYcS06VmXw086yZ" engine="oss" tts="":
    #!/usr/bin/env bash
    set -euo pipefail
    md="{{md}}"
    case "$md" in *.md) ;; *) echo "render takes a markdown file, not $md" >&2; exit 1 ;; esac
    [ -f "$md" ] || { echo "No such file: $md" >&2; exit 1; }
    dir=$(cd "$(dirname "$md")" && pwd)
    # Refuse to spend TTS credits narrating the placeholder. The marker is asked for rather
    # than spelled out here: it used to be a third hand-synchronised copy of the string, and
    # a drifted copy means this guard silently stops guarding.
    if grep -qF "$(./tools/_session.py --stub-marker)" "$md"; then
        echo "Refusing to render: $md is still the stub — paste Claude's letter into it first." >&2
        exit 1
    fi
    # Everything lands beside the markdown that produced it, so a session is one flat folder:
    # the files you fed Claude, the letter, and the audio made from it. Which name is which is
    # tools/_names.py's decision, not this recipe's — this was a fifth copy of that rule, and
    # the copies had already drifted. Sets stem, voice, narration, mix_mp3 and voice_stem.
    eval "$(./tools/_names.py --sh "$md")"
    # A render that would land on files already in the folder asks first — audio is
    # gitignored, so an overwritten master is not somewhere it can be got back from. Asked
    # here, before a character is billed; a yes rides down to the narrator and the mixer in
    # the environment, so neither asks again about these same three files.
    ./tools/_overwrite.py "$voice" "$mix_mp3" "$voice_stem"
    export PETROGRAPH_OVERWRITE=1
    # …and which engine narrates is _speech.py's decision, for the same reason: the flag beats
    # the environment beats eleven, and that rule should exist once rather than once here too.
    # Sets engine (eleven|chatterbox) and tts_tool (the script that is it).
    eval "$(./tools/_speech.py --sh "{{tts}}")"
    t0=$(date +%s)
    echo "==> 1/2  narrate  $(basename "$md") → $(basename "$voice")  ($engine_label, $engine_where)"
    # --no-compress, either way: this narration feeds the mixer, whose compressor does the
    # dynamics. Compressing here too squeezes the voice through two unrelated 4:1/3.5:1 stages.
    if [ "$engine" = "eleven" ]; then
        # An empty voice means "the tool's own default", the way `mix`'s empty `out` does — so
        # `just render <file> <track> 1 "" vst` can reach the engine without naming a voice.
        if [ -n "{{voice}}" ]; then voice_arg=(--voice "{{voice}}"); else voice_arg=(); fi
        "$tts_tool" "$md" ${voice_arg[@]+"${voice_arg[@]}"} --chunk 300 --normalize --no-compress \
            --sentence-pause {{pause}} --out "$voice"
    else
        # No voice id and no pause dashes over here: the local engine has no catalogue to look
        # an id up in, and its beats are real silence with their own millisecond defaults.
        "$tts_tool" "$md" --normalize --no-compress --out "$voice"
    fi
    mkdir -p "{{share}}" && touch "{{eleven_ran}}"
    t1=$(date +%s)
    echo
    echo "==> 2/2  voice chain ({{engine}}) → duck → tape master buss → ${stem}.mp3"
    ./tools/mix_music.py "$voice" --music "{{music}}" --engine {{engine}} \
        --out "$mix_mp3" --voice-stem "$voice_stem"
    t2=$(date +%s)
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$mix_mp3")
    echo
    echo "Done — in ${dir#{{justfile_directory()}}/}/, beside the markdown they came from:"
    # file:// URLs are cmd-clickable in Terminal.app and iTerm2 alike; the tools themselves
    # emit OSC 8 hyperlinks where the terminal supports them.
    for f in "$mix_mp3" "$voice" "$voice_stem"; do
        printf "  %-44s %7.1f MB  %6.1fs\n      file://%s\n" "$(basename "$f")" \
            "$(echo "scale=1; $(stat -f%z "$f")/1048576" | bc)" \
            "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")" "$f"
    done
    echo
    echo "timing — end to end:"
    printf "  %-22s %5ds\n" "1/2 narrate (API)" "$((t1-t0))"
    printf "  %-22s %5ds\n" "2/2 mix + master" "$((t2-t1))"
    printf "  %-22s %5ds\n" "──────────────────────" "$((t2-t0))"
    printf "  %-22s %5.1fx realtime  (%.0fs of audio)\n" "" \
        "$(echo "scale=2; $((t2-t0))/$dur" | bc)" "$dur"

# The second half of a render you have already paid for: same narration, mixed again.
# Changing the engine, the music, or any of mix_music.py's knobs changes the master and the
# voice-chain stem — it does not change the narration, which is what the API was billed for.
# `just render` would re-run the narrator anyway (free from share/.tts-cache if nothing about
# the text, the voice or the pauses moved, billed in full if anything did), so the way to be
# sure a remix costs nothing is not to call the narrator at all.
# Takes the *markdown*, not the mp3, so it resolves the same names `render` did — hand it the
# session you rendered and it finds the narration beside it. The voice file is only read here;
# the two files rewritten are the master and the stem, and those are what it asks about.
# A narration that is not there is an error rather than an API call — that case is `just render`.
# 3rd arg = engine, oss (default) or vst, exactly as in `render` and `mix`.
#   just remix sessions/2026-05-29/lifelog-2026-05-29.md track.flac vst
# Re-mix an existing narration → a new master + stem beside it. No API, nothing billed.
remix md music engine="oss":
    #!/usr/bin/env bash
    set -euo pipefail
    md="{{md}}"
    case "$md" in *.md) ;; *) echo "remix takes the markdown the narration was made from, not $md" >&2; exit 1 ;; esac
    [ -f "$md" ] || { echo "No such file: $md" >&2; exit 1; }
    # The same rule render used to decide where the narration went — asked, not remembered.
    eval "$(./tools/_names.py --sh "$md")"
    # narration is the lossless -voice.wav, or the -voice.mp3 a render from before that left.
    if [ ! -f "$narration" ]; then
        echo "No narration at ${narration#{{justfile_directory()}}/} — nothing to remix." >&2
        echo "That is what 'just render' is for; this recipe never calls the API." >&2
        exit 1
    fi
    # Only the two files this rewrites are named here. The narration is an input now, so it is
    # not in the list — the prompt that used to lead with a 20 MB narration was asking about a
    # file that was never at risk.
    ./tools/_overwrite.py "$mix_mp3" "$voice_stem"
    export PETROGRAPH_OVERWRITE=1
    t0=$(date +%s)
    echo "==> voice chain ({{engine}}) → duck → tape master buss → $(basename "$mix_mp3")"
    ./tools/mix_music.py "$narration" --music "{{music}}" --engine {{engine}} \
        --out "$mix_mp3" --voice-stem "$voice_stem"
    t1=$(date +%s)
    dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$mix_mp3")
    echo
    echo "Done — in $(dirname "${md#{{justfile_directory()}}/}")/, from the narration already there:"
    for f in "$mix_mp3" "$voice_stem"; do
        printf "  %-44s %7.1f MB  %6.1fs\n      file://%s\n" "$(basename "$f")" \
            "$(echo "scale=1; $(stat -f%z "$f")/1048576" | bc)" \
            "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")" "$f"
    done
    echo
    printf "  %-22s %5ds  (%.1fx realtime, nothing billed)\n" "mix + master" "$((t1-t0))" \
        "$(echo "scale=2; $((t1-t0))/$dur" | bc)"

# Runs the commands in providers.conf (copy providers.example.conf to make one), collects the
# markdown each writes into sessions/<date>/, and points `attachments:` at them.
# A provider that fails costs you its attachment, not the run — failures are listed at the end
# and the exit is nonzero. Nothing here knows what a provider produces: petrograph runs a
# command and inlines a markdown file, so the private repos keep their own data and their own
# credentials. Args pass through: --dry-run, --days N, --only microlite, --skip browsing —
# and a filtered run merges into the session rather than replacing what is already wired.
# Four of them decide the session rather than the providers, and are written into its
# front-matter so they hold for every later command on that week:
#   --llm opencode           opencode writes the read and the letter instead of claude, and
#                            `model:` is blanked, since a Claude alias means nothing over there
#   --tts-engine chatterbox  the letter is narrated on this machine rather than posted to
#                            ElevenLabs (a path here is a narrator of your own)
#   --music PATH[,PATH…]     a backing bed under the narration, so the week's master is
#                            voice over music without hand-editing `music:` (repeat the
#                            flag, or comma-separate, for several — they play in order and
#                            loop; the path is checked now, not after both model calls)
#   --blank-context          a session that needs no edits: a stand-in title, and a week
#                            context that says none was written — the read then works from the
#                            diff and the attachments alone
# All four apply to a session being scaffolded. Re-running against a session that already
# exists leaves them as that session set them, and says so.
# END TO END, the front half: context providers → diff + attachments → this week's session
weekly *args:
    ./tools/weekly_context.py {{args}}

# Fill in the title, the interlocutor, and the freeform week-context paragraph in the file it
# writes, then hand that file to `review-and-narrate`. Won't overwrite an input that exists.
# `just weekly` calls this for you — reach for it directly only when assembling a week by hand.
# Scaffold a bare session → sessions/<date>/edit-me.md (+ a lifelog-<date>.md stub)
apply-review-template slug="compaction" template="sessions/TEMPLATE.md":
    ./tools/weekly_review.py --new --slug "{{slug}}" --template "{{template}}"

# The letter is written by resuming the session that produced the read, so it has the diff and
# the analysis in context — the property the copy-paste flow relied on. Prints every file made.
# 2nd arg passes through to the tool: `--dry-run` (bundle + cost, no API calls) first if you
# want to see what is being sent, `--skip-narration` for the paid path without the TTS spend,
# `--resume` to redo only the letter and the narration after one of them failed, `--llm
# opencode` to have `opencode` write the read and the letter instead of `claude`, and
# `--tts-engine chatterbox` to have the letter narrated on this machine instead of posted to
# ElevenLabs. That last one is also settable per session with an `engine:` line in its
# front-matter, or for good with PETROGRAPH_TTS_ENGINE=chatterbox in .env. The default,
# unset everywhere, stays eleven. The two choices are independent: `--llm` picks who writes
# stages 2 and 3, `--tts-engine` picks who reads the finished letter aloud in stage 4.
# END TO END: the week's diff → ACT read → compaction letter → narration, unattended
review-and-narrate input *args:
    ./tools/weekly_review.py "{{input}}" {{args}}

# The hunks alone, to an explicit path. `just weekly --only microlite` is the same call made
# as a provider — it names the output for you and wires it into the session's `diff:` key,
# which is how the review consumes it. Reach for this one to look at the week on its own.
# Generated here from Obsidian's File Recovery snapshots, by the same reader the Microlite
# plugin is a port of, so it needs nothing running and matches what the plugin would show.
# The week's Obsidian hunks → the path you give it (wired into nothing)
hunks out days="7":
    ./tools/microlite_hunks.py --days {{days}} --out "{{out}}"

# Unknown flags pass through to the reader, so this is `just hunks` in patch form: a git-style
# multi-file diff instead of markdown, which is what hunk wants.
# View the week's Obsidian hunks in hunk (github.com/modem-dev/hunk)
view since="7":
    ./tools/microlite_hunks.py --days {{since}} --out - --format patch | hunk patch -

# Not this week but every week: the archived microlite.md in each session directory, drawn as
# one waffle column per week. Two views a chip swaps between: a square per hunk, so the column
# counts edits, or a square per round number of characters added or removed, so the column
# weighs them and a rewritten page is a block. Those files are the only surviving record —
# File Recovery prunes, so a week that was never reviewed cannot be redrawn — which is why this
# reads them rather than the reader. Self-contained page, no network. Args pass through: --out,
# --view, --chars-per-square, --per-row, --hunks-only, or explicit paths to microlite.md files.
# Every archived week's hunks as a waffle chart → sessions/microlite-waffle.html
waffle *args:
    ./tools/microlite_waffle.py {{args}}

# The same chart and the same two views, drawn by the library the form came from and clickable
# the same way: the rows live in DuckDB-WASM, which fans each hunk out into the squares it is
# owed, a click publishes to a Mosaic Selection, and the diff panel is a MosaicClient answering
# it with a query. `import` reaches a CDN, so unlike every other page
# here this one wants a network; it is a separate recipe for that reason, and because it shows
# diffs it holds the same private note text `just waffle` does.
# The waffle again, drawn by Mosaic → sessions/microlite-waffle-mosaic.html
waffle-mosaic *args:
    ./tools/microlite_waffle.py --mosaic {{args}}

# Regenerate the BAML client from baml_src/. Needed after editing a .baml file, and on a fresh
# checkout, since tools/baml_sdk is gitignored. The toolchain is pinned to the version that
# matches the `baml-bridge` the tools depend on — a mismatched pair fails at import with a
# version-skew error rather than at generate time, which is a confusing place to find out.
# Regenerate the typed BAML client → tools/baml_sdk
baml:
    baml toolchain use 0.17.0
    baml check
    baml generate

# Reads that week's letter back against the hunks it was written from and asks Haiku which
# pairs a sentence joined. Cached under share/.baml-cache by the letter, the hunks, the context
# and the prompt's own source, so a re-run costs nothing and an edited prompt correctly misses.
# --dry-run says hit or miss before you spend; --no-cache asks again anyway.
# The connections a week's letter drew between hunks → sessions/<date>/connections-<date>.json
connections *args:
    ./tools/hunk_connections.py {{args}}

# One square per unit — every hunk, plus the week's own sleep report and ledger — and one arrow
# per connected pair, violet within a week and orange across them. The slider shows only the
# strongest by default. Click an arrow for the sentences behind it and a rationale for each end;
# its two squares are outlined and both diffs open. Needs a network, like `just waffle-mosaic`.
# Draw those connections → sessions/connections-<date>.html
connections-page *args:
    ./tools/connection_waffle.py {{args}}

# One Haiku call per unit, in order, each shown the topics found so far so it can reuse one or
# name a new one. Two are fixed — finance and physiology — and the rest are discovered. Two
# hundred calls is a few minutes the first time and nothing after; the pass is cached whole.
# A topic for every unit → sessions/<date>/topics-<date>.json
topics *args:
    ./tools/unit_topics.py {{args}}

# The same connectome, coloured by topic instead of by what the edit did. Squares are sorted by
# topic so each one is a contiguous band, which is the adjacency the palette was validated for.
# The connectome coloured by topic → sessions/connections-<date>-topics.html
topics-page *args:
    ./tools/connection_waffle.py --color-by topic {{args}}

# One week's topic connectome, drawn against the three weeks before it: the letter read into
# connections, every unit given one of at most ten topics, and the page. Everything lands under
# that week's own name — sessions/<week>/{connections,topics}-<week>.json and
# sessions/connections-<week>-topics.html — so an earlier week's page is never overwritten.
# `just week-topics 2026-06-12`
week-topics week:
    ./tools/hunk_connections.py --week {{week}} --weeks 4
    ./tools/unit_topics.py --week {{week}} --max-topics 10
    ./tools/connection_waffle.py --week {{week}} --color-by topic

# END TO END: ask Haiku for the connections, then draw them.
connect *args:
    ./tools/hunk_connections.py {{args}}
    ./tools/connection_waffle.py

# Long-window corpus for entity extraction → corpus/vault-corpus-<date>-<N>mo.{md,jsonl}
# Layered and provenance-tagged; File Recovery only reaches ~60 days, so the rest is
# reconstructed from the vault (see the `Coverage` section of the output).
corpus months="12":
    ./tools/vault_corpus.py --months {{months}}

# Exact diffs only — no reconstruction. Thin until the mirror has a few snapshots.
corpus-exact months="12":
    ./tools/vault_corpus.py --months {{months}} --layers git,recovery

# Commit the vault's markdown to the local git mirror — the only way to accumulate real
# long-window diffs, since File Recovery prunes. Run it on whatever cadence you want history at.
vault-snapshot:
    ./tools/vault_snapshot.sh

# Price the extraction before spending anything (chunk count + token estimate)
names-cost corpus="":
    ./tools/extract_names.py {{corpus}} --dry-run

# Extract every name from the corpus via Haiku 4.5, chunk by chunk (cached + resumable)
names corpus="":
    ./tools/extract_names.py {{corpus}}

# Render the extracted names as a browsable page: click a name → its chunks, notes, dates
names-html names="":
    ./tools/names_html.py {{names}}

# END TO END: corpus → names → page. Re-runs are near-free (chunk cache).
names-render months="12":
    #!/usr/bin/env bash
    set -euo pipefail
    ./tools/vault_corpus.py --months {{months}}
    ./tools/extract_names.py "corpus/vault-corpus-$(date +%F)-{{months}}mo.jsonl"
    ./tools/names_html.py "corpus/names-$(date +%F)-{{months}}mo.json"

# Most-contacted people from Messages + WhatsApp → messages/contacts-<date>-<N>mo.json
# Read-only: every database is opened through the immutable URI, never written or copied.
# 2nd arg trims the sources, e.g. `just contacts 12 imessage`.
contacts months="12" sources="imessage,whatsapp":
    ./tools/extract_contacts.py --months {{months}} --sources {{sources}}

# Render the contacts as a browsable page: click a contact → last 2 messages, months, services
contacts-html contacts="":
    ./tools/contacts_html.py {{contacts}}

# END TO END: message databases → contacts → page. No API calls; nothing leaves the machine.
contacts-render months="12" sources="imessage,whatsapp":
    #!/usr/bin/env bash
    set -euo pipefail
    stamp=$(date +%F)
    ./tools/extract_contacts.py --months {{months}} --sources {{sources}} \
        --out "messages/contacts-$stamp-{{months}}mo.json"
    ./tools/contacts_html.py "messages/contacts-$stamp-{{months}}mo.json"

# Take a dated baseline into messages/snapshots/ — the thing a future run diffs against.
# Never committed (messages/ is gitignored); it holds real message text.
contacts-snapshot months="12" sources="imessage,whatsapp":
    #!/usr/bin/env bash
    set -euo pipefail
    stamp=$(date +%F)
    ./tools/extract_contacts.py --months {{months}} --sources {{sources}} \
        --out "messages/snapshots/contacts-$stamp-{{months}}mo.json"

# Read-only and local: the history db is opened immutable (so this works while the browser
# is running), nothing is copied, no network. One row per site per hour — visits, time on
# page, and the commonest page titles, busiest site first — rather than a log of every URL,
# which is a rough sense of the week rather than an enumeration of it. Point this week's
# `attachments:` at the result — nothing in the review flow changes, it just reads one file
# more, and both browsers can be attached at once since the filename carries the browser.
# Firefox records no time on page, so its file leaves those columns out.
# 3rd arg = profile: a directory name for Chrome ("Profile 1"), the name you know it by for
# Firefox ("default-release"); empty means Chrome's Default / the profile Firefox launches.
# The week's browsing → browsing/browsing-<browser>-<date>-7d.md, grouped by hour and site
summarize-browsing days="7" browser="chrome" profile="":
    ./tools/browsing_history.py --days {{days}} --browser {{browser}} {{ if profile != "" { "--profile '" + profile + "'" } else { "" } }}

# What changed between the two newest snapshots: who you talk to more, less, or not at all
contacts-diff old="" new="":
    ./tools/contacts_diff.py {{old}} {{new}}

# Normalize note checkboxes in ~/notes (dry-run by default; pass --apply to commit)
normalize *args:
    ./tools/normalize-checkboxes.sh {{args}}

# The script itself lives in obsidian-microlite and is pinned here as a submodule, so this
# publishes that repo's copy — `git submodule update --remote` first if you want the newest.
# Publish the File Recovery reader to the public gist (one-way)
publish-gist:
    gh gist edit {{gist_id}} -f vendor/obsidian-microlite/manual/hunking_obsidian.py

# Confirm what the public gist currently exposes
gist-status:
    gh gist view {{gist_id}} --files

# Runs every stage the real pipeline runs — the Obsidian reader, the session scaffold, the
# bundle assembly, the narration, the voice chain, the duck, the tape master — against a
# temp directory that is deleted afterwards. sessions/ is never touched.
# Costs nothing and needs nothing: no API key, no network, no music library. The narration
# comes from chunks already in share/.tts-cache and the backing track is a sine wave ffmpeg
# generates on the spot. Before narrating anything it prices the letter and REFUSES to
# continue if a single character would be billed, so this can never turn into a purchase.
# 1st arg = `quick` to stop before the mix, which is ~95s of the ~105s.
# 2nd arg = the letter to narrate; default is the newest already-cached one in sessions/.
# END TO END, spending nothing: every stage, on cached audio, in a temp directory
smoke mode="full" letter="":
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{justfile_directory()}}"
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    today=$(date +%F)

    echo "==> 1/5  the tools load"
    python3 -m py_compile tools/*.py
    (cd tools && python3 -c "import _dates,_db,_env,_names,_numbers,_overwrite,_page,_session,_speech,_term")
    ./tools/_numbers.py | tail -1 | sed 's/^/    /'
    echo "    $(ls tools/*.py | wc -l | tr -d ' ') modules compile, 10 shared modules import"

    echo "==> 2/5  the week's Obsidian hunks"
    ./tools/microlite_hunks.py --days 7 --out "$tmp/microlite.md" 2>&1 | tail -1

    echo "==> 3/5  scaffold a session"
    ./tools/weekly_review.py --new --out-root "$tmp" --slug smoke >/dev/null
    edit="$tmp/sessions/$today/edit-me.md"
    # Fill the template the way a person would, so the unfilled-body guard lets it past.
    python3 - "$edit" "$tmp/microlite.md" <<'PY'
    import re, sys
    p, diff = sys.argv[1], sys.argv[2]
    head, body = open(p).read().split("---\n", 2)[1:]
    head = head.replace("<short label>", "smoke test")
    head = re.sub(r"^diff:.*$", f"diff: {diff}", head, count=1, flags=re.M)
    body = "\n".join(l for l in body.splitlines() if not l.startswith("<"))
    open(p, "w").write(f"---\n{head}---\n\n{re.sub(chr(10)+'{3,}', chr(10)*2, body).strip()}\n"
                       "A smoke-test week: nothing happened, which is the point.\n")
    PY
    echo "    $(basename "$edit") filled"

    echo "==> 4/5  assemble the bundle (no API calls)"
    ./tools/weekly_review.py "$edit" --dry-run 2>&1 | tail -1
    test -s "$tmp/sessions/$today/bundle.md" || { echo "    no bundle written" >&2; exit 1; }
    echo "    bundle.md — $(wc -c < "$tmp/sessions/$today/bundle.md" | tr -d ' ') bytes"
    # The other backend. First the guard a session flipped to opencode meets: the template
    # names a Claude model, and `provider/model` is what opencode takes.
    if ./tools/weekly_review.py "$edit" --dry-run --llm opencode >/dev/null 2>&1; then
        echo "    --llm opencode accepted a Claude model alias" >&2; exit 1
    fi
    echo "    --llm opencode refuses the template's Claude model alias"
    # Then the same bundle priced through it, with the model left to opencode's own config.
    # A dry run reaches neither CLI, so this passes on a machine with no opencode installed.
    python3 -c "import re,sys;p=sys.argv[1];f=open(p).read();open(p,'w').write(re.sub(r'^model:.*$','model:',f,count=1,flags=re.M))" "$edit"
    ./tools/weekly_review.py "$edit" --dry-run --llm opencode >/dev/null
    echo "    --llm opencode prices the same bundle, reaching no CLI"

    letter="{{letter}}"
    if [ -z "$letter" ]; then
        marker=$(./tools/_session.py --stub-marker)
        # The letter is named for the folder it sits in, which no glob can express, so the
        # list is asked for — newest first — the same way the marker above is.
        letter=$(./tools/_session.py --letters sessions/*/ 2>/dev/null \
                 | while read -r f; do grep -qF "$marker" "$f" || { echo "$f"; break; }; done)
    fi
    if [ -z "$letter" ]; then
        echo "==> 5/5  narration SKIPPED — no written letter in sessions/ to narrate"
        echo; echo "Stages 1-4 passed."; exit 0
    fi

    # The one thing this recipe must never do is spend money. A letter whose chunks are all
    # in the cache costs nothing to re-render; anything else is refused rather than billed.
    priced=$(./tools/eleven_tts.py "$letter" --chunk 300 --sentence-pause 1 --dry-run 2>&1)
    if ! grep -qE '(^|[^0-9])0 chunk\(s\) would be synthesised' <<<"$priced"; then
        echo "==> 5/5  narration SKIPPED — $(basename "$(dirname "$letter")") is not fully cached:"
        echo "$priced" | grep -E 'would be synthesised' | sed 's/^/     /'
        echo "     Re-run with a cached letter, or narrate it yourself with \`just render\`."
        echo; echo "Stages 1-4 passed."; exit 0
    fi

    smoke_letter="$tmp/sessions/$today/lifelog-$today.md"
    cp "$letter" "$smoke_letter"
    ffmpeg -v error -y -f lavfi -i "sine=frequency=220:duration=90" -ac 2 "$tmp/track.wav"
    if [ "{{mode}}" = "quick" ]; then
        echo "==> 5/5  narrate from cache (mix skipped — pass no argument for the full run)"
        ./tools/eleven_tts.py "$smoke_letter" --chunk 300 \
            --normalize --no-compress --sentence-pause 1 2>&1 | tail -2
    else
        echo "==> 5/5  narrate, voice chain, duck, tape master"
        just render "$smoke_letter" "$tmp/track.wav" 2>&1 \
            | grep -E 'lifelog|^  [0-9]/2' | sed 's/^/    /'
    fi
    echo
    echo "All stages passed. Nothing was billed and sessions/ was not touched."

# ── synthetic data ────────────────────────────────────────────────────────────────────────────
# Everything above draws private notes. These four recipes build a second vault that draws
# nobody's — same formats, same tools, same pages, and a cast that does not exist — so the
# connectome can be shown to someone. See synthetic/README.md for what reaches which model.

synth_root := justfile_directory() / "synthetic"

# Measure the real vault's habits and forget its contents → synthetic/{profile.json,style-guide.md}
# One Haiku call per hunk, and the class it returns has no string field in it, so a name cannot
# come back. Everything countable is counted in Python instead. Cached whole; --dry-run to price
# it, --no-llm for the arithmetic alone.
synth-profile *args:
    ./tools/synth_profile.py {{args}}

# Assemble four weeks from the HAND-WRITTEN vault in synthetic/vault/ → synthetic/sessions/mm-dd/
# One file per note, holding the text it held before the run and then its full text at the end of
# each week it was touched in. The diffs are not written by hand and must not be: `assemble` runs
# difflib over consecutive states, which is what keeps every `@@` header arithmetic and every
# hunk id a real position in a real file. Edit a note, run this, and the chart redraws.
# The vault is composed against a fixed reference calendar and shifted onto a live one on the way
# out — about two months ahead, by a whole number of weeks so every weekday written into a note
# stays true — and every date is written without its year, folders and filenames included. A
# corpus regenerated at any point lands in the near future and never starts looking stale.
# Moving the dates invalidates the letters, which were read off the old ones: follow with
# `just synth-letters --force`.
# This is what the vault is actually built from now. `just synth-corpus` below is the generator
# it replaced — kept because it is what measured the shape, and because a topic-by-topic corpus
# is still the honest null hypothesis to compare a plotted one against.
synth-vault *args:
    ./tools/synth_vault.py {{args}}

# Write four weeks of a vault that never existed → synthetic/sessions/<date>/
# Reads only the profile, the style guide and a list of subjects — never a real note.
# --story hamlet plans each week as one act, so the chart's connections have a known answer:
# whether the read downstream can work out what it is reading is the experiment. Drop the flag
# for the topic-by-topic corpus, which is the honest null hypothesis.
# Roster, then the week's sleep and spending, then the notes planned week by week, then the
# edits, then difflib for the diffs. Cached whole, so re-running to change the layout is free.
synth-corpus *args:
    ./tools/synth_corpus.py --story hamlet {{args}}

# Opus reads the newest synthetic week, and only that one: the first three are what it is read
# against. prompts/act-analysis.md plus the style guide, over the same bundle a real week assembles → synthetic/sessions/<date>/lifelog-<date>.md
# One prompt and one call, not the two-stage read-then-compact a real week runs: the compaction
# exists because a real letter gets narrated, and this one gets read on a page and handed to
# `just synth-connections`. The read prompt's four openers arrive answered, in the writer's own
# first person, in the session's `## Week context`. --force rewrites letters already there.
synth-letters *args:
    ./tools/synth_letter.py {{args}}

# Plant the arrows that reach back. A generated corpus writes each week to stand on its own, so
# almost nothing crosses a week boundary; this picks the best-matching pairs by shared
# vocabulary, has Haiku write a callback into the current week's note and a sentence into the
# letter's open loops, and re-assembles the vault. --dry-run prints the pairs and their scores.
# Long-range structure → edited notes + an open-loops section in the letter
synth-links *args:
    ./tools/synth_links.py {{args}}

# Re-read the letter against the hunks and redraw, without re-running the topic pass — what you
# want after `just synth-links`, since the units did not change but the letter did.
synth-connections *args:
    #!/usr/bin/env bash
    set -euo pipefail
    export PETROGRAPH_OUT="{{synth_root}}"
    ./tools/hunk_connections.py --hunk-chars 900 --hunk-chars 1400 {{args}}
    ./tools/connection_waffle.py --pick-strength 5=10 --salience hamlet --fill-same-topic --pick-with finances-=2 --pick-with oura-=2
    ./tools/connection_waffle.py --color-by topic --pick-strength 5=10 --salience hamlet --fill-same-topic --pick-with finances-=2 --pick-with oura-=2

# Drawn whole except for the top band, which is cut to ten. The slider opens at 5, so the
# strongest arrows are the only ones a reader sees until they move it — the page's first
# impression. `--pick-with` puts two ledger arrows and two sleep-report arrows among them, so the
# attachments are on the first screen too, and `--pick-strength 5=10 --salience hamlet` fills the
# rest with the ones a reader would know the play
# by, each joining two different notes under two different topics with no pair repeated, and
# leaves 4, 3 and 2 untouched, so pulling the slider down still reveals everything else.
# `--hunk-chars 900 --hunk-chars 1400`: two readings, pooled. A hand-written note's entry for a
# week runs to a thousand characters, and the default 420 cut off the half a letter's sentence
# was quoting, so the model pinned the sentence on whichever hunk it could still see. At 900
# every quoted phrase is in view; at 1400 the model sees more of each note and finds some pairs
# 900 missed while losing others. Neither reading alone got past five clean arrows; pooled,
# seven. Each is cached on its own. Real weeks keep 420.
# `--fill-same-topic`: when too few pass every rule, the rest may join two notes filed
# under one topic. The topic pass put a third of the story under "family", and the bout, the
# bottle and the button all landed on the wrong side of that word.
# Nothing is thrown away: sampling is a display choice made at render time, and all of the
# connections stay in sessions/<week>/connections-<week>.json. Drop the flag to draw them all.
# The connectome, on synthetic data. Same four tools as `just connect` and `just topics-page`,
# pointed at the other vault by the one environment variable they all already read.
# → synthetic/sessions/connections-<date>{,-topics}.html
synth-pages *args:
    #!/usr/bin/env bash
    set -euo pipefail
    export PETROGRAPH_OUT="{{synth_root}}"
    ./tools/hunk_connections.py --hunk-chars 900 --hunk-chars 1400 {{args}}
    ./tools/unit_topics.py
    ./tools/connection_waffle.py --pick-strength 5=10 --salience hamlet --fill-same-topic --pick-with finances-=2 --pick-with oura-=2
    ./tools/connection_waffle.py --color-by topic --pick-strength 5=10 --salience hamlet --fill-same-topic --pick-with finances-=2 --pick-with oura-=2

# END TO END: assemble the hand-written vault, read each week, draw the pages. The whole loop.
# `synth-links` is not in here any more. It existed to plant arrows across weeks because a
# generated corpus wrote each week to stand on its own; a vault with a plot actually in it
# crosses weeks on its own, and planting on top of that is inventing evidence.
synth:
    just synth-vault
    just synth-letters
    just synth-pages

# The two synthetic pages the lifelogging post on jaan.io frames, copied into that repo under the
# names the post uses. From synthetic/ only, and through the same gate `site-build` uses first —
# no real note title — with its output held back, since a refusal names what it found. Framed by
# that site, a page takes its palette; see FRAMED in tools/_page.py.
# The waffle and the topic connectome → ../jaan.io/public/files/lifelogging/
blog-embed dest="../jaan.io/public/files/lifelogging":
    ./tools/site_build.py --check 2>&1 | grep "^checked" || { echo "refused: site_build.py --check did not pass"; exit 1; }
    mkdir -p {{dest}}
    cp synthetic/sessions/microlite-waffle.html {{dest}}/microlite-waffle.html
    cp synthetic/sessions/connections-*-topics.html {{dest}}/connections-topics.html

# ── the one-off host ──────────────────────────────────────────────────────────────────────────
# Three of the four pages cannot open from a file on iOS: DuckDB-WASM needs a Worker and a 30 MB
# module fetched cross-origin, and a file:// page has an opaque origin, so WebKit blocks both.
# Every iOS browser is WebKit. Served over https they work untouched. See site/README.md.

site_project := "petrograph-synthetic"

# Assemble site/public from the SYNTHETIC vault — there is no flag that points this anywhere else.
# Refuses absolutely on a real note title, and there is no flag for that either.
# The synthetic pages → site/public/
site-build *args:
    ./tools/site_build.py {{args}}

# Generate a password and set it as the deployment's secret. Prints it once; it is not stored.
# The gate fails closed, so until this is run every request gets a 503 rather than a page.
site-password:
    #!/usr/bin/env bash
    set -euo pipefail
    pw="$(openssl rand -hex 24)"
    printf '%s' "$pw" | wrangler pages secret put SITE_PASSWORD --project-name {{site_project}}
    echo
    echo "  username: anything    password: $pw"
    echo "  Not stored anywhere. Put it in a password manager now."

# Push it. Creates the project on the first run. Any username, the password above.
site-deploy *args:
    cd {{justfile_directory()}}/site && wrangler pages deploy public --project-name {{site_project}} {{args}}

# It is a one-off; deleting it is the honest end state. The pages regenerate from `just synth`.
site-destroy:
    wrangler pages project delete {{site_project}}

# The REAL connectome, on its own deployment, behind its own password. A separate project, a
# separate directory and a separate tool from `just site-*` — the synthetic builder refuses to
# touch the real vault, and that guard is only worth having if nothing can flip it. Publishes one
# file, served at /, so the deployment has no other URL on it. Read what site_private.py prints.
private_project := "petrograph"

# Say what it would publish → pass --yes to stage it into site/private/
site-private-build *args:
    ./tools/site_private.py {{args}}

site-private-password:
    #!/usr/bin/env bash
    set -euo pipefail
    pw="$(openssl rand -hex 32)"
    printf '%s' "$pw" | wrangler pages secret put SITE_PASSWORD --project-name {{private_project}}
    echo
    echo "  username: anything    password: $pw"
    echo "  Not stored anywhere. Password manager, now — this one is the real vault."

site-private-deploy *args:
    cd {{justfile_directory()}}/site && wrangler pages deploy private --project-name {{private_project}} {{args}}

site-private-destroy:
    wrangler pages project delete {{private_project}}

# Enable the tracked pre-commit guard (.githooks/pre-commit). Refuses to commit anything from the
# real vault, by path AND by content hash — so a `git add -f`, or a rendered page copied somewhere
# tracked under another name, is stopped too. A fresh clone needs this run once.
guard:
    git config core.hooksPath .githooks
    @echo "core.hooksPath = $(git config core.hooksPath)"
