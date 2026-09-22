// What each unit is about, decided one unit at a time against a list that grows as it goes.
//
// The connectome colours squares by what the edit *did* — added, removed, mixed. That is a
// property of the diff and not of the writer: a week of pruning one note and a week of building
// another look alike. Topic is the other axis, and nothing in the vault records it. There are no
// tags, the folder is a date, and a note's title is a name its author chose years ago.
//
// Two topics are fixed, because two of the week's sources are: `finance` for the ledger and
// everything that spends or earns, `physiology` for sleep, activity and the rest of what the
// ring records. Everything else is discovered. Each call sees the topics found so far and either
// picks one or names a new one, so the vocabulary is built by reading rather than declared in
// advance — and stays small, because reusing a topic is always the easier answer.

client TopicHaiku = anthropic.AnthropicClient.new(
    model = "claude-haiku-4-5-20251001",
    api_key = env.ANTHROPIC_API_KEY,
    max_tokens = 200,
);

// A class and not a bare string. Asked for a string, the model sometimes answers with the
// unit's own prose — "i need to book the flight" — and prose truncated to two words looks
// exactly like a topic and is not one. A field with a description is the schema the model is
// filling in rather than a blank it is writing into, which is what BAML is for.
class UnitTopic {
    topic: string @description(
        "One or two lowercase words naming the subject, as a plain noun someone would recognise: health, travel, writing, work, family. Never a phrase, never a sentence, never words copied out of the unit's text, and never the note's own title.",
    ),
}

function ClassifyUnit(unit: string, topics: string) -> UnitTopic {
    client: TopicHaiku
    prompt: `
        You are giving one unit of a personal knowledge vault a topic. A unit is either one edit to one note, or one of the week's own sources.

        Topics found so far, in order of use:

        ${topics}

        Rules:
        - Reuse a topic from that list whenever the unit plausibly belongs to it. Reuse is almost always the right answer; a vocabulary of a dozen topics is useful and one of eighty is not.
        - Name a new topic only when nothing on the list fits. Keep it one or two lowercase words, a plain noun for a subject someone would recognise — "health", "travel", "writing" — never a phrase, never a sentence, never the note's own title.
        - finance covers money in every form: the ledger, spending, income, banking, bills.
        - physiology covers the body as measured: sleep, activity, heart rate, recovery, illness.
        - The topic names a subject, not a feeling and not an action. If the unit reads as a person's own words, ask what it is *about* rather than what it says.

        The unit:

        ${unit}

        ${ctx.output_format}
    `
}
