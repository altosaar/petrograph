---
title: ACT re-entry read — <short label> # names both archived files
slug: compaction # metadata for the archived files; the session is sessions/<date>/
date: 2026-01-01 # filled in by `just weekly` / `just apply-review-template`
interlocutor: Bobby # the expert the compaction letter is between — see the prompts
since: 7 # days of Obsidian history to diff
diff: # pre-built Obsidian diff; blank = build it now. Filled in by `just weekly`.
attachments: # comma-separated paths; filled in by `just weekly`. See the note below.
calendar: # optional path to a calendar export; blank = skip
music: # optional backing track for the narration
music-engine: # mixer voice chain: oss (default, nothing to install) or vst (licensed plug-ins)
engine: # who narrates: eleven (default — the letter is posted to ElevenLabs) or chatterbox
voice: WeAAwKYcS06VmXw086yZ # ElevenLabs voice id; ignored under engine: chatterbox
voice-sample: # engine: chatterbox only — a 10-20s clip to clone. Blank = share/voices/victoria.wav
llm: claude # which CLI writes the read and the letter: claude, or opencode (= `--llm`)
model: fable # model for both calls; a Claude alias, or `provider/model` under opencode
effort: max # reasoning effort; blank = whatever the CLI would pick
---

<!--
`llm: opencode` sends the read and the letter through `opencode run` instead of `claude`,
written by whichever model you are signed in to there. Same prompts, same two turns, same
archived files — and `model:` then wants an `opencode models` pair like
`anthropic/claude-opus-4-5`, or nothing at all to use opencode's own default. `effort:`
becomes `--variant`. Who narrates is a separate choice, and `engine:` makes it.
-->

This upcoming week I'm

Tomorrow I hope to

Today I hope to

Emotionally,

<The freeform week context. Everything below the front-matter is passed to Claude verbatim,
under a "Week context" heading, alongside the diff, the calendar, and every attachment — so
write it the way you would say it: mood, sleep, what happened, what is coming, the activity
ceiling, anything the notes do not show.

Where an attachment already carries the numbers, say less about them here than you would
otherwise. The bundle can read a ledger; what it cannot read is what the week felt like from
inside, and that is the part only you can write.>

<!--
`attachments:` is a comma-separated list of file paths. Each one is read and inlined into the
bundle under its own heading, verbatim. Nothing about the review flow knows what any of them
contain — a ledger, a sleep report and a browsing summary are all just markdown to it — so
this list is the whole of the extension mechanism. Add a file, and Claude sees it.

Relative paths resolve against this session's own directory, which is where `just weekly`
puts everything by default — so a session is one folder you can paste from.

`just weekly` writes this line for you from providers.conf. What follows is what it tends to
produce, and what to write by hand if you are assembling a week yourself.

  attachments: /Users/you/projects/your-ledger/context/finances-2026-05-15.md,
               /Users/you/projects/obsidian-oura-metrics/context/oura-2026-05-15.md,
               browsing-firefox-2026-05-15-7d.md

(One line in the real file — the wrapping above is only for reading.)

── the week's spending, as Beancount ────────────────────────────────────────────────────
Written by your ledger repo's own weekly script, from whatever it syncs from. Postings
rather than a summary, because the categories are the interesting part and a total is not:

    2026-05-12 * "Corner Market" ""
      Expenses:Food:Groceries      40.00 USD
      Liabilities:CC:Card

    2026-05-13 * "Transit Authority" ""
      Expenses:Transport:Transit    3.00 USD
      Liabilities:CC:Card

Stubs, not the ledger itself: payee, amount, account, and the category the sync assigned.
An uncategorised posting shows up as Expenses:FIXME, which is itself worth reading — it is
usually the week's unusual purchase.

── the week's sleep and physiology, from the Oura ring ──────────────────────────────────
Written by obsidian-oura-metrics' CLI. A row per night plus the week's shape:

    | Night   | Sleep | Efficiency | HRV | Resting HR | Readiness |
    | ------- | ----- | ---------- | --- | ---------- | --------- |
    | Night 1 | 6h30m | 90%        | 40  | 60         | 75        |
    | Night 2 | 8h00m | 95%        | 50  | 55         | 85        |

Bedtime and wake time matter as much as the totals here — a week that drifted later is a
different week from a short one, and the read is meant to notice the difference.

── where the week went online ───────────────────────────────────────────────────────────
Written by this repo's `browsing_history.py`, read-only and local. One row per site per
hour — a shape, not a log:

    | Hour  | Site            | Visits | On page | Pages                          |
    | ----- | --------------- | -----: | ------: | ------------------------------ |
    | 09:00 | mail.google.com |     14 |     22m | Inbox (31) ×9; Compose         |
    | 10:00 | github.com      |      8 |     16m | altosaar/petrograph; Pull req… |

Chrome, Firefox and Safari are all supported and the filename says which, so a week can
carry more than one — they are different windows onto the same days, not competing takes:

    browsing-chrome-2026-05-15-7d.md
    browsing-firefox-2026-05-15-7d.md
    browsing-safari-2026-05-15-7d.md

Only Chrome records how long a page was open. Firefox and Safari store no duration at all,
so their files drop the `On page` column rather than print a dash that would read as "under
a minute" when it means "unknowable". Expect the Chrome file to look richer for that reason
alone — it is not a busier week, it is a chattier database.

── anything else ────────────────────────────────────────────────────────────────────────
There is nothing special about the three above. A calendar export, a training log, a
paragraph you wrote in a different file — if it is markdown and it is a path, it goes in.

── who reads the letter aloud ───────────────────────────────────────────────────────────
`engine:` picks the narrator, and it is the one line in this file that decides whether the
finished letter — the most private document this repo produces — is posted to a third party.

  engine: eleven      the default, and what every session so far was rendered with. The
                      letter is sent to ElevenLabs, who synthesise it. Better long-form
                      prosody, costs credits, and the text is on their servers.
  engine: chatterbox  synthesised on this machine by tools/chatterbox_tts.py. Nothing is
                      sent anywhere; the cost is wall-clock rather than credits.

Leaving it blank falls through to $PETROGRAPH_TTS_ENGINE (environment or .env) and then to
eleven, so a machine can be switched over once in .env rather than per session. A single run
goes the other way with `just review-and-narrate <session> --tts-engine chatterbox`.

Under chatterbox, `voice:` does not apply — there is no catalogue to look an id up in. The
voice is a reference clip instead: `voice-sample:`, or share/voices/victoria.wav if you have
captured one with `just voice-sample`, or the model's own built-in speaker if you have not.

Stages 2 and 3 — the ACT read and the letter — still go to Anthropic either way. `engine:`
is about the narration, which is the stage that reads the finished letter aloud.

── the week's Obsidian diff, which is not an attachment ─────────────────────────────────
`diff:` is its own key because the vault history is the spine of the read rather than
something beside it: it gets its own section at the end of the bundle, and re-running only
the letter (`--resume`) reads it back out of the session.

Leave it blank and `weekly_review.py` builds it when the review runs. `just weekly` fills it
in from the `[microlite]` provider, which is the same `microlite_hunks.py` call made early, so
you can read the week before deciding to spend anything on it.
-->
