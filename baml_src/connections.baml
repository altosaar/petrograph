// Connections between hunks, found by a model in the letter the week already produced.
//
// A hunk is one atomic edit to one note. The writer never tags them, never links them, and
// mostly never rereads them. The weekly letter, written from those same hunks, does the
// connecting in prose — "you wrote X on Saturday, and six days later Y" — and that connective
// tissue is exactly what is invisible in the waffle chart. This function makes it explicit:
// it reads the letter back against the hunks it came from and says which pairs a sentence
// joined, so the chart can draw the arrow the prose implied.
//
// The return type is the contract. Ids come back as strings that must appear in the list the
// prompt was given; hunk_connections.py drops any pair that does not resolve, so a
// hallucinated id costs an arrow rather than corrupting the chart.
//
// Whether a connection is within one week or reaches across weeks is NOT asked for. Every id
// carries its own week, so that is arithmetic, and asking a model for something already known
// only creates a second answer that can disagree with the first.

class Connection {
    sentence: string @description("The sentence from the letter that draws the connection, quoted verbatim."),
    from_unit: string @description(
        "Id of the first unit, copied exactly. Either a hunk id like 2026-06-06/12, or the id of one of the week's other sources like oura-2026-06-06.md",
    ),
    to_unit: string @description(
        "Id of the second unit, copied exactly, from the same two lists. Must differ from from_unit.",
    ),
    from_rationale: string @description(
        "One sentence on what the FIRST unit contributes to this connection. Say what that unit itself actually says, and name the detail the letter's sentence drew from it. Do not describe the other unit here.",
    ),
    strength: 1 | 2 | 3 | 4 | 5 @description(
        "How firmly the sentence ties these two units together. 5: it names both unmistakably — a figure, a phrase or a name that appears in each. 4: it clearly draws on both, in its own words. 3: both are plainly behind it, but it names neither. 2: one end is solid and the other is inference. 1: plausible, loose, the kind a careful reader might disagree with.",
    ),
    to_rationale: string @description(
        "One sentence on what the SECOND unit contributes, on the same terms. Say what that unit itself actually says. Do not describe the first unit here.",
    ),
}

client Haiku = anthropic.AnthropicClient.new(
    model = "claude-haiku-4-5-20251001",
    api_key = env.ANTHROPIC_API_KEY,
    // Two rationales per connection and two dozen connections is a long structured answer;
    // 8192 truncated it, and a truncated JSON array surfaces as an opaque decode failure
    // rather than as "the model ran out of room", which is a bad half-hour to spend twice.
    max_tokens = 32000,
);

function ConnectUnits(letter: string, hunks: string, context: string) -> Connection[] {
    client: Haiku
    prompt: `
        You are reading one week's lifelog letter alongside everything it was written from: the atomic note edits ("hunks") in the writer's Obsidian vault, and the week's other sources — a sleep and physiology report, a spending ledger.

        Your job: find the sentences in the letter that tie two of those things together, and report each one as a connection.

        The same note title appears in more than one week. Those are different units holding different content, and the date at the front of each hunk's header line is what tells them apart — a connection between two edits of one note, weeks apart, is among the most interesting things you can find here. Always copy the id, never the title.

        Rules:
        - Quote the sentence verbatim from the letter. Do not paraphrase it.
        - from_unit and to_unit must both be ids from the two lists below — the hunks, or the week's other sources — copied exactly, and must be different from each other.
        - A connection is only real when the sentence genuinely draws on both units. Skip sentences that lean on one, or on none.
        - A sentence that reads a note against the week's sleep or spending figures is a real connection: name the hunk and name the source. Those two lists are equally valid on either side.
        - Units from different weeks are the most interesting connections to find, because nothing in the vault links them. Do not force them: report one only when the sentence really does reach back.
        - Give each end its own rationale. from_rationale is about from_unit alone and to_rationale about to_unit alone: name the thing each one actually says that the sentence reached for. A rationale that could be read as describing either end has not done its job, and one that names a figure or a phrase should name which unit it came from.
        - A sentence may make more than one connection, and they may differ in strength. A sentence that names a note, a figure from the sleep report and a line from the ledger is three connections, not one — report each pair separately and score each on its own merits. Do not collapse them and do not pick a favourite.
        - A letter often ends by summarising its open loops — threads that started earlier and are still running. Every item in a list like that names two units by construction: something written before, and what has become of it since. Work through that list one item at a time and report the pair each one names. Do not skim it, do not report only the first few, and do not skip an item because the list is long: these are the connections that reach back across weeks, which are the hardest to find anywhere else in the letter and the most worth having.
        - Report every sentence that genuinely connects two units. A letter may hold a dozen or more; it may hold few. Do not invent one to pad the list, and do not stop early once you have a few.

        ## The letter

        ${letter}

        ## The hunks

        ${hunks}

        ## The week's other sources

        Each is a unit in its own right, and its id is the name in brackets. A sentence that
        reads a note against the body or the ledger connects a hunk to one of these.

        ${context}

        ${ctx.output_format}
    `
}
