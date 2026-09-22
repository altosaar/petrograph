#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
weekly_review — the week's diff, an ACT read, a compaction letter, and its narration, in one run.

This is the whole of README's "two use cases" with the copy-pasting removed. You write one
thing — the freeform week-context paragraph — and everything downstream runs unattended:

  1. the week's Obsidian hunks           microlite_hunks.py --days N (the canonical reader)
  2. the ACT re-entry read               prompts/act-analysis.md + diff + attachments + context
  3. the narrative compaction letter     prompts/narrative-compaction.md, same session
  4. the narration                       eleven_tts.py or chatterbox_tts.py, see below
                                         → lifelog-<date>-voice.wav, and the master beside
                                         it when `music:` is set

Step 3 resumes the session that produced step 2 rather than starting a fresh one, because
that is what the manual claude.ai flow did: the letter is written with the diff and the read
still in context.

Steps 2 and 3 go to `claude` unless told otherwise. `--llm opencode` sends them to `opencode`
instead — the same two prompts, the same resumed session, the same archived files, written by
whichever model you are signed in to there. Nothing else in the run changes — who narrates is
step 4's own choice, below. Those two turns take minutes each, so opencode's events are read as
they arrive and reported on a line that keeps a clock: a long wait looks like a wait rather
than like a dead connection.

Step 4 is the one stage with a choice of where the work happens, and it is worth making
deliberately: it is handed the finished letter in full. `--tts-engine chatterbox`, or an
`engine: chatterbox` line in the session, or PETROGRAPH_TTS_ENGINE in .env, swaps
eleven_tts.py for chatterbox_tts.py — which synthesises on this machine and sends the letter
nowhere. Unset everywhere it stays eleven, which is what every session here was rendered
with. _speech.resolve_engine owns that precedence; nothing here re-derives it. The two choices
are independent: `--llm` picks who writes steps 2 and 3, `--tts-engine` picks who reads the
finished letter aloud.

That same answer may also be a *path*, and then the script it names is handed the letter on
the command line the two engines here answer to. This is the seam a narrator that is neither
of them fits through — one that synthesises on a GPU box you rent by the hour, say — and it
is deliberately the only shape that seam has: nothing in this file learns a third way to
narrate, and nothing here learns what ssh is. Stage 4 says out loud which script it handed
the letter to, because with an engine from outside this repo that sentence is the only thing
said about where the letter went.

Nothing here reimplements a tool that already exists — the diff, the narration, and the model
calls are all subprocesses, so `just review`, `just eleven` and this share one implementation.

A session is a directory, and every stage of one week lives in it, numbered in the order it
happens. `ls` prints the pipeline, and a session can be read, moved, archived or deleted as
the single thing it always was:

    sessions/<date>/
        edit-me.md          the one file you write     (front-matter + week context)
        microlite.md        the week's Obsidian history
        finances-<date>.md  ·  whatever the providers wrote, written in beside the rest so
        oura-<date>.md      ·  one folder holds everything a paste into Claude needs
        claude-analysis.md  the ACT read               (unattended runs only)
        lifelog-<date>.md   the letter — a stub until you paste one in, or the run writes it
        bundle.md           --dry-run only: the assembled prompt, to read or paste by hand
        lifelog-<date>.mp3  the master, named for the session rather than the file
        lifelog-<date>-voice.wav   ·  the narration alone, and the coloured voice
        lifelog-<date>-stem.wav    ·  on its own — 24-bit, for re-mixing

The session directory is discovered from the input file rather than recomputed from slug and
date, so one that has been renamed, moved or restored from a backup still knows where its own
stages belong. Naming the directory works everywhere the input file does.

Front-matter beyond the obvious: `attachments` is a comma-separated list of markdown files
folded into the bundle under their own H1 as a heading — the week's spending, an Oura summary,
anything a sibling tool generates. `calendar` predates it and still works, keeping its tilde
fence because a calendar export is arbitrary text rather than markdown. `llm` is `claude` (the
default) or `opencode`, which is what `--llm` sets — recorded into both archived documents, so
a session says which CLI wrote it and a `--resume` follows the run it is resuming. `model` is
read by whichever of the two that is: a Claude alias like `opus`, or the `provider/model` pair
`opencode models` prints, and blank under opencode leaves the choice to its own config.
`effort` is passed through to `claude --effort` and to `opencode --variant`, which are the same
idea and equally provider-specific; unset leaves the CLI's own choice.

Usage:
    ./weekly_review.py --new                              # scaffold this week's session
    ./weekly_review.py --new --llm opencode               # ...with `llm:` already decided
    ./weekly_review.py --new --tts-engine chatterbox      # ...and `engine:` too
    ./weekly_review.py --new --music ~/beds/rain.flac     # ...and a bed under the narration
    ./weekly_review.py --new --blank-context              # ...and nothing left to fill in
    # ...though `just weekly` scaffolds it *and* fills in `attachments:` from providers.conf
    ./weekly_review.py sessions/2026-05-15 --dry-run          # bundle, no API calls
    ./weekly_review.py sessions/2026-05-15                    # the whole run
    ./weekly_review.py sessions/2026-05-15 --skip-narration
    ./weekly_review.py sessions/2026-05-15 --tts-engine chatterbox   # narrate locally
    ./weekly_review.py sessions/2026-05-15 --tts-engine ~/git/your-tts-repo/remote_chatterbox.py
    ./weekly_review.py sessions/2026-05-15 --resume           # letter + audio only
    ./weekly_review.py sessions/2026-05-15 --llm opencode     # ...written by opencode
    # the read asked with a prompt of your choosing rather than prompts/act-analysis.md
    ./weekly_review.py sessions/2026-05-15 --prompt prompts/some-other-read.md
"""

from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# This stage reports what the run produced, so it has to name the files the same way the tools
# that wrote them did — reconstructing the rule here is what made it report a master as missing.
from _names import master_name, voice_name
from _term import Waiting, link, say
from _env import REPO_ROOT
from _llm import DEFAULT_LLM, LLMS, default_model, model_fits, resolve_llm
from _speech import (engine_label, engine_tool, engine_where, music_field,
                     music_tracks, resolve_engine)
from _session import ANALYSIS, BUNDLE, DIFF, INPUT, compaction, ensure_stub

TOOLS = REPO_ROOT / "tools"
ACT_PROMPT = REPO_ROOT / "prompts" / "act-analysis.md"
COMPACTION_PROMPT = REPO_ROOT / "prompts" / "narrative-compaction.md"
SESSION_TEMPLATE = REPO_ROOT / "sessions" / "TEMPLATE.md"

# The register the read is written in, and the prose principles under it. It used to be a section
# at the bottom of act-analysis.md and of a second copy beside it, which is two files to keep in
# step and one of them easy to forget. Now it is one file, appended to whatever ask a run is made
# with — so `--prompt` swaps the question without also silently dropping the voice.
STYLE_PROMPT = REPO_ROOT / "prompts" / "synthetic-style-guide.md"


def _prompt_override(var: str, default: Path) -> Path:
    """A prompt file an experiment can swap without editing this file.

    The read prompt already has `--prompt`; the style block and the letter do not, and both are
    things a sweep wants to vary. Unset, every one of these is the repo's own file, so a run
    that sets nothing is the run this file always made.
    """
    value = os.environ.get(var, "").strip()
    return Path(value).expanduser() if value else default


# What a local model needs that a frontier one infers. Both prompts above are written for Claude,
# and are four or five sentences that leave the shape of a good read unstated, so under
# --llm opencode the read gets two reference sections appended after the diff.
METAPHORS = REPO_ROOT / "prompts" / "metaphors.md"
EXAMPLE_READ = TOOLS / "example.md"



# The reply is archived verbatim as the document, so the model is asked for prose rather than
# for the coding-agent register that Claude Code defaults to. Replacing the system prompt (and
# denying every tool) gets the run close to what the manual claude.ai session produced.
SYSTEM_PROMPT = (
    "You are writing for a person, not operating a codebase. Answer the prompt directly and "
    "completely, in markdown. Do not use tools. Do not open with a preamble about what you are "
    "about to do, and do not close with an offer of further help — your reply is saved verbatim "
    "as the document itself."
)
DENY_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit TodoWrite"


def system_prompt() -> str:
    """The register, or whatever `PETROGRAPH_SYSTEM_PROMPT` names instead.

    A path is read as a file; anything else is taken as the prompt itself, because a one-line
    register is a thing you would rather type than keep in a file.
    """
    value = os.environ.get("PETROGRAPH_SYSTEM_PROMPT", "").strip()
    if not value:
        return SYSTEM_PROMPT
    path = Path(value).expanduser()
    return path.read_text().strip() if path.is_file() else value


# ── inputs ────────────────────────────────────────────────────────────────────────────────

# Flat scalars only. The front-matter in these files is a form, not a data structure, and a
# 20-line reader keeps the tool at `dependencies = []` like every other tool in this repo.
COMMENT_RE = re.compile(r"\s+#(?:\s.*)?$")


def parse_front_matter(raw: str, source: Path) -> tuple[dict[str, str], str]:
    """Split a markdown file into its YAML front-matter (flat keys) and its body."""
    if not raw.startswith("---\n"):
        sys.exit(f"{source}: expected YAML front-matter, starting with a --- line.")
    end = raw.find("\n---", 4)
    if end == -1:
        sys.exit(f"{source}: front-matter is never closed by a --- line.")
    # A file that ends on its closing --- with no trailing newline has no newline to find, and
    # find() answers -1 — so slicing from there would hand back the whole file as the body.
    nl = raw.find("\n", end + 1)
    head, body = raw[4:end], (raw[nl + 1 :] if nl != -1 else "")

    fields: dict[str, str] = {}
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t", "-")):
            continue  # blanks, comments, and the nested blocks this reader does not need
        key, _, value = line.partition(":")
        if _:
            fields[key.strip()] = COMMENT_RE.sub("", value).strip()
    return fields, body.strip()


COMMENT_BLOCK_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_comments(text: str) -> str:
    """Drop HTML comments from the week context before it becomes a prompt.

    The session template carries its own documentation inside `<!-- -->` — what each key
    does, what an attachment looks like, an example ledger and an example sleep table.
    That is written for whoever fills the file in, and passing it along would spend tokens
    teaching Claude the format of files it is being handed the contents of.
    """
    return COMMENT_BLOCK_RE.sub("", text).strip()


def unfilled(context: str) -> bool:
    """Whether the week context is still the template's own prose.

    Angle brackets are this repo's mark for "you have not written this yet" — `require`
    reads them the same way in the front-matter. Checking every line rather than only the
    first is the point: the template's placeholder sits at the *end*, below four prompt
    stubs, so `startswith` looked at "This upcoming week I'm" and passed a file nobody had
    touched straight through to a paid call.
    """
    return not context or bool(re.search(r"^\s*<", context, re.MULTILINE))


def prompt_body(path: Path) -> str:
    """The pasteable part of a prompt file: everything under `## Prompt`, up to the next rule.

    A file with no `## Prompt` heading is taken to be all prompt. That shape used to be an
    error, on the reasoning that a prompt file is a documented thing and the undocumented case
    was a mistake -- but a prompt short enough to need no explaining around it is not a mistake,
    and refusing it stopped the run at stage 2 with a sentence about markdown headings. Files
    that do carry the heading are read exactly as before.
    """
    if not path.exists():
        sys.exit(f"Missing prompt: {path}")
    text = path.read_text()
    m = re.search(r"^## Prompt\s*$", text, flags=re.MULTILINE)
    if not m:
        return text.strip()
    rest = text[m.end() :]
    rule = re.search(r"^---\s*$", rest, flags=re.MULTILINE)
    return (rest[: rule.start()] if rule else rest).strip()


def act_prompt(override: Path | None = None) -> Path:
    """Which read prompt this run asks with: `act-analysis.md`, or whatever `--prompt` names.

    `--prompt` is a flag rather than a front-matter key, unlike `llm:` and `model:` and the rest.
    Those are recorded so a session re-runs the way it ran; this one is a question you ask *about*
    a session -- what would this week look like asked the other way -- and answering it should not
    edit the session. What the run chose is archived either way.
    """
    if override:
        if not override.exists():
            sys.exit(f"--prompt: no such file: {override}")
        return override
    return ACT_PROMPT


def prompt_label(path: Path) -> str:
    """How the archive names the prompt a run asked with.

    Repo-relative when it is one of this repo's own, absolute when it came from `--prompt`
    somewhere else -- a bare filename there would read like a prompt that shipped here.
    """
    return str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


def reference_sections(llm: str) -> list[tuple[str, str]]:
    """The read's appended reference material: an exemplar, and the metaphor menu.

    `tools/example.md` is the standard the prompt is aiming at — one real read of one real week,
    shown to the model that cannot infer the shape from four sentences and withheld from the one
    that can. It is gitignored rather than shipped, because a read of a week is a week of a
    person, so this repo has no copy: write or keep your own there. `prompts/metaphors.md` is 26k
    of curated ACT metaphors referenced by no other code — material claude has in its priors and
    a local model has thinly, which is part of why its read comes back generic.

    A missing file is skipped rather than fatal: these sharpen a bundle, and a run that cannot
    find its exemplar should still write the week.
    """
    if llm != "opencode" or os.environ.get("PETROGRAPH_REFERENCES", "").strip() in ("0", "off"):
        return []
    wanted = [("Reference — a read in the right register", EXAMPLE_READ),
              ("Reference — ACT metaphor menu", METAPHORS)]
    # The file's own H1 is dropped rather than demoted: it would land at the same level as the
    # heading given here and title the section twice. Same rule attachment_section already uses,
    # differing only in that the heading is ours rather than the file's.
    return [(heading, re.sub(r"\A#\s+.+?\n+", "", path.read_text()))
            for heading, path in wanted if path.exists()]


def require(fields: dict[str, str], key: str, source: Path) -> str:
    value = fields.get(key, "").strip()
    if not value or value.startswith("<"):
        sys.exit(f"{source}: `{key}` is empty — fill it in before running.")
    return value


# ── the model calls ───────────────────────────────────────────────────────────────────────


def run_turn(prompt: str, *, llm: str, model: str, session: str, resume: bool,
             effort: str = "") -> tuple[str, dict]:
    """One non-interactive turn, against whichever CLI is writing this week.

    Both answer in the same shape — the reply, and a dict carrying `session_id` and enough
    of a usage record for `call_summary` and `resolved_model` — so everything downstream of
    here is written once and comes out the same either way.
    """
    call = run_opencode if llm == "opencode" else run_claude
    return call(prompt, model=model, session=session, resume=resume, effort=effort)


def run_claude(prompt: str, *, model: str, session: str, resume: bool,
               effort: str = "", cwd: str | None = None) -> tuple[str, dict]:
    """One non-interactive turn. `resume` continues the session instead of opening it.

    `cwd` is where the CLI starts, and it is not cosmetic: the CLI loads the memory, CLAUDE.md
    and skills of whatever project it starts in. For a real week that is this repo, and fine.
    For a synthetic one it is a leak — a letter about a fictional man came back quoting this
    vault's own backup situation out of the project's memory — so `synth_letter` starts it in
    an empty directory that belongs to no project.
    """
    # Claude takes the session id rather than minting one, so an opening turn brings its own.
    # It comes back out in the reply either way, which is what the caller reads.
    session = session or str(uuid.uuid4())
    cmd = [
        "claude", "-p",
        "--resume" if resume else "--session-id", session,
        "--model", model,
        "--output-format", "json",
        "--system-prompt", SYSTEM_PROMPT,
        "--disallowed-tools", DENY_TOOLS,
    ]
    # Omitted rather than defaulted: an unset effort leaves whatever the CLI and
    # the model would have chosen, which is the right behaviour for old inputs.
    if effort:
        cmd += ["--effort", effort]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        sys.exit(f"claude exited {proc.returncode}:\n{(proc.stderr or proc.stdout)[-2000:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"claude returned output that is not JSON:\n{proc.stdout[:2000]}")
    if data.get("is_error") or not data.get("result"):
        sys.exit(f"claude reported an error:\n{json.dumps(data, indent=2)[:2000]}")
    data.setdefault("session_id", session)
    return data["result"].strip(), data


# ── opencode ──────────────────────────────────────────────────────────────────────────────

# The agent this run asks for. Named rather than anonymous because opencode wants a key, and
# prefixed because the name lands in a config that a user's own agents share a namespace with.
OPENCODE_AGENT = "petrograph-letter"


def opencode_env() -> dict[str, str]:
    """`--system-prompt` and `--disallowed-tools`, said the way opencode says them.

    opencode has a flag for neither: what a run's system prompt is and which tools it may
    call are properties of an *agent*, and agents come from configuration. Declaring one on
    the environment keeps this a subprocess call like every other in this file — nothing
    installed, nothing written into the repo, and no checked-in config to keep in step with
    the SYSTEM_PROMPT twenty lines above.

    Without it the default agent brings its coding-assistant prompt and its whole toolset:
    thousands of tokens telling the model it is operating a codebase, in front of a prompt
    asking it to write a letter to a person. `"*": False` denies the tools by wildcard rather
    than by name, so a tool opencode adds later is denied too.
    """
    agent = {
        "mode": "primary",
        "description": "The weekly read and the letter: prose for a person, and no tools.",
        "prompt": system_prompt(),
        "tools": {"*": False},
        # Prose, not code. Left unset the run inherits whatever the served model ships, which is
        # not always what you would pick for prose — qwen3.8:27b ships a top_k of 20, and twenty
        # candidates a token is most of why its letters come back flat. That one is a Modelfile
        # parameter and not in the OpenAI-compatible surface, so it is set where the box is built;
        # `top_k` and `min_p` cannot be set from here at all. These two can, which makes them the
        # pair to sweep — even where, as with that model, they start out already at these values.
        "temperature": float(os.environ.get("PETROGRAPH_TEMPERATURE") or 1.0),
        "top_p": float(os.environ.get("PETROGRAPH_TOP_P") or 0.95),
    }
    config = json.dumps({"agent": {OPENCODE_AGENT: agent}})
    return {**os.environ, "OPENCODE_CONFIG_CONTENT": config}


def run_opencode(prompt: str, *, model: str, session: str, resume: bool,
                 effort: str = "") -> tuple[str, dict]:
    """One non-interactive turn against `opencode run`, in the shape run_claude answers in.

    Resuming is the same idea with the mechanics the other way round: opencode mints the
    session id rather than accepting one, so an opening turn reports it and the letter hands
    it back with `--session`. A session belongs to the directory the CLI ran in, which is why
    that is the repo rather than wherever the command was typed — otherwise `--resume` from
    a different shell would find nothing to resume.

    The events are read as they arrive rather than collected at the end, so the wait — which
    is minutes, and was a blank terminal for all of them — can be reported while it happens.
    """
    cmd = ["opencode", "run", "--format", "json", "--agent", OPENCODE_AGENT]
    # In `--format json` the flag decides only whether reasoning parts are emitted as events;
    # it asks nothing different of the model. They are the one thing a long turn says before
    # its text arrives, so without it the stream is silent for the whole call.
    cmd += ["--thinking"]
    if resume:
        cmd += ["--session", session]
    # Blank is a real answer here, unlike Claude's `--model`: it leaves the choice to
    # whatever opencode's own config picked, and the session is asked afterwards what that was.
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--variant", effort]  # opencode's word for reasoning effort
    t = time.perf_counter()

    # A stream of JSON events, one per line, with progress of its own printed around them.
    # Text arrives as parts keyed by id, and a part can be reported more than once as it
    # fills — so they are collected into a dict, where a later report of a part replaces the
    # earlier one rather than being appended to it.
    parts: dict[str, str] = {}
    reported, cost, tokens, failure, steps = session, 0.0, {}, "", 0
    # Whatever opencode says on stdout that isn't an event. Kept because a run that ends with
    # no text at all used to be diagnosed from stdout, and stdout is now read rather than held.
    chatter: list[str] = []

    # Both the prompt and stderr go through temp files rather than pipes. Reading the events
    # as they arrive means the loop below is the only thing draining a pipe, and a second pipe
    # nobody is draining — a bundle too big for the buffer going in, a chatty stderr coming
    # out — is a deadlock rather than a slow run.
    with tempfile.TemporaryFile("w+") as stdin_file, tempfile.TemporaryFile("w+") as err_file:
        stdin_file.write(prompt)
        stdin_file.seek(0)
        proc = subprocess.Popen(cmd, stdin=stdin_file, stdout=subprocess.PIPE, stderr=err_file,
                                text=True, cwd=REPO_ROOT, env=opencode_env())
        with Waiting("opencode") as wait:
            for line in proc.stdout:
                if not line.startswith("{"):
                    if line.strip():
                        chatter.append(line.rstrip())
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                reported = event.get("sessionID") or reported
                kind, part = event.get("type"), event.get("part") or {}
                if kind == "text":
                    parts[part.get("id") or str(len(parts))] = part.get("text") or ""
                    wait.say(f"writing — {sum(len(p) for p in parts.values()):,} chars")
                elif kind == "reasoning":
                    # Whole parts, at the moment each finishes, so the last line of one is the
                    # freshest thing the model has thought — and the clearest sign it is alive.
                    thought = (part.get("text") or "").strip().splitlines()
                    wait.say(f"thinking: {thought[-1]}" if thought else "thinking")
                elif kind == "step_start":
                    steps += 1
                    wait.say(f"step {steps}" if steps > 1 else "prompt sent")
                elif kind == "step_finish":
                    cost += part.get("cost") or 0.0
                    tokens = part.get("tokens") or tokens
                    out = (tokens or {}).get("output")
                    wait.say(f"step {steps} done" + (f" — {out:,} output tokens" if out else ""))
                elif kind == "tool_use":
                    wait.say(f"tool: {part.get('tool') or 'unknown'}")
                elif kind == "error":
                    failure = json.dumps(event.get("error"), indent=2)
            proc.wait()
        err_file.seek(0)
        stderr = err_file.read()

    if proc.returncode != 0 or failure:
        # The error is an event on stdout when the model call failed and a plain line on
        # stderr when opencode itself did — a bad session id, a model it cannot reach.
        detail = failure or stderr[-2000:] or "(no output)"
        sys.exit(f"opencode exited {proc.returncode}:\n{detail}")
    body = "".join(parts.values()).strip()
    if not body:
        detail = "\n".join(chatter[-20:] + [stderr]).strip()
        sys.exit(f"opencode returned no text:\n{detail[-2000:] or '(no output)'}")
    # Named for the keys call_summary already reads, so the two report lines are one line
    # of code and read the same whichever CLI wrote the document.
    return body, {"session_id": reported, "total_cost_usd": cost,
                  "duration_ms": (time.perf_counter() - t) * 1000, "usage": tokens}


def opencode_model(session: str) -> str:
    """The model an opencode session actually ran on.

    `model:` is optional under opencode, and left blank nothing in the run's output says
    what the CLI chose in its place — an archived document that cannot say what wrote it is
    the one outcome worth a second subprocess to avoid. `export` prints a line of its own
    before the JSON, hence the seek to the first brace.

    A blank on failure rather than a placeholder, because this answer is written into the
    archive's `model:` and read back out of it by `--resume` — and a blank there means what
    a blank in the input meant, "opencode chooses", where `unknown` would be passed on as a
    model id and rejected.
    """
    proc = subprocess.run(["opencode", "export", session], capture_output=True, text=True,
                          cwd=REPO_ROOT)
    start = proc.stdout.find("{")
    if proc.returncode != 0 or start == -1:
        return ""
    try:
        model = json.loads(proc.stdout[start:])["info"]["model"]
        return f"{model['providerID']}/{model['id']}"
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""


def call_summary(data: dict) -> str:
    cost = data.get("total_cost_usd")
    secs = (data.get("duration_ms") or 0) / 1000
    return f"{secs:.0f}s" + (f" · ${cost:.2f}" if isinstance(cost, (int, float)) else "")


def resolved_model(llm: str, data: dict, requested: str) -> str:
    """Which model wrote this, in the words the archive should record.

    Claude reports its own usage and the canonical id is in there — `opus` resolved to
    something with a date on it. opencode reports nothing of the kind and takes `--model`
    literally, so what was asked for is what ran; when nothing was asked for, only the
    session knows, and that costs a second call to find out.
    """
    if llm == "opencode":
        return requested or opencode_model(data.get("session_id") or "")
    return model_used(data, requested)


def model_used(data: dict, requested: str) -> str:
    """The full model id the run resolved to, so the archive records more than `opus`.

    `modelUsage` is keyed by model and also lists the small models Claude Code runs for its own
    bookkeeping — Haiku is in there on every call, usually first. The one that wrote the
    document is the one that cost the most, by a couple of orders of magnitude."""
    usage = data.get("modelUsage")
    if isinstance(usage, dict) and usage:
        model = max(usage, key=lambda m: (usage[m] or {}).get("costUSD", 0))
        return (usage[model] or {}).get("canonicalModel") or model
    return requested


# ── the archived files ────────────────────────────────────────────────────────────────────


def analysis_doc(meta: dict, body: str) -> str:
    return f"""---
title: {meta['title']}
date: {meta['date']}
slug: {meta['stem']}
stage: analysis
llm: {meta['llm']}
model: {meta['model']}
session: {meta['session']}
sources:
  - obsidian: microlite_hunks.py --days {meta['since']}
  - attachments: {meta['attachments'] or 'none'}
prompts:
  analysis: {meta['act_prompt']}
  style: prompts/synthetic-style-guide.md
related:
  compaction: {meta['compaction']}
tags: [analysis, act, re-entry]
---

{body}
"""


def compaction_doc(meta: dict, body: str) -> str:
    return f"""---
title: {meta['interlocutor']} Compaction — {meta['title']}
interlocutor: {meta['interlocutor']}
date: {meta['date']}
slug: {meta['stem']}
stage: compaction
llm: {meta['llm']}
model: {meta['model']}
session: {meta['session']}
voice: {meta['voice']}
engine: {meta['engine']}
audio: {meta['audio']}
sources:
  - obsidian: microlite_hunks.py --days {meta['since']}
  - attachments: {meta['attachments'] or 'none'}
prompts:
  analysis: {meta['act_prompt']}
  style: prompts/synthetic-style-guide.md
  compaction: prompts/narrative-compaction.md
related:
  analysis: {ANALYSIS}
tags: [compaction, re-entry]
---

{body}
"""


# ── modes ─────────────────────────────────────────────────────────────────────────────────


def session_dir(out_root: Path, date: dt.date) -> Path:
    """Where one session lives: one folder per day, named so they sort chronologically."""
    return out_root / "sessions" / f"{date:%Y-%m-%d}"


def set_field(text: str, key: str, value: str, note: str = "") -> str:
    """Write one flat front-matter key, whatever the template said before.

    Appended to the front-matter when the template does not carry the key at all: a flag
    that silently did nothing is worse than a key written in an unexpected order. The
    replacement goes in through a lambda because a value can be a path, and `\\g` in a
    filename is not a backreference.
    """
    line = f"{key}: {value}".rstrip() + (f"  # {note}" if note else "")
    text, wrote = re.subn(rf"^{key}:.*$", lambda _: line, text, count=1, flags=re.MULTILINE)
    if wrote:
        return text
    end = text.find("\n---", 4)
    return text[: end + 1] + line + "\n" + text[end + 1 :]


BLANK_TITLE = "ACT re-entry read — week of {date}"

# What a session with no week context says in place of one. It reaches the model as the
# bundle's `## Week context`, so it is written to be read by one: an empty section would
# leave the read to guess whether the week was quiet or merely unwritten, and those are
# very different weeks. Saying which it is costs a paragraph and prevents an invention.
BLANK_CONTEXT = """\
No week context was written for this session. There is no paragraph here about mood, sleep,
what happened or what is coming — not because the week held none of it, but because nobody
sat down to write one.

So read the diff and the attachments as the whole of what is known, and say only what they
support. Where a week normally tells you how it felt from inside, this one is silent: treat
that silence as missing data rather than as an answer.
"""


def blank_body(text: str) -> str:
    """Replace the template's prompt stubs with a context that needs no filling in.

    The HTML comments are kept — they are the template's own documentation of what each key
    does, and `strip_comments` drops them before the bundle, so they cost nothing to leave
    in the file for whoever opens it. Everything else in the body goes: the four prompt
    stubs are there to be answered, and the placeholder paragraph below them is what
    `unfilled` reads as "not written yet" and refuses to run on.
    """
    end = text.find("\n---", 4)
    nl = text.find("\n", end + 1)
    head, body = text[: nl + 1], text[nl + 1 :]
    kept = COMMENT_BLOCK_RE.findall(body)
    return head + "\n" + "\n\n".join([BLANK_CONTEXT.strip(), *kept]) + "\n"


def scaffold(out_root: Path, slug: str, template: Path, llm: str = "", engine: str = "",
             blank: bool = False, music: str = "", model: str = "",
             effort: str = "") -> None:
    """This week's input file, with as much of it already decided as was asked for.

    `llm`, `engine` and `music` are the choices a session records rather than takes at run
    time — who writes the read and the letter, who reads the letter aloud, and what plays
    under it — so naming them here is naming them once, in the file, where `--resume` and a
    re-run will find them again.
    `blank` goes further and fills in the two things you would otherwise have to type: a
    stand-in title, and a week context that says there is none. That session runs as it
    stands, which is the point of it.

    `model` and `effort` are the same kind of answer one level down: which model of that CLI's,
    and how much reasoning to ask of it. Given, they replace what the template carries; left
    empty, the template's own answer stands, except that a Claude alias under opencode is
    blanked rather than kept — see below.
    """
    today = dt.date.today()
    if not template.exists():
        sys.exit(f"Missing template: {template}")
    session = session_dir(out_root, today)
    out = session / INPUT
    if out.exists():
        sys.exit(f"Already exists, refusing to overwrite: {out}\nEdit it, or delete it first.")
    session.mkdir(parents=True, exist_ok=True)
    ensure_stub(session)
    text = template.read_text()
    text = set_field(text, "date", f"{today:%Y-%m-%d}")
    text = set_field(text, "slug", slug)
    decided: list[str] = []
    if llm:
        text = set_field(text, "llm", llm, "who writes the read and the letter")
        decided.append(f"llm: {llm}")
        # A template carrying a Claude alias and a session saying `llm: opencode` is a
        # session that scaffolds cleanly and then refuses to run, three commands later.
        # Blanking it hands the choice to opencode's own config, which is its default.
        fields, _ = parse_front_matter(text, template)
        if not (model or model_fits(llm, fields.get("model", ""))):
            text = set_field(text, "model", default_model(llm),
                             "opencode's own default; `opencode models` lists the pairs")
            decided.append(f"model: {default_model(llm) or 'left to opencode'}")
    if model:
        # An answer from the caller outranks the template's and the blanking rule above: a
        # wrapper that knows which box it just started knows the model better than either.
        if not model_fits(llm or DEFAULT_LLM, model):
            sys.exit(f"model: {model} is a Claude alias, and opencode wants a "
                     "`provider/model` pair.")
        text = set_field(text, "model", model, "named by the caller")
        decided.append(f"model: {model}")
    if effort:
        text = set_field(text, "effort", effort, "reasoning effort asked of that model")
        decided.append(f"effort: {effort}")
    if engine:
        text = set_field(text, "engine", engine, engine_where(engine))
        decided.append(f"engine: {engine_label(engine)} — {engine_where(engine)}")
    if music:
        # Already absolute and already checked by music_field, so what lands here is a line
        # the narration stage can use as it stands. `music-engine:` stays the template's:
        # which chain colours the voice is a taste, and it holds across whatever is playing.
        text = set_field(text, "music", music, "backing bed, mixed under the narration")
        decided.append(f"music: {music}")
    if blank:
        text = set_field(text, "title", BLANK_TITLE.format(date=f"{today:%Y-%m-%d}"),
                         "stand-in, since --blank-context writes no week to name")
        text = blank_body(text)
        decided.append("title and week context: written for you, and there is nothing left to edit")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    say(f"Scaffolded this session:\n  {link(out)}\n")
    for line in decided:
        say(f"  {line}")
    if decided:
        say("")
    where = session.relative_to(out_root) if session.is_relative_to(out_root) else session
    say("Nothing to fill in — run it:" if blank else
        "Fill in the title and the week-context paragraph, then:")
    say(f"  just review-and-narrate {where}")


def demote_headings(text: str) -> str:
    """Push every heading down one level, leaving fenced blocks alone.

    An attachment's own `## Totals` would otherwise sit at the same level as the
    bundle's `## Week context`, flattening a 100k-character document into one
    undifferentiated list of sections. Fenced content is skipped because a
    beancount or diff block can legitimately begin a line with `#`.
    """
    out, fence = [], None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
        elif re.match(r"#{1,5}\s", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def attachment_section(name: str, text: str) -> tuple[str, str]:
    """An attachment names itself: its own H1 if it has one, else its filename.

    Generators that already title their output (`# Oura metrics — last 7 days`)
    therefore need no configuration here, and the model is told what it is
    reading rather than being handed an anonymous block. The H1 is consumed
    rather than copied so the title does not appear twice, and everything under
    it is demoted so it nests below the bundle's own sections.
    """
    m = re.match(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    if not m:
        return name, demote_headings(text)
    return f"{m.group(1)} — {name}", demote_headings(text[m.end():].lstrip("\n"))


def build_bundle(act: str, context: str, attachments: list[tuple[str, str, bool]],
                 diff: str, since: str, reference: list[tuple[str, str]] | None = None) -> str:
    parts = [act, "---", "## Week context", context]
    for name, text, fence in attachments:
        if fence:
            # A tilde fence cannot be broken by backticks in an exported calendar,
            # which is arbitrary text rather than markdown.
            parts += ["---", f"## Calendar — {name}", f"~~~text\n{text}\n~~~"]
        else:
            # Markdown attachments go in raw, so their tables stay tables.
            heading, body = attachment_section(name, text)
            parts += ["---", f"## {heading}", body]
    # The diff is not fenced: it already contains ```diff blocks of its own, and nesting them
    # inside another fence is how a bundle stops being readable to anyone, model included.
    parts += ["---", f"## Obsidian diff — the last {since} days", diff]
    # Reference material last, and after the week rather than before it: an exemplar and a
    # metaphor menu are tens of thousands of characters, and putting them between the prompt and
    # the diff would push the week itself an essay away from the instruction that asks about it.
    # Empty for claude, which needs neither.
    for heading, text in reference or []:
        parts += ["---", f"## {heading}", demote_headings(text)]
    return "\n\n".join(parts) + "\n"


def report(created: list[tuple[str, Path]], stages: list[tuple[str, float]]) -> None:
    say("\nDone — files created:")
    for label, path in created:
        size = path.stat().st_size
        unit = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"
        say(f"  {label:<12} {unit:>8}  {path.name}\n      {link(path)}")
    if stages:
        say("\ntiming — end to end:")
        for label, secs in stages:
            say(f"  {label:<22} {secs:5.0f}s")
        say(f"  {'──────────────────────':<22} {sum(s for _, s in stages):5.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Week's diff → ACT read → compaction letter → narration, in one run.")
    ap.add_argument("input", nargs="?", type=Path, help="The session directory, or the edit-me.md inside it.")
    ap.add_argument("--new", action="store_true", help="Scaffold this week's input file and exit.")
    ap.add_argument("--slug", default="compaction", help="Stem slug for --new (default: %(default)s).")
    ap.add_argument("--template", type=Path, default=SESSION_TEMPLATE,
                    help="Template for --new (default: sessions/TEMPLATE.md), which carries "
                         "its own notes on what `attachments:` can point at.")
    ap.add_argument("--blank-context", action="store_true",
                    help="With --new: scaffold a session that needs no edits — a stand-in "
                         "title, and a week context saying that none was written. For a week "
                         "you want read off the diff and the attachments alone.")
    ap.add_argument("--music", action="append", metavar="PATH[,PATH…]",
                    help="With --new: the backing track laid under the narration, written "
                         "into the session as `music:`. Repeat, or comma-separate, for "
                         "several — they play in order and the sequence loops under a long "
                         "letter. Checked here, so a mistyped path costs this command rather "
                         "than the two model calls in front of the narration.")
    ap.add_argument("--out-root", type=Path, default=Path(os.environ.get("PETROGRAPH_OUT", REPO_ROOT)),
                    help="Where sessions/ is written — audio included, since it lands in the "
                         "session (default: $PETROGRAPH_OUT, else the repo).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Assemble and save the bundle, price it, and make no API calls.")
    ap.add_argument("--skip-narration", action="store_true", help="Stop after the compaction letter.")
    ap.add_argument("--llm", choices=LLMS,
                    help="Which CLI writes the read and the letter. Overrides the session's "
                         "own `llm:`, which is `claude` unless it says otherwise.")
    ap.add_argument("--model",
                    help="With --new: the `model:` line to stamp into the session. A Claude "
                         "alias for claude, a `provider/model` pair for opencode. For a "
                         "wrapper that has just started the box it is about to point at, and "
                         "so knows the pair better than the template does.")
    ap.add_argument("--effort",
                    help="With --new: the `effort:` line to stamp into the session — the "
                         "reasoning effort asked of that model.")
    ap.add_argument("--tts-engine", metavar="NAME|SCRIPT",
                    help="Who narrates: 'eleven' posts the letter to ElevenLabs, 'chatterbox' "
                         "synthesises it on this machine and sends nothing, and a path is a "
                         "script of your own that is handed the same command line as those. "
                         "Overrides the session's `engine:` line. Unset anywhere, the default "
                         "is eleven (see _speech.resolve_engine for the full precedence). "
                         "Not validated by argparse, because a path cannot be — a bad answer "
                         "is caught before the first model call rather than by the choices "
                         "list.")
    ap.add_argument("--prompt", type=Path, metavar="FILE",
                    help="Ask the read with this prompt file instead of "
                         "prompts/act-analysis.md — for trying a different question against "
                         "a week you have already read, or for asking both backends the same "
                         "one. Read the same way as any prompt here: the `## Prompt` section "
                         "if it has one, the whole file if it does not. The style guide is "
                         "appended either way, so this swaps the question without also "
                         "dropping the voice. The archived documents record whichever was "
                         "used. Note this sets the ask only — the reference "
                         "sections still follow --llm, so an opencode run asked with this "
                         "flag is still an opencode run.")
    ap.add_argument("--resume", action="store_true",
                    help="Redo only the letter and the narration, reusing the diff and the "
                         "ACT read already in the session. For a run that died at the "
                         "narration, so the expensive call is not paid for twice.")
    args = ap.parse_args()

    out_root = args.out_root.expanduser().resolve()

    if args.new:
        # --llm, --tts-engine and --music mean at scaffold time what they mean at run time:
        # they are written into the session as `llm:`, `engine:` and `music:`, so the choice
        # is recorded in the file rather than having to be repeated on every later command.
        try:
            llm = resolve_llm(args.llm) if args.llm else ""
            engine = resolve_engine(args.tts_engine) if args.tts_engine else ""
            if engine:
                engine_tool(engine)  # a mistyped narrator, caught before the week is built
            music = music_field(args.music)
        except ValueError as e:
            sys.exit(str(e))
        scaffold(out_root, args.slug, args.template, llm, engine, args.blank_context,
                 music, args.model or "", args.effort or "")
        return
    if args.blank_context:
        ap.error("--blank-context scaffolds a session, so it only means anything with --new")
    if args.music:
        # At run time the bed is the session's own `music:` line — a flag here would be a
        # second answer to a question the file has already answered, and the narration would
        # have to guess which one the master was named for.
        ap.error("--music writes a session's `music:` line, so it only means anything with "
                 "--new. For a session that exists, edit that line.")
    if not args.input:
        ap.error("a session is required (or use --new to scaffold one)")
    if not args.input.exists():
        sys.exit(f"No such session: {args.input}")
    # Naming the session directory is the same as naming its input file, and is what every
    # message this tool prints suggests — the file inside is an implementation detail.
    if args.input.is_dir():
        if not (args.input / INPUT).exists():
            sys.exit(f"{args.input} is not a session — it has no {INPUT} in it.")
        args.input = args.input / INPUT

    fields, context = parse_front_matter(args.input.read_text(), args.input)
    context = strip_comments(context)
    if unfilled(context):
        sys.exit(f"{args.input}: the week-context body is still the placeholder — write it "
                 "first.\n  (Lines beginning with < are the template's, not yours.)")

    slug = require(fields, "slug", args.input)
    title = require(fields, "title", args.input)
    interlocutor = fields.get("interlocutor") or "Bobby"
    since = fields.get("since") or "7"
    voice = fields.get("voice") or "WeAAwKYcS06VmXw086yZ"
    # The flag beats the session, the session beats $PETROGRAPH_TTS_ENGINE, and nothing at all
    # means eleven — the same answer every session in this repo has already been rendered with.
    try:
        engine = resolve_engine(args.tts_engine, fields.get("engine"))
        # Located here rather than at stage 4: a mistyped engine is a typo, and finding out
        # about it after two model calls have been paid for is finding out too late.
        narrator = engine_tool(engine)
    except ValueError as e:
        sys.exit(f"{args.input}: {e}")
    effort = fields.get("effort") or ""
    date = dt.date.fromisoformat(fields.get("date") or dt.date.today().isoformat())

    # Which CLI writes this week. The flag wins; otherwise the session says, the way it
    # already says which model and which voice — so a session records how it was run and
    # re-runs the same way.
    try:
        llm = resolve_llm(args.llm, fields.get("llm"))
    except ValueError as e:
        sys.exit(f"{args.input}: {e}")

    # `model:` belongs to whichever CLI that is, and _llm.py knows which — the same pair of
    # rules `--new` asks when it stamps `llm:` into a session it is scaffolding.
    model = fields.get("model") or default_model(llm)
    if not model_fits(llm, model):
        sys.exit(f"{args.input}: model: {model} is a Claude alias, and opencode wants a "
                 "`provider/model` pair.\n  `opencode models` lists what you are signed in "
                 "to. Blank it out to use opencode's own default.")

    # The session is the directory the input file sits in, not something recomputed from
    # slug and date — so a session moved, renamed or restored from a backup still knows
    # where its own stages belong, and --out-root moves the whole set by moving sessions/.
    session = args.input.parent
    stem = session.name
    review_path = session / DIFF
    analysis_path = session / ANALYSIS
    compaction_path = session / compaction(session)
    # Beside the letter, not on a surface of its own. Both names come from _names.py, which is
    # what eleven_tts.py and mix_music.py will use when they write these same two files.
    audio_path = voice_name(args.input)
    master_path = master_name(audio_path)
    for p in (review_path, audio_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    def resolve(key: str, raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (args.input.parent / path).resolve()
        if not path.exists():
            sys.exit(f"{args.input}: {key}: {raw} does not exist. Blank it out to skip.")
        return path

    # (name, text, fence). The calendar keeps its own key and its tilde fence
    # because a calendar export is arbitrary text; `attachments` is the general
    # mechanism and takes markdown, which is included raw.
    attachments: list[tuple[str, str, bool]] = []
    if cal := fields.get("calendar"):
        path = resolve("calendar", cal)
        attachments.append((path.name, path.read_text().strip(), True))
    for raw in (a.strip() for a in fields.get("attachments", "").split(",")):
        if raw:
            path = resolve("attachments", raw)
            attachments.append((path.name, path.read_text().strip(), False))

    # Resolved once, here, so the bundle and the archive cannot disagree about what was asked.
    # Resolved to an absolute path before anything labels it: a relative --prompt is relative
    # to wherever you were standing, and an archive that recorded it that way would name a file
    # that only means something from that directory.
    read_prompt = act_prompt(args.prompt.expanduser().resolve() if args.prompt else None)

    meta = {
        "title": title, "date": f"{date:%Y-%m-%d}", "stem": stem, "since": since,
        # The read names the letter it belongs to, and the letter is named for the session.
        "compaction": compaction_path.name,
        # What will exist when the run finishes: the master if a bed was laid under it,
        # otherwise the narration alone.
        "audio": (master_path if fields.get("music") else audio_path).name,
        # Which engine read it is part of what this document is, not a runtime detail: two
        # letters that sound different should not have identical front-matter. Same reasoning
        # for the read's prompt, which is one of two files and used to be named as whichever
        # one the archive was written to expect.
        "interlocutor": interlocutor, "voice": voice, "engine": engine,
        "act_prompt": prompt_label(read_prompt),
        "attachments": ", ".join(name for name, _, _ in attachments),
        "model": model, "session": "", "llm": llm,
    }
    created: list[tuple[str, Path]] = []
    stages: list[tuple[str, float]] = []

    # ── 1/4  the week's diff ──────────────────────────────────────────────────────────────
    if args.resume:
        if not (review_path.exists() and analysis_path.exists()):
            sys.exit(f"--resume needs the earlier run's {DIFF} and {ANALYSIS} in {session}")
        prior, _ = parse_front_matter(analysis_path.read_text(), analysis_path)
        meta["session"] = require(prior, "session", analysis_path)
        meta["model"] = prior.get("model") or model
        # The letter is meant to be written by whatever wrote the read, so an unflagged
        # --resume follows the archived run rather than today's default. Documents written
        # before `llm:` existed have no line to read, and those were all claude.
        if not args.llm:
            llm = meta["llm"] = prior.get("llm") or llm
        say(f"==> resuming {llm} session {meta['session']} from {ANALYSIS}")
    elif raw_diff := fields.get("diff"):
        # Already built, by the microlite provider that `just weekly` ran. Archived under
        # the session all the same: --resume reads it back, and an analysis whose
        # diff has since been regenerated somewhere else is not a record of anything.
        built = resolve("diff", raw_diff)
        t = time.perf_counter()
        if built.resolve() == review_path.resolve():
            # The usual case: the provider wrote straight into the session, so it is
            # already where the archive wants it and copying would be copying onto itself.
            say(f"==> 1/4  diff      {DIFF}, already in the session")
        else:
            say(f"==> 1/4  diff      {built.name} (pre-built) → {DIFF}")
            review_path.write_text(built.read_text())
        stages.append(("1/4 diff", time.perf_counter() - t))
        created.append(("diff", review_path))
    else:
        # No provider built one, so generate it here — same reader, nothing to press.
        say(f"==> 1/4  hunks     last {since} days → {DIFF}")
        t = time.perf_counter()
        subprocess.run(
            [str(TOOLS / "microlite_hunks.py"), "--days", str(since), "--out", str(review_path)],
            check=True,
        )
        stages.append(("1/4 diff", time.perf_counter() - t))
        created.append(("diff", review_path))

    diff = review_path.read_text()

    # ── 2/4  the ACT read ─────────────────────────────────────────────────────────────────
    style = _prompt_override("PETROGRAPH_STYLE_PROMPT", STYLE_PROMPT)
    bundle = build_bundle(f"{prompt_body(read_prompt)}\n\n{prompt_body(style)}",
                          context, attachments, diff, since, reference_sections(llm))

    if args.dry_run:
        bundle_path = session / BUNDLE
        bundle_path.write_text(bundle)
        tokens = len(bundle) // 4
        say(f"\nbundle: {len(bundle):,} chars ≈ {tokens:,} tokens on {model or llm}")
        # Which prompt asked it, because with --prompt and two defaults that is now a question
        # a bundle can answer for you rather than one you check by reading the first line.
        say(f"  prompt {prompt_label(read_prompt)}")
        say(f"  diff {len(diff):,} · context {len(context):,}" + "".join(
            f" · {name} {len(text):,}" for name, text, _ in attachments))
        created.append(("bundle", bundle_path))
        report(created, stages)
        say("\nDry run — no API calls made, nothing archived.")
        return

    # Both CLIs are named for their binary, so the one thing a missing backend needs saying
    # about it is said here rather than as a stack trace a stage and a bundle later.
    if not shutil.which(llm):
        sys.exit(f"`{llm}` is not on PATH — install it, or run with --llm "
                 f"{'claude' if llm == 'opencode' else 'opencode'}.")

    if not args.resume:
        say(f"\n==> 2/4  act read  {len(bundle):,} chars → {ANALYSIS} ({llm})")
        t = time.perf_counter()
        body, data = run_turn(bundle, llm=llm, model=model, session=meta["session"],
                              resume=False, effort=effort)
        # opencode mints the session id it reports back; claude was handed one. Either way
        # the archive records the session the read ran in, which is what --resume picks up.
        meta["session"] = data.get("session_id") or meta["session"]
        meta["model"] = resolved_model(llm, data, model)
        analysis_path.write_text(analysis_doc(meta, body))
        stages.append(("2/4 act read", time.perf_counter() - t))
        created.append(("analysis", analysis_path))
        say(f"    {len(body):,} chars · {call_summary(data)}")

    # ── 3/4  the compaction letter, in the same session ───────────────────────────────────
    say(f"\n==> 3/4  letter    for {interlocutor} → {compaction_path.name}")
    t = time.perf_counter()
    ask = prompt_body(_prompt_override("PETROGRAPH_COMPACTION_PROMPT", COMPACTION_PROMPT)) \
        .replace("{{INTERLOCUTOR}}", interlocutor)
    body, data = run_turn(ask, llm=llm, model=meta["model"], session=meta["session"],
                          resume=True, effort=effort)
    compaction_path.write_text(compaction_doc(meta, body))
    stages.append(("3/4 letter", time.perf_counter() - t))
    created.append(("compaction", compaction_path))
    say(f"    {len(body):,} chars · {call_summary(data)}")

    # ── 4/4  the narration ────────────────────────────────────────────────────────────────
    if args.skip_narration:
        say("\n==> 4/4  narration skipped (--skip-narration)")
        # `just eleven` and `just chatterbox` are recipes; an outside script is not, so it is
        # named as the command it actually is.
        rerun = f"just {engine}" if engine in ("eleven", "chatterbox") else str(narrator)
        say(f"    {rerun} {compaction_path} when you want it")
    else:
        say(f"\n==> 4/4  narrate   {compaction_path.name} → {audio_path.name}"
            f"  ({engine_label(engine)}, {engine_where(engine)})")
        t = time.perf_counter()
        # Both tools take the same file, write the same name and hand off to the same mixer;
        # only what happens in the middle — and where the letter goes — differs. Everything
        # engine-specific is a flag, so this stays one code path rather than two.
        cmd = [str(narrator), str(compaction_path),
               "--normalize", "--out", str(audio_path)]
        if engine == "eleven":
            # `voice:` is an ElevenLabs voice id, which means nothing to a local cloner with
            # no catalogue to look one up in. Same for the pause dashes: over there a beat is
            # characters inside the text, here it is silence spliced between chunks, and the
            # local tool's millisecond defaults are already scaled to match these.
            cmd += ["--voice", voice, "--chunk", "300", "--sentence-pause", "1"]
        elif sample := fields.get("voice-sample"):
            # Anything that is not ElevenLabs clones from a clip rather than picking an id.
            # The clip whose voice to clone, resolved like every other path in front-matter.
            cmd += ["--voice-sample", str(resolve("voice-sample", sample))]
        # `music:` takes a comma-separated list, like `attachments:` — several tracks play
        # in order, crossfade into each other, and the sequence loops under a long letter.
        # Separated by the same routine the two tts tools use, which resolves the ambiguity
        # against the disk rather than splitting on every comma: a comma is how you say "and
        # then", and also a character in `{Label Co., Ltd.}`, and only the files
        # know which one a given comma is. Relative to the session, like every path here.
        if music := fields.get("music"):
            for track in music_tracks([music], base=args.input.parent):
                cmd += ["--music", str(resolve("music", str(track)))]
            # Which mixer chain colours the voice: oss (default, nothing to install) or vst
            # (the licensed plug-ins). Only meaningful alongside a backing track, and shared
            # by both engines — they hand the narration to the same mix_music.py.
            if mix_engine := fields.get("music-engine"):
                cmd += ["--mix-engine", mix_engine]
        subprocess.run(cmd, check=True)
        # The `just mix` gate reads the sentinel in the repo's share/, wherever the audio
        # landed. It gates on a narration existing, not on which engine produced it.
        (REPO_ROOT / "share").mkdir(parents=True, exist_ok=True)
        (REPO_ROOT / "share" / ".eleven-ran").touch()
        stages.append(("4/4 narrate", time.perf_counter() - t))
        created.append(("audio", audio_path))
        if music:
            # The name meta["audio"] already promised: mix_music.py drops the narration's
            # -voice, it does not append -music. `stem` here is the bare date, not the filename.
            mixed = master_path
            if mixed.exists():
                created.append(("mix", mixed))

    report(created, stages)


if __name__ == "__main__":
    main()
