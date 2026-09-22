// What a real hunk is SHAPED like — and nothing about what it says.
//
// This is the only function in the repo that reads the private vault and hands it to a model,
// and its whole design is the wall between that read and the synthetic corpus written from it.
// The return type has no string field. Not "should not contain" a name — cannot: there is
// nowhere for one to go. A model that decided to be helpful and quote the writer back would
// have to put it in an integer.
//
// Everything a computer can count is counted in Python instead (synth_profile.py): hunk sizes,
// add/delete ratios, how often a note recurs across weeks, how often a line starts lowercase.
// A model is asked only for the two judgements arithmetic cannot make — how many distinct
// entities of each kind a hunk names, and whether it holds text pasted in from somebody else.
//
// The counts are then averaged over two hundred hunks before anything is written down, so even
// the numbers that survive describe the habit rather than any hunk.

class HunkShape {
    people: int @description("How many distinct named people this hunk mentions. Count each person once, however many times they appear. A first name alone counts. 0 if none."),
    places: int @description("How many distinct named places — cities, neighbourhoods, venues, countries. Not 'the park' or 'home'; a name."),
    organizations: int @description("How many distinct named companies, employers, institutions, bands, clubs or products-as-brands."),
    addresses: int @description("How many street addresses, apartment numbers, postcodes or precise locations appear."),
    events: int @description("How many distinct named or dated occasions — a gig, a trip, an appointment, a party, a deadline."),
    numbers: int @description("How many concrete figures appear — money, times, durations, counts, measurements."),
    has_pasted_message: bool @description("True when part of this hunk is text written by somebody else and pasted in: a message, an email, a quote from a chat."),
    has_typos: bool @description("True when the writing holds obvious spelling slips, missing apostrophes, doubled words or dropped capitals."),
    has_tags: bool @description("True when the hunk uses #hashtags or [[wiki links]]."),
}

client ProfileHaiku = anthropic.AnthropicClient.new(
    model = "claude-haiku-4-5-20251001",
    api_key = env.ANTHROPIC_API_KEY,
    // A dozen integers. There is nothing long to say.
    max_tokens = 400,
);

function ProfileHunk(hunk: string) -> HunkShape {
    client: ProfileHaiku
    prompt: `
        You are measuring one edit from somebody's personal notes, so that a realistic but entirely fictional imitation of their notes can be generated later without anybody reading the real ones.

        Count what is there. Do not summarise it, do not judge it, and do not repeat any of it back — the answer is numbers and true/false, and there is nowhere in the answer for a word from this text to go.

        The edit:

        ${hunk}

        ${ctx.output_format}
    `
}
