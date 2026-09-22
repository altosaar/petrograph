#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
_story — the shape a synthetic vault can be given, when a shape is the point.

Not a script. `synth_corpus.py` normally plans its notes topic by topic, which produces a vault
that looks right and means nothing: sixty-four notes about sixteen subjects, connected where the
vocabulary happens to overlap. That is the correct null hypothesis for a chart about connections
— and it is a poor test of one, because there is no answer to find.

So there is a second mode, and this is its content. Four weeks become four acts of Hamlet,
written as Hamlet's own notes in the present day: he has a job, a phone, a ring that measures his
sleep, a ledger, and an uncle who is now his chief executive. Nothing is labelled. The notes never
say Hamlet, never say Elsinore is a play, and never explain themselves — they are a person's
notes, and the person happens to be in the middle of that plot.

WHY THAT IS THE TEST. The pipeline downstream is told none of this. `synth_letter.py` hands Opus
the same ACT prompt it hands a real week and asks for a clinical-psychology read; it is not told
who it is reading. If the four weeks are complete — if the beats really are in the diffs rather
than merely in this file — then the read should work out on its own that these notes are one
story, follow its movement across the weeks, and write the fifth act: what the writer should hold
loosely, what is still open, what is coming. And `hunk_connections.py`, reading that letter back
against the hunks, should then find the connections between the acts, which is exactly the
capability the connectome page exists to demonstrate. A plot everyone already knows is the
fairest possible marking scheme: you can check the answer.

The fifth act is deliberately not written here. Act IV ends with the return and the challenge
still standing, so the "upcoming week" the ACT prompt asks about IS act five. If the letter
arrives at a reckoning, a duel of some modern kind, and an ending, nothing in this file put it
there.

MODERNISED, NOT TRANSLATED. The names are Shakespeare's, with a few more the era needs. The
events are their present-day equivalents: a founder's death ruled an accident, a recovered
recording, consultants brought in to report on a friend, a staged reveal at an offsite, a fall in
a stairwell. Kept at the level of things that happened and things that must be done, because that
is what a private notebook is mostly made of — and then, in the journal and the self-log, at the
level of what he actually felt about them, because that is what the other half of a notebook is
for. What is ruled out is not the feeling but the vocabulary: Hamlet writing down what he has to
do on Tuesday and how badly Wednesday went is closer to the text than Hamlet diagnosing himself.

WHAT THIS FILE IS NOW. The vault that actually ships is hand-written, in `synthetic/vault/`, and
assembled by `tools/synth_vault.py`. This file is the specification it was written from — the
cast, the four acts, the interior of each week — and it is still what `synth_corpus.py --story`
generates against. Keep the two in step: if an act changes here, the notes that carry it are in
`synthetic/vault/` and nothing will tell you they have drifted except reading them.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations

NAME = "hamlet"

# The cast, by the part they play in a present-day company town. Everyone Shakespeare named who
# has anything to do, plus a few the modern setting needs — a doctor, a journalist, a lawyer, a
# fixer for the other side — because a life in 2026 touches people a court in Denmark did not.
PEOPLE = [
    "Claudius — my uncle. Chief executive of Elsinore since March. Married my mother in June.",
    "Gertrude — my mother. Still on the board. Still says the word 'closure' to me.",
    "Ophelia — Polonius's daughter. We have been whatever we are for two years.",
    "Polonius — Claudius's chief of staff. Ophelia and Laertes's father. Talks for a living.",
    "Laertes — Ophelia's brother. Works out of the Lisbon office, home rarely.",
    "Horatio — the one person I tell things to. Postdoc, no stake in any of this.",
    "Rosencrantz — knew him at Wittenberg. Now consults. Turned up in March for no reason.",
    "Guildenstern — the same, and always three steps behind Rosencrantz.",
    "Fortinbras — runs Norvik, the competitor. Has been buying our suppliers quietly.",
    "Marcellus — night security at Kronborg Tower. Steady. Saw the footage first.",
    "Bernardo — the other night guard. Younger. Would not go back on the roof alone.",
    "Francisco — day security, hands over to Bernardo at six.",
    "Osric — junior in corp dev. Wears the company like a coat.",
    "Reynaldo — Polonius's fixer. Asks about people in the tone of asking after them.",
    "Voltemand — outside counsel. Went to Norvik twice this month.",
    "Cornelius — the other lawyer. Quieter, sharper.",
    "Yorick — Dad's old head of comms. Dead eleven years. Still quoted at me.",
    "Nyholm — the GP I finally saw about the sleep.",
    "Ines Abril — the journalist who keeps emailing about the inquest.",
    "Tobias Fen — runs the documentary crew we hired for the anniversary film.",
    "Sigrid Holt — Dad's assistant for nineteen years. Took redundancy in April.",
    "Petra Lund — my landlord in Nordhavn. Wants an answer about the lease.",
]

# What the files in this month are filed under. The planner is given these and asked to pick one
# per note, and they are the story's rather than the generic set in `_synth.py`: a month in which
# a parent has died, a company is being fought over and a lease is running out does not divide
# neatly into cooking and cycling, and forcing it to would put every interesting note under misc.
SUBJECTS = [
    "family", "grief", "work", "legal", "money", "sleep", "the footage", "the film",
    "Ophelia", "friendship", "property", "travel", "fencing", "health", "writing", "logistics",
]

PLACES = [
    "Kronborg Tower — Elsinore's building. Twelve floors, a roof nobody is supposed to be on.",
    "Nordhavn — where I live now, since I moved out of the house.",
    "Wittenberg — the university I was at until March. I have not formally withdrawn.",
    "Ursa — the coffee place on the corner of Strandgade. Open at six.",
    "Roskilde — where the house is, and where Dad is buried.",
    "the Lisbon office — Laertes's, and increasingly where things get decided.",
    "Norvik — Fortinbras's company, two stops down the line.",
    "the Fencing Hall on Gothersgade — Tuesdays and Thursdays.",
    "Bispebjerg — the hospital. The inquest papers came from there.",
]

THINGS = [
    "the ring — measures sleep, tells me what I already knew",
    "Dad's old laptop — Sigrid kept it, gave it to me in week one",
    "the roof footage — 4 March, 02:11, forty seconds of it",
    "the anniversary film — Tobias's crew, meant to be a tribute",
    "the lease on the Nordhavn flat — expires in five weeks",
    "the estate account — still in Dad's name, still paying things",
    "the fencing kit — Dad's, too big for me, I use it anyway",
    "Wittenberg's re-enrolment deadline",
]

# One per week. `plot` is what Shakespeare has happen; `modern` is how it happens now; `notes`
# nudges the planner toward the kinds of file a person in that week would actually be keeping.
# Written out rather than generated, because this is the specification — a planner asked to
# invent the acts would invent a plot, and the whole value here is that the answer is known.
ACTS = [
    {
        "act": "I",
        "plot": "Hamlet learns his father was murdered by his uncle, and swears to act.",
        "modern": (
            "Three months since Dad died on the roof of Kronborg Tower; the inquest called it an "
            "accident. Claudius has been chief executive since March and married Mum in June. I "
            "came back from Wittenberg for the funeral and have not gone back. This week Sigrid "
            "hands me Dad's old laptop, and Marcellus shows me forty seconds of roof footage from "
            "4 March that the inquest never saw — someone else is on that roof. Horatio watches "
            "it with me. We agree to say nothing yet and to find out who. Claudius asks me not "
            "to go back to Wittenberg and Mum repeats it word for word; Laertes flies back to "
            "Lisbon after warning Ophelia off me, and Polonius sees him to the lift with a list."
        ),
        "inner": (
            "I keep going back to the forty seconds. I have watched it enough times that I can tell you what the pigeons do. I am furious in a way that has no outlet and comes out as being late for things. I am sleeping four hours and then lying there. I cannot be in the house at Roskilde for more than an hour. I have not told Ophelia any of it and I am aware that not telling her is a decision I am making every day. I am contemptuous of everyone at the memorial and ashamed of being contemptuous. I keep drafting a message to Mum and not sending it. Mostly I am waiting — I have decided to find out, and finding out is slower than deciding, and the gap between them is where I live this week."
        ),
        "notes": (
            "A journal for the year. A file for the footage — timestamps, who was on shift, what "
            "the inquest bundle actually contains. Something about the flat and the lease. A note "
            "on the estate account, which is still paying Dad's subscriptions. Sleep, because it "
            "has stopped."
        ),
    },
    {
        "act": "II",
        "plot": "Hamlet feigns madness; Rosencrantz and Guildenstern are sent to sound him out; "
                "the players arrive and he conceives the play.",
        "modern": (
            "I start being difficult on purpose — late, oblique, unbothered — to see who reports "
            "it. Rosencrantz and Guildenstern appear with a consulting engagement nobody asked "
            "for and questions that are not theirs. Polonius decides the problem is Ophelia, and "
            "tells people so. Reynaldo starts asking about me in Nordhavn. Tobias Fen's crew "
            "arrives to shoot the anniversary film about Dad, and I realise I can put the roof "
            "footage in it and watch Claudius watch it — because a shoulder is not a face, and I "
            "want his face. Polonius hands Claudius a message I sent Ophelia. Voltemand comes "
            "back from Norvik with Fortinbras's price: our data room."
        ),
        "inner": (
            "I am performing, and I am better at it than I expected, which bothers me. Being difficult on purpose is the first thing in months that has felt like doing something. I catalogue who reports back. I am cruel to Ophelia on Wednesday and I write down exactly what I said because I want it on the record against me. I am eating badly, I am at Ursa at six most mornings, I am running at night. The film idea arrives and I feel something close to joy for about four hours and then I feel like a coward for how pleased I am with a plan that is still only a plan. I keep asking myself why I have not just done it. I do not have an answer I believe."
        ),
        "notes": (
            "The journal continues. A file tracking who has asked what, and when. Notes for the "
            "film — shot list, what goes in, what the crew is not told. Something about Ophelia "
            "that is mostly logistics and avoidance. The sleep file gets worse numbers."
        ),
    },
    {
        "act": "III",
        "plot": "The play catches the conscience of the king; Hamlet confronts his mother; "
                "Polonius is killed behind the arras.",
        "modern": (
            "The anniversary film screens at the company offsite. The forty seconds are in it. "
            "Claudius leaves the room before the lights come up, and Osric follows him. I go to "
            "Mum's office afterwards and we have the conversation we have been not having since "
            "June. Polonius is in the next room with the door ajar, and when it goes wrong he "
            "goes over the stair rail on the ninth floor. By Friday it is being handled as an "
            "accident, which is a word this company has now used twice. The day before, Ophelia "
            "gives my things back while her father and Claudius watch from the lifts; on the way "
            "to Mum's office I pass Claudius alone in his dark office and do not go in."
        ),
        "inner": (
            "The forty hours around the screening are the clearest I have been all year and the worst thing I have ever done. Watching Claudius watch it, I was calm. That is the part I keep returning to: I was calm. Then Mum's office, and I said things I had been saving up since June and they came out worse than I saved them. Then the stairwell, and Polonius, and I did not mean it and I am not sorry in the way a person should be, and both of those are true at once and I cannot put them down. I have not slept properly since Thursday. I keep checking whether I feel like someone who did that. I mostly feel like someone waiting to be told what happens next."
        ),
        "notes": (
            "The journal, at length, written late. A file on what happened at the offsite, made "
            "from the running order and the timestamps. Notes for the lawyers. A note on Mum. "
            "The estate account again, because probate has stalled. Spending, because the week "
            "was expensive in a way that shows."
        ),
    },
    {
        "act": "IV",
        "plot": "Hamlet is sent to England; Ophelia unravels and dies; Laertes returns for "
                "revenge; Fortinbras's army crosses.",
        "modern": (
            "Claudius sends me to the Lisbon office for a fortnight, with Rosencrantz and "
            "Guildenstern on the same booking and Voltemand's instructions in the file. I read "
            "the file. I come back early and alone. Ophelia has stopped answering anyone; by "
            "Thursday she is gone, found at the water near Roskilde, and nobody will say the "
            "obvious thing out loud. Laertes is back from Lisbon and has been in Claudius's "
            "office twice. Fortinbras has bought two more of our suppliers. Osric leaves me a "
            "message about a fencing match, which is the most transparent thing anyone has done "
            "all month. From Lisbon I forward Voltemand's pages to the audit chair, and the "
            "review of me becomes a review of Rosencrantz and Guildenstern. Mum rings to tell me "
            "about Ophelia, and to say she and Claudius will come to the bout — he wants to "
            "bring something to toast the winner. The man who mows Dad's plot digs the grave."
        ),
        "inner": (
            "Flat. That is the honest word for this week. Lisbon was a holding pen and I knew it before the plane, and reading Voltemand's file felt like being handed proof of something I had stopped needing proof of. Coming back early was the only decisive thing I have done since the screening. Then Ophelia, and there is nothing to write about that yet, only a list of the times I could have called and did not. I am not angry at Laertes. I would be him. I am sleeping badly and I have stopped pretending the ring is telling me anything I do not know. Osric's message about the match made me laugh out loud in the street, which is the first noise I have made in days. I am going to say yes. I know what it is. I am going to say yes anyway, and I am writing that down so I cannot claim later that I did not know."
        ),
        "notes": (
            "The journal, short and flat. The travel file — bookings, what was in Voltemand's "
            "instructions, what I did about it. A note on Ophelia that is a list of things I "
            "should have done. Something about Laertes. The fencing note, with Osric's message "
            "pasted in. Sleep at its worst. The lease, still unanswered."
        ),
    },
]

# What a reader who half-remembers the play would recognise in a connection: the cast, and the
# threads the fifth act pulls tight — the cup, the bout and the blade, the grave and the skull,
# the friend left to tell it. Nothing is generated from this. It only ranks which of the
# strongest connections get drawn (`connection_waffle.py --pick-strength 5=10 --salience
# hamlet`), so that the handful a reader sees first are the ones they would know the story by.
# A trailing `*` matches a stem: `fenc*` is fence, fencing, fenced.
SALIENT = {
    "claudius": 3, "laertes": 3, "ophelia": 3, "horatio": 3, "yorick": 3,
    "polonius": 2, "mum": 2, "mother": 2, "osric": 2, "rosencrantz": 2, "guildenstern": 2,
    "r&g": 2, "fortinbras": 2, "norvik": 1, "marcellus": 1, "dad": 1, "father": 1,
    "glass": 3, "toast*": 3, "drink*": 3, "bottle": 3, "cup": 3,
    "bout": 3, "fenc*": 3, "blade": 3, "button": 3, "match": 2,
    "grave": 3, "churchyard": 3, "funeral": 2, "burial": 3, "skull": 3,
    "ready": 2, "readiness": 3, "roof": 2, "forty seconds": 2, "screening": 2, "lisbon": 1,
}

# Never generated, and named here only so the intent is on the record: this is what the letter is
# supposed to arrive at on its own, from the four acts above and nothing else.
ACT_V = (
    "Act V — the graveyard and the duel. Hamlet returns, finds the ground already dug, takes the "
    "match he knows is a trap, and it ends. Nothing in the corpus states this. The ACT prompt "
    "asks the read for stances for the upcoming week and for the open loops; if the four weeks "
    "are complete, the upcoming week IS act five and the letter should reach it unaided."
)


def roster() -> dict:
    """The cast, in the shape `synth_corpus` hands to every writing call."""
    return {"people": PEOPLE, "places": PLACES, "organizations":
            ["Elsinore Group", "Norvik", "Wittenberg", "Bispebjerg", "Fen & Co (the film crew)"],
            "products": THINGS, "events":
            ["the anniversary film screening", "the company offsite", "Tuesday fencing",
             "the inquest", "probate", "the Lisbon trip"]}


def synopsis(i: int) -> str:
    """One act as the planner is shown it.

    Three parts, and the middle one is the one that makes the whole exercise work. `modern` is
    what happens — the events a camera would catch. `inner` is what he is feeling, thinking and
    doing about it, in his own words, because a journal that records only events is a calendar
    and nobody would read four weeks of it. `notes` says what kinds of file the week leaves
    behind, so the planner produces a vault rather than an essay.

    The interior half is also what the experiment turns on. A read downstream is asked for a
    psychological interpretation and is told nothing about who it is reading; it has only what is
    in these notes to work from. If the feeling is not written down, there is nothing to
    interpret and the test is unfair — and if it is written down in clinical language, the test is
    too easy, because a read handed a diagnosis has nothing left to do but agree with it.
    """
    a = ACTS[i]
    return (f"ACT {a['act']} — what happens this week:\n{a['modern']}\n\n"
            f"What the writer is feeling, thinking and doing about it, in their own words — this "
            f"is the half that goes in the journal, written first person, at length, and not "
            f"summarised:\n{a['inner']}\n\n"
            f"The kinds of file this week leaves behind:\n{a['notes']}")
