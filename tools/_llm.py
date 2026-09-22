#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_llm — which CLI writes the read and the letter, and what a model name means to it.

The narration has _speech, which owns the question "who speaks, and where does the letter
go". This is the same question one stage earlier: who *writes* stages 2 and 3. The two are
deliberately independent — `--llm` picks the author, `--tts-engine` picks the narrator — and
each rule lives in one file so a session and the tool that scaffolds it cannot disagree.

That second half is why this exists at all. weekly_review.py owned the list of CLIs and the
precedence between a flag and a session's `llm:` line, and weekly_context.py had no way to
reach it: the only thing tools here import from each other is a `_` module. So a
`just weekly --llm opencode` would have had to hold its own copy of the two names, and find
out a third of the way through a run of paid providers that one of them was mistyped.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations

# The two CLIs that can write the read and the letter. They are also the names of the
# binaries, which is what the on-PATH check in weekly_review.py leans on.
LLMS = ("claude", "opencode")
DEFAULT_LLM = "claude"


def resolve_llm(*candidates: str | None) -> str:
    """Which CLI writes this week, given the answers in order of precedence.

    Callers pass what they know, most specific first — a `--llm` flag, then a session's own
    `llm:` line — and an empty answer falls through to claude, which is what every session
    here was written with. Mirrors _speech.resolve_engine, minus the environment: where the
    letter is *sent* is worth being able to switch once per machine, but who writes it is a
    per-session choice and reads better in the file that records it.
    """
    for choice in (*candidates, DEFAULT_LLM):
        if choice and (name := choice.strip().lower()):
            if name not in LLMS:
                raise ValueError(f"llm: {choice.strip()} — expected one of {', '.join(LLMS)}.")
            return name
    return DEFAULT_LLM


def default_model(llm: str) -> str:
    """The model to use when the session names none.

    Claude wants an alias and has a default worth keeping. opencode wants a `provider/model`
    pair and has a default of its own — whatever you are signed in to — so a blank is an
    answer over there rather than something left unfilled.
    """
    return "" if llm == "opencode" else "opus"


def model_fits(llm: str, model: str) -> bool:
    """Whether this CLI can be handed this model name at all.

    `fable` and `opus` are Claude aliases and mean nothing to opencode, which lists its
    models as `anthropic/claude-opus-4-5`. Asked at scaffold time as well as at run time:
    a session scaffolded with `--llm opencode` on top of a template carrying a Claude alias
    would otherwise write cleanly and then refuse to run.
    """
    return not (llm == "opencode" and model and "/" not in model)
