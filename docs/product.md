# Product

Wordwinnow answers one question for a learner of English.

> Here is a text I want to read: which of its words deserve my attention, what
> do they mean, and how hard are they?

The answer is a study report: the vocabulary of the text, placed in tiers
relative to the learner, each word with a level and a meaning, and, on its
card, the sentence it appeared in.

## Why it exists

Enjoying English and learning its vocabulary pull against each other: stopping
at every unfamiliar word spoils an episode or a story, and never stopping lets
the words go by.

Wordwinnow does the looking-up before you start, so you can enjoy the text and
still learn from it: it points at the words worth your attention at your
level, and you decide which of them to learn.

It does not teach and it does not remember what you learned; the learning
stays yours.

Where the idea came from is told beside the sample story, in
[`data/corpus/README.md`](../data/corpus/README.md).

### The name

To winnow is to separate grain from chaff: the harvest is tossed into the
wind, which carries the light husks away and lets the grain fall.

Wordwinnow does that to a text, with your level, your goal, and the words you
know as the wind: the words that deserve your attention now land in FOCUS, the
ones to keep for later in STRETCH, and the ones you know, or likely know, in
KNOWN and REVIEW.

The name is *word* and *winnow*, and that is what it means.

It also carries a second reading, on purpose: split another way, it spells
*word, win, now*, the words you can win from a text you want to read, while you
read it.

That reading is wordplay for the eye, not the pronunciation: the name is said
as *word* and *winnow*, and *winnow* rhymes with *minnow*.

*Winnow* is also a word the tool would point out itself, for instance in this
sentence:

> Farmers winnow the grain in the wind.

No reference list has *winnow*, the level model places it at C2, and a B1
learner finds it among the STRETCH words, glossed "separate the chaff from by
using air currents."

### Wordwinnow and Wordbench

Wordwinnow is the earlier of two projects in one line of my work on learning
vocabulary from real texts; Wordbench, the later one, is a separate project,
more mature and built around the product.

Wordwinnow continues an academic prototype of mine, which turned a text into
flashcards on its own and chose the words for the learner; Wordbench grew from
the same line of work, and neither project is a version of the other.

Wordwinnow was shaped by its technologies, and by the requirements of the
course it was built for, more than by its learner: the system came first, and
the product took the form that let a distributed system be built, measured,
and reasoned about.

That made it a sound exercise in systems engineering and a limited product,
and I count it as a mistake worth naming: it answers well how to build such a
system, and less well what a learner most needs from one.

Its question is the engineering one: how to build, and reason about, a system
that takes its texts in through outside providers, spreads the work across
services, survives their failures, and stays correct when work is repeated.

Answering it is where I learned to draw a domain's boundaries, to keep
providers behind ports and adapters, to let services talk through events, to
make repeated work harmless, and to watch a pipeline of processes through its
metrics, traces, and logs.

It keeps the prototype's simple product model, one level for a word and one
for the learner, whose cost this page sets out, and departs from the prototype
where a learner feels it most: Wordwinnow points, and leaves every decision
about learning to you.

Wordbench reverses the order on purpose: it starts from the product question,
what a vocabulary tool should actually help a learner do and which decisions
must stay the learner's, and chooses its technology to serve the answer.

The lesson I take from the two is plain: when the technology is chosen first,
the product bends to fit it, so in building a product the problem has to come
first and the technology has to follow.

Within Wordwinnow, that lesson does not make any one technology arbitrary:
each still has to answer a concrete problem of the running system, and
[`architecture.md`](architecture.md#why-each-technology-is-here) names that problem for each one, including those that go
beyond what one learner needs, and shows where the technology shaped the
domain.

## Who it is for

A learner at roughly A2 to B2 who brings their own texts: an article, a
chapter, the subtitles of an episode they watched.

Someone preparing for an examination such as IELTS or TOEFL can run a practice
passage through it and see, before reading, which words sit above their level
and what they mean.

The learner is comfortable in a terminal: the command line is the whole
interface, and the learner runs the tool themselves, in one process or as
services under Docker Compose.

## Who it is not for

- A beginner who needs a course: the tool analyzes a text, it does not teach.
- A C1 or C2 learner: the lists stop at C2, the tiers flatten near the top,
  and what such a learner needs, uncommon senses and collocations, is what the
  tool sees least.
- Anyone who wants an application with a screen, an account, or progress kept
  between sessions.
- A teacher who needs a readability score: the tool measures how rare a text's
  words are, which is not the same as how hard the text is to read.

## The learner workflow

1. Bring a text: a file, standard input, or a New York Times source the tool
   fetches.
2. Say which [CEFR level](https://www.coe.int/en/web/common-european-framework-reference-languages/table-2-cefr-3.3-common-reference-levels-self-assessment-grid) you have, which level you are working toward, and,
   optionally, which words you already know.
3. Read the vocabulary in study order: the FOCUS tier first, then the cards
   for the words you want to keep.
4. Export the report as CSV for a flashcard tool, or read it again later with
   `wordwinnow show`.

The list is a set of suggestions, not a syllabus: skim the FOCUS words before
you read, keep the ones worth learning, and let the rest go.

When a level looks wrong, the report says where it came from, a reference
list, the level model, or the frequency heuristic, so you can tell a judgment
made by people from an estimate; a word you already know goes in the `--known`
file, and from then on it is KNOWN, listed last and struck through.

## What the report says about a word

| Field | Where it comes from |
| --- | --- |
| Lemma and part of speech | The text, through segmentation, tagging, and lemmatization |
| Occurrence count and a sentence | The text |
| CEFR level and its source | A reference list, the level model, or the frequency heuristic |
| Study tier | The level against the learner's level, target, and known words |
| Meaning | The dictionary's definition, or [WordNet](https://wordnet.princeton.edu/)'s gloss when the dictionary has none |
| Where the meaning comes from | The license the dictionary's definitions are shown under, and the page each one came from where the dictionary has one |
| The dictionary's example and a pronunciation | The dictionary: the Free Dictionary API, or WordNet, which has no pronunciation, when it is chosen |
| Synonyms, broader terms, opposites | WordNet, constrained by part of speech |

WordNet is local and always answers; the Free Dictionary API is external and
optional: an answer can take many seconds, and the service can be unavailable,
so an analysis finishes without it.

The Free Dictionary API serves Wiktionary's definitions with the license it
reports for them, CC BY-SA 3.0, so the table names that license under the
words, and a card and a CSV row carry the Wiktionary page of the definition
shown.

## What Wordwinnow knows about the learner

Three facts, and nothing else changes the analysis.

| Fact | Effect |
| --- | --- |
| The level the learner says they have | Words below it are REVIEW |
| The level they are working toward, one above their own by default | Words from their level up to it are FOCUS, words above it STRETCH |
| The words they already know | Those words are KNOWN whatever their level |

FOCUS is what deserves your attention now and STRETCH what to keep for later;
together, in that order, they are the study list, and REVIEW and KNOWN are
there to look up, not to study.

The learner's level is declared, never inferred, and it is never a label the
model learns from: it is the reference point against which a word's general
difficulty becomes this learner's priority.

## What it does well

- It is fast without the dictionary: analyzing the whole 8,530-word sample
  story again in the same process takes the pipeline a median 0.29 s on the
  laptop [`performance.md`](performance.md) describes, and one whole command takes several
  seconds, most of them spent loading its libraries.
- It says where every level came from, so a learner can tell a judgment made
  by people from an estimate.
- It runs entirely on one machine, and every benchmark in the documentation
  can be rerun with the commands [`performance.md`](performance.md) gives.
- It keeps someone else's text only as long as its owner allows, and never
  learns from it.

## What it does poorly

These are limits of the design, not defects waiting for a fix.

**Words, not expressions.**

It has no notion of a multi-word expression: `look after`, `in spite of`, and
every idiom arrive as separate words, and a phrasal verb loses the meaning
that makes it worth learning.

**One level per word, not per meaning.**

A lemma in one part of speech gets one level whatever it means in the text:
CEFR-J lists the noun `dive` at B1 without naming a meaning, Octanove lists it
at C2 as a run-down bar, and the report shows B1 for either.

**One level for the learner.**

A single CEFR label for a whole person is coarse: a learner who reads well but
writes poorly, or knows one field's vocabulary deeply, is still one letter and
one digit here.

**Senses are listed, never chosen.**

The report lists up to three WordNet senses of a word, most used first, and
leaves the choice to the reader, so the sense a text means can be missing.

In the story Watson tosses a smoke-rocket into the room at Holmes's signal,
and the closest WordNet sense, a signal rocket, comes fourth, after a vehicle,
an engine, and a salad plant, so none of the three the report lists fits.

The first sense of *landau* is the physicist Lev Landau, so that is the gloss
the report shows for the carriage.

**The tagger makes mistakes, and they travel.**

The tagger reads *Holmes* as a plural common noun in 6 of the story's 48
mentions, so a report on the sample story lists *holmes* as a word to learn,
glossed with WordNet's entry for the fictitious detective.

Five of those mentions open a sentence; the sixth ends this line, which the
sentence splitter cuts at the question mark:

> "What do you make of that?" asked Holmes.

Read whole, the line gives *Holmes* as a proper noun; cut, the tagger sees only
*asked Holmes* and reads it as a plural.

**Archaic English is read as rare English.**

The tool knows no archaic forms: *thou*, *thee*, and *spake* are rare words to it,
so an older text fills a study list with grammar no learner needs.

**Rare is not the same as hard.**

A B2 learner's list for the sample story holds *landau* and *brougham*, which are
rare, but each is only a kind of carriage a reader can safely skip: the report
says how rare a word is, not how much it matters.

**The estimates are estimates.**

For a word outside the lists, the model is wrong by 0.73 of a level on average
on the words it could be tested on, it almost never answers C2, and the words
it is used for are exactly the ones it could not be tested on; [`domain.md`](domain.md) has
the numbers.

**The dictionary is external and borrowed.**

The Free Dictionary API is a free service outside Wordwinnow, built and run by
its developer, `meetDeveloper`, at <https://dictionaryapi.dev>: an answer can
take many seconds, the service can be unavailable, an analysis finishes
without it, and its definitions come from Wiktionary, under Wiktionary's
terms; WordNet can stand in for it, locally.

Each word Wordwinnow looks up there is one request, so the words of a text
leave the machine, though the text itself never does, and in the distributed
mode each answer stays in the dictionary cache under its word for up to thirty
days; with `--no-dictionary`, no word of a text leaves the machine.

## Where texts come from

A file or standard input needs nothing.

The New York Times feeds need nothing either; the New York Times APIs need one
developer key, which enables the top stories, the most popular stories,
article search, and the archive as sources.

[`operating.md`](operating.md) says how to get a key and what The New York Times allows.

Every source yields the same kind of document, and the analysis itself sees
only its text; the document also carries a label, your own text or The New
York Times, which the report and the statistics show.

A text that belongs to someone else says so: it is shown with the attribution
its owner asks for and a link to read each piece in full, and nobody can read
an analysis of it after a day.

An export of such an analysis leaves out the text and its sentences and keeps
the learner's words, levels, and tiers; the JSON also keeps the attribution
and each piece's headline and link, so the credit travels with the words.

## Non-goals

- Accounts, authentication, or any notion of a user beyond one request.
- Spaced repetition, review scheduling, or progress tracking.
- Translation into another language.
- Multi-word expressions and word-sense disambiguation.
- A graphical or web interface; one would be another client of the intake
  service's HTTP API, as the command line's remote mode is.
