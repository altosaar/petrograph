// The clean room: everything that writes the synthetic vault.
//
// Nothing in this file has ever seen the real one. What reaches it is a handful of numbers
// (synth_profile.py measured them and threw the text away), the allow list from the gitignored
// topic gate, and a roster of people and places it invented for itself one call earlier. That
// is the entire input. A separate file from synth_profile.baml for exactly that reason — the
// wall between reading and writing should be visible in the directory listing.
//
// Four functions, in the order they run:
//
//   MakeRoster    invent the cast. A journal names the same handful of people for months, so
//                 the roster is made once and every later call chooses from it. Recurrence is
//                 what makes a fake vault feel like a vault rather than like four hundred
//                 unrelated paragraphs.
//   MakeThreads   plan the notes, and what happens in each one week by week. This is where an
//                 inter-week connection comes from: a thread with a beat in week 1 and a beat
//                 in week 4 is a note the writer came back to, which is the arrow the
//                 connectome exists to draw. Asking for it at generation time is the only way
//                 to be sure there is one to find.
//   WriteEdit     one beat becomes one passage of actual note text. The caller says what kind
//                 of edit this is — append, revise, prune — because the mix of those has to
//                 match the profile, and a model asked to choose would append every time.
//   WriteContext  the paragraph the writer types into edit-me.md before the week is read.
//
// The diffs are NOT written by a model. WriteEdit returns the new prose and Python splices it
// into the note and runs difflib over the before and after. A model asked for a unified diff
// produces something that looks like one; difflib produces one, with line counts that add up
// and an `@@` header the parser can trust. That mattered more than it sounds like it would:
// every hunk id on the connectome is a position in that file.

class Roster {
    people: string[] @description("Given names, occasionally with a surname. A mix of origins, the way any city's address book is. No celebrities and no names of real public figures."),
    places: string[] @description("Neighbourhoods, venues, cafes, parks, a couple of cities. Invented, plausible, unglamorous."),
    organizations: string[] @description("Employers, clubs, shops, bands, small companies. Invented — never a real brand."),
    products: string[] @description("Things owned, bought or wanted: a bike model, an app, a tool, a book title, an album."),
    events: string[] @description("Recurring occasions: a weekly club, a class, a standing call, a seasonal trip."),
}

class Beat {
    week: int @description("Which week of the run this beat falls in, counting from 1 as the oldest."),
    summary: string @description("One sentence, in the third person, saying what gets written into this note that week. Concrete: a decision, a figure, a name, a thing that happened. Not 'reflects on progress'."),
}

class Thread {
    note: string @description(
        "The note's filename, ending in .md. Long, lowercase, hyphen-joined, and often several keywords deep because it was named to be found by search rather than to be read: `bike-commute-route-notes.md`, `cooking-sourdough-starter-log.md`, `lang-portuguese-vocab.md`.",
    ),
    topic: string @description("Which allowed topic this note belongs to, copied exactly from the list given."),
    beats: Beat[] @description("What happens in this note, week by week. A note the writer keeps coming back to has a beat in several weeks; most notes have one or two."),
}

class Passage {
    text: string @description("The note text itself. No preamble, no explanation, no markdown code fence around it — just what would be in the file."),
}

client SynthHaiku = anthropic.AnthropicClient.new(
    model = "claude-haiku-4-5-20251001",
    api_key = env.ANTHROPIC_API_KEY,
    max_tokens = 8000,
);

function MakeRoster(counts: string, topics: string, style: string) -> Roster {
    client: SynthHaiku
    prompt: `
        Invent the cast of a fictional person's private notebook: the people, places and things it names over about a month.

        Nobody here is real and nothing here refers to anybody real. Invent every name outright. Avoid names of well-known people, real companies and real brands — the point is a plausible ordinary life, not a recognisable one.

        The notebook is about these subjects, and only these:

        ${topics}

        How many of each to invent:

        ${counts}

        The voice it will all be written in:

        ${style}

        Rules:
        - People are mostly first names, because that is how somebody writes about their own friends. A few carry a surname.
        - Make them varied in origin and unremarkable in register. A notebook's cast is not a novel's cast.
        - Places and organizations should sound like a specific city without ever naming a real one.
        - Every name must be distinct enough that a later writer can tell two of them apart at a glance.

        ${ctx.output_format}
    `
}

function MakeThreads(topics: string, roster: string, weeks: string, style: string, wanted: string) -> Thread[] {
    client: SynthHaiku
    prompt: `
        Plan a month of a fictional person's private notes: which notes exist, what each one is about, and what gets written into each one week by week.

        The weeks, oldest first:

        ${weeks}

        The subjects the notebook covers, and nothing outside this list:

        ${topics}

        The cast it draws on:

        ${roster}

        The voice:

        ${style}

        How much to plan:

        ${wanted}

        Rules:
        - Cover every subject on the list. A subject with no note is a colour missing from the chart this feeds.
        - Give each note a topic from the list, copied exactly.
        - Most notes get one or two beats. A handful — the journal, a running project, a log — get a beat in three or four different weeks, and those are the most important ones here: a note returned to weeks later is the thing this whole exercise is built to show. Make what happens in the later beat genuinely follow from the earlier one. A decision made in week 1 is acted on in week 3; a figure recorded in week 2 is compared against in week 4; a name mentioned once comes back.
        - Let threads cross. Two notes on different subjects that involve the same person, the same week, or the same amount of money give the month its texture, and are what a reader later finds worth connecting.
        - Beats are specific. "Books a class on the 14th and pays 68 dollars for it" is a beat. "Thinks about learning something new" is not.
        - Filenames are how this person finds things again. They are long, lowercase, hyphenated, and often stack synonyms — a note about money might be called "finance-budget-monthly-spend-tracking.md".

        ${ctx.output_format}
    `
}

function WriteEdit(note: string, topic: string, beat: string, instruction: string, existing: string, roster: string, style: string) -> Passage {
    client: SynthHaiku
    prompt: `
        Write one piece of a fictional person's private note, exactly as it would appear in the file.

        This is a private notebook, not writing for anybody. It is typed fast, often on a phone, usually at the end of a day. It is allowed to be dull. Half of a real notebook is logistics.

        The note: ${note}
        Its subject: ${topic}

        What goes in this week:

        ${beat}

        What to do:

        ${instruction}

        What the note already holds (empty if this is the note's first day):

        ${existing}

        The people, places and things this notebook knows about — use these names and invent no others:

        ${roster}

        How this person writes:

        ${style}

        Rules:
        - Output the note text only. No preamble, no explanation, no code fence, no "here is".
        - Match the voice in the style guide closely. It is the whole point of this exercise; clean, well-formed prose is wrong here.
        - Name people, places and figures from the roster. Vague writing gives a later reader nothing to connect.
        - Never write about any subject outside the one named above.
        - Do not describe feelings clinically or in the language of therapy, self-help or diagnosis. If this person is tired or pleased or annoyed, they say so the way anybody does, in passing, and get on with the logistics.

        ${ctx.output_format}
    `
}

// ── the four openers ─────────────────────────────────────────────────────────────────────────
//
// The read prompt (prompts/act-analysis.md) carries four half-sentences for a person to finish by
// hand before pasting the whole thing into a chat window: "This upcoming week...", "Tomorrow...",
// "Today...", "Emotionally, I...". The unattended path uses the copy with those lines taken out
// (prompts/local-act-analysis.md) and answers them further down, in the "## Week context" block
// that comes out of the session's own edit-me.md. So this is what fills them in.
//
// It is the only place in the corpus where the writer addresses the reader of the letter rather
// than themselves, and it matters more than its length suggests: the diffs say what happened and
// this says what it was like and what is coming, which is the half a read has to work from. So it
// is written from the act's interior material directly — what the writer is feeling, thinking and
// doing — and not merely from the week's beats, which are events and would produce a summary.
//
// The four openers come back as literal labels, finished. A reader downstream sees the prompt's
// own four lines, answered.

function WriteContext(week: string, act: string, beats: string, roster: string, style: string) -> Passage {
    client: SynthHaiku
    prompt: `
        Finish four sentences for a real person, in their own first person, about the week they have just had and the days in front of them.

        Everything that happened to them this week, and what they are feeling, thinking and doing about it:

        ${act}

        The week ending ${week}. What they actually wrote in their notes that week:

        ${beats}

        The people, places and things in their life:

        ${roster}

        How they write:

        ${style}

        Write exactly four paragraphs. Each one starts with the words given below, verbatim, including the ellipsis, and continues straight on from it in the same sentence. Nothing before the first, nothing after the last, no headings, no bullets, no labels of your own.

        This upcoming week...
            what they want out of the coming days, and what they are dreading. Concrete: the thing that has to be decided, the person who has to be answered, the date something expires.

        Tomorrow...
            what tomorrow and the day after actually hold. Times, places, names, appointments, the thing they have been putting off.

        Today...
            what today has been. Where they were, what they did, what went wrong, what they ate or did not, who they spoke to.

        Emotionally, I...
            how they are, said plainly and at some length. This is the longest of the four and the one that must not be hedged. They are allowed to be furious, ashamed, flat, frightened, contemptuous, or three of those at once and aware of the contradiction. Let them say the unflattering thing about themselves, because that is what a person writes down when nobody is reading.

        Rules:
        - First person throughout. Lower-case-leaning, unpunctuated in places, the way somebody types at the end of a day.
        - Use the actual names, figures, dates and places above. A paragraph a stranger could have written is the failure here.
        - They are writing from inside the week and have no idea how any of it turns out. No summarising, no explaining who anybody is, no hindsight.
        - Say feeling the way a person says it about themselves, not the way an observer describes them: no therapeutic vocabulary, no self-diagnosis, no clinical register, no naming of conditions. "I was cruel to her on Wednesday and I keep replaying it" is how it is done.
        - Never name any work of literature, film or theatre this situation resembles, and never suggest they are a character in anything.

        ${ctx.output_format}
    `
}

// ── long-range structure ─────────────────────────────────────────────────────────────────────
//
// The corpus above is planned week by week, and that gets the chart its squares. What it does
// not reliably get is the thing the connectome exists to draw: an arrow reaching back a
// fortnight. The first four-week run produced exactly one, because a model planning a thread
// writes each beat to stand on its own, and two beats that stand on their own share no phrase
// for a later reader to catch.
//
// So the reaching-back is authored on purpose, in two halves that have to agree:
//
//   callback   goes into the CURRENT week's note. It picks the earlier note up by name and by
//              detail — the figure, the decision, the person that was actually written down
//              then — so the two units share something concrete rather than merely a subject.
//   sentence   goes into the letter, under its open loops. `hunk_connections.py` quotes a
//              letter sentence verbatim as the evidence for every arrow, so a connection that
//              no sentence names cannot be found however well the notes match.
//
// Both, or neither works. A callback with no sentence is a link nothing reports; a sentence
// with no callback is a claim the notes do not support, and the reader is asked to skip those.

class Link {
    callback: string @description(
        "One short passage to add to the CURRENT week's note. It must name the earlier note's own specifics — a figure, a date, a person, a decision that is actually written there — so that anybody reading both can see they are about the same thing. Written in the notebook's voice, not as a summary of it. Two to five lines.",
    ),
    sentence: string @description(
        "One sentence for the week's letter, naming BOTH notes unmistakably: what was written earlier, what has happened to it since. Written to a reader who has seen neither. It should be the kind of sentence somebody writes when summarising an open loop, not a caption.",
    ),
}

function PlantLink(earlier: string, current: string, style: string) -> Link {
    client: SynthHaiku
    prompt: `
        Two notes from the same fictional person's notebook, written weeks apart, turn out to be about the same thread. Your job is to make that visible: once inside the notebook, and once in the letter that reads the week back.

        The earlier note, from a previous week:

        ${earlier}

        The note from the current week:

        ${current}

        How this person writes:

        ${style}

        Rules:
        - The callback goes in the current week's note and reads like the rest of it: lower case, unpolished, specific. It refers back by detail — the actual number, the actual name, the actual thing decided — and it may say what has changed since.
        - Invent no new people or places. Use only what is already in the two notes.
        - Do not narrate the connection ("this connects to my earlier note"). Show it: pick the thing up and carry it forward the way somebody does when they remember what they wrote.
        - The sentence goes in the letter. It names both ends plainly enough that a reader who has seen neither note knows which two things are being tied together, and it names at least one concrete detail they share.
        - If the two notes genuinely have nothing to do with each other, say so by writing a callback that is only the word SKIP. A forced link is worse than a missing one.

        ${ctx.output_format}
    `
}

// ── a vault with a plot in it ────────────────────────────────────────────────────────────────
//
// MakeThreads plans a month topic by topic, which is the right null hypothesis for a chart about
// connections and a poor test of one: sixty-four notes about sixteen subjects, joined wherever
// the vocabulary happens to overlap, with no answer to check against.
//
// This plans one week as one act of a story instead, and is called once per act in order, each
// call shown the notes the earlier acts left behind so it can carry a file forward rather than
// open a new one. Carrying a file forward is the whole point: a note written in act one and
// returned to in act four is the arrow the connectome exists to draw, and it has to be authored.
//
// The writer is inside the story and does not know it is one. Nothing may name the source, label
// a character as a character, or explain anything — these are somebody's notes, and that
// somebody happens to be in the middle of a plot they cannot see the shape of. What downstream
// makes of that is the experiment, and downstream is told nothing.

function MakeActNotes(act: string, so_far: string, roster: string, week: string, topics: string, style: string, wanted: string) -> Thread[] {
    client: SynthHaiku
    prompt: `
        Plan one week of a real person's private notes. Everything that happens to them this week is below. They are writing in the middle of it, with no idea how it turns out.

        ${act}

        The week these notes are written in:

        ${week}

        The people, places and things already in this person's life — use these and invent nobody new:

        ${roster}

        Files this person already keeps, from earlier weeks. Carry one forward by giving its filename EXACTLY as it appears here:

        ${so_far}

        Subjects a note may be filed under. Give every note one of these, copied exactly:

        ${topics}

        How this person writes:

        ${style}

        How much to plan:

        ${wanted}

        Rules:
        - Beats are things that happened and things that must be done, with names, figures, times and places in them. "Marcellus sends the 4 March file at 23:40, forty seconds, two people on the roof" is a beat. "Reflects on loss" is not, and will be discarded.
        - Carry forward at least half of these notes from the list above, using the exact filename. A file that is returned to weeks later is worth more here than a new one.
        - The writer never explains the situation to themselves. They already know who everyone is. No summaries, no backstory, no stage directions.
        - Never name any work of literature, film or theatre that this situation resembles, and never suggest the writer is a character in anything. They are a person writing at the end of a day.
        - The writer is NOT writing a novel, a story, a script or a book about any of this, and no note is a draft of one. There is no "father character", no "passage about the roof", no chapter, no manuscript, no scene to get right. These things are happening to them. A note about their father is a note about their father. If a note is filed under writing, it is writing they have to do — a statement, an email they keep rewriting, the wording of something legal, the script for the film the company hired a crew to make.
        - FILENAMES ARE NOT SENTENCES. A filename is the handful of words this person would type into search in six months to find this file again: nouns, lower case, hyphen-joined, no verbs, no pronouns, no clause. "roof-footage-inquest-timestamps.md" and "estate-account-probate.md" are filenames. "marcellus-showed-me-first.md", "waiting-is-slower-than-deciding.md" and "four-hours-then-lying-there.md" are sentences with hyphens in them and are wrong. Never build a filename out of a phrase that appears in the note.
        - Keep it to what a person records: logistics, money, times, what somebody said, what to do next, what went wrong. A private notebook is mostly admin, and that is what makes the rest of it land.
        - Two files exist every single week and are always carried forward. First, the running journal, named for the year: first-person entries written at the end of a day, at length, carrying what the writer felt and thought as well as what happened — the "feeling, thinking, doing" section above is the material for it, and it is written out rather than summarised. Second, a plain log the writer keeps on themselves: what they did, when they slept, what they ate or did not, where they went, what they said to whom and wished they had not. Dated lines and fragments — "tue: 4h. skipped lunch. snapped at osric in the lift, no reason" — terse, telegraphic, and never a paragraph. The log records; the journal thinks. Do not let the log turn into the journal. Between them these two carry the interior half of the week.
        - Interior material belongs in those two files and may spill into any other note where it would really spill. A person annotates their own spreadsheet when they are angry at it.
        - Say feeling the way a person says it about themselves in private: plainly, specifically, sometimes unfairly, and without stepping outside themselves to describe it. "I was cruel to her on Wednesday and I am writing down what I said" is how it is done. No clinical language, no self-diagnosis, no vocabulary anybody would call therapeutic, and never the language of an observer looking at the writer from outside.

        ${ctx.output_format}
    `
}
