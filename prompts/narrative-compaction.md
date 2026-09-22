# Prompt — narrative compaction (use case 2)

**Purpose.** Compact the session (typically following an `act-analysis.md` read) into a
short literary letter to a recurring expert interlocutor — **Bobby** by default, or
another hypothetical specialist — in a Ben Lerner / David Foster Wallace register, written
to be narrated by text-to-speech. The output is saved to `compactions/YYYY/<slug>-<date>.md`
with front-matter (see `compactions/TEMPLATE.md`), then narrated. `just review-and-narrate`
does all three — it sends this prompt into the session that produced the ACT read, so the
letter is written with the week's diff and the analysis still in context.

**Variable.** `INTERLOCUTOR` — default `Bobby`.

---

## Prompt

Now perform narrative compaction for **{{INTERLOCUTOR}}** in a Ben Lerner / David Foster
Wallace tone, following these principles:

- Distinguish real grammatical rules from folklore.
- Use subjects to name the characters in your story.
- Use verbs to name their important actions.
- Open your sentences with familiar units of information.
- Get to the main verb quickly:
  - Avoid long introductory phrases and clauses.
  - Avoid long abstract subjects.
  - Avoid interrupting the subject–verb connection.
  - Push new, complex units of information to the end of the sentence.
  - Begin sentences that form a unit with consistent subjects/topics.
- Be concise:
  - Cut meaningless and repeated words and obvious implications.
  - Put the meaning of phrases into one or two words.
  - Prefer affirmative sentences to negative ones.
- Control sprawl:
  - Don't tack more than one subordinate clause onto another.
  - Extend a sentence with resumptive, summative, and free modifiers.
  - Extend a sentence with coordinate structures after verbs.
- Above all, write to others as you would have others write to you.

Update {{INTERLOCUTOR}} on the **logistics/ops, the planning, the register, the vibe, and
the result**, using standard best practices in both narrative relation and screenwriting,
ready to be narrated using state-of-the-art text-to-speech.

---

*Notes for TTS:* keep it speakable — the narrator reads the body only; front-matter is
stripped by both TTS tools. Avoid tables, code, and dense parentheticals that don't read
aloud well. `review-and-narrate` uses ElevenLabs, with the voice from the input file's
`voice:` field; `just tts <file> <voice>` narrates an existing letter through Cartesia instead.
