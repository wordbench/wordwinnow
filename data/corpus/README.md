# The sample story

One complete story: "A Scandal in Bohemia," the first Sherlock Holmes short
story, in which the King of Bohemia hires Holmes to recover a photograph and
Irene Adler outwits them both.

## What English is to me

When I meet a word I do not know, the question that interests me is not only
what it means in the sentence in front of me, but how far the word reaches.

*Coin*, met as a piece of money, is a small example: looking it up, I found that
it is also a verb, that you can *coin* a word or a phrase, and that discovery
was worth something in itself, because the word turned out to be larger than
the sentence that brought it to me.

That is what English has become for me: not a list of words, each with one
translation, but a territory, in which a word has several meanings and more
than one grammatical role, keeps particular company, lives in idioms and
proverbs, and is used in ways a native speaker would recognize at once.

It did not start that way.

At school, English was a compulsory subject, taught as an academic discipline
with close attention to grammar, and I was perfectly fine with that side of
it: it was the starting point.

It also handed me some words in an artificially narrow form: a *notebook* was
something you jot notes down in, and an *artist* was a painter, while in English
a notebook can just as well be a laptop, and an artist can be a musician
releasing an album.

Finding the rest of those words taught me something larger than any one of
them: school had given me one relationship with English, and curiosity could
make it far larger.

So I took up English on my own, this time by choice rather than requirement,
with a deliberately ambitious goal: to understand quickly, to speak quickly,
and to feel at home wherever English is spoken, the way someone who grew up
with the language would.

English is a world language rather than one country's, so that means being
comfortable in any English-speaking setting: a conversation, a talk, a series
or a film, a story, or my own writing.

I have a soft spot for American English, and it is the variety I lean toward,
but that is a preference within the goal, not the goal itself.

The bar is high on purpose: as I approach it, it moves further away, in a
productive sense, which is why learning English is a lifelong journey for me
rather than a course to finish.

Curiosity of this kind becomes a habit of attention: to precision and nuance,
to natural phrasing, to which words go together and which merely could, to the
difference between what a word means here and everything it can mean, and to
how a native speaker would actually say something.

People close to me sometimes call me a snob when it comes to English, and I
take it as a compliment.

## Why I built Wordwinnow

I believe in immersion, in surrounding myself with the language every day, so
learning and pleasure came together naturally, and *Loki*, the Marvel series,
was the first series I binge-watched entirely in English, which I still
remember warmly.

Curiosity has a cost, though.

A series or an article worth enjoying is full of words worth investigating,
and following each one where it leads means pausing the episode, which costs
some of the pleasure of the very thing that brought the word to me.

While I was catching up on vocabulary, I paid that cost deliberately: I kept
the genuinely new words and idioms in Quizlet sets I built myself, typing each
word, looking it up in dictionaries, and finding the best fit, and I learned
them between sets at the gym; it worked very well for memorization, and it was
expensive in time and attention.

Wordwinnow comes from that tension, and its real subject is attention.

It does the looking-up and the sorting before I read or watch: it finds the
words of a text, estimates how hard each one is, places each against my level
and my goal, and lists their meanings, so that I can decide in advance which
words deserve deliberate study and then enjoy the text without stopping.

It lists several meanings of a word rather than choosing the one the text
happens to use, which suits the way I learned: the meaning met in one sentence
is only part of the word.

It does not do the learning: it points, and you decide which of those words to
study, and the learning stays yours.

I am at roughly C1+ now and need that old routine less: I can usually watch a
series without stopping, and hearing a word in context, with a look at a
favorite dictionary now and then, is often enough; but I remember the stage
when every episode was full of words worth knowing, and Wordwinnow is built
for a learner at that stage.

## Why this story

A series cannot live in a repository, and neither can its subtitles, so the
sample had to be a real English text that anyone may copy.

I wanted it to keep what *Loki* meant to me, and "A Scandal in Bohemia" does:

- It is genuine English, written by a native speaker for native readers, and
  it ranges from Watson's narration and Holmes's precision to a king's
  formality and a letter.
- Many learners already know Sherlock Holmes from the screen, so their
  attention can go to the words rather than the plot.
- It is something people read for pleasure, not for a course.
- It has two screen adaptations in English to enjoy after reading it:
  Granada's faithful one, which opened its series in 1984, and BBC *Sherlock*'s
  "A Scandal in Belgravia" (2012).
- It is in the public domain, so it can legally live in this repository.

I chose it for the reader, not for whatever the tool happens to measure best.

## What the text is for

I use this one story everywhere, so a reader meets the same King, photograph,
and groom in the documentation, in the reports, and in the tests.

| Job | Where |
| --- | --- |
| The quickest demonstration | `make demo`, the quick starts, the notebook's final experiment |
| Timing the pipeline's stages | `wordwinnow bench stages`, at a quarter, a half, and the whole of the story |
| Timing the dictionary stage and the distributed mode | `wordwinnow bench enrichment` and `wordwinnow bench throughput`, on the whole story |

Nothing is fitted on it: the level model learns its labels from the reference
lists in `data/reference/` alone, the frequency heuristic's boundaries, fitted
once to the same lists, are fixed, and the word frequencies come from
`wordfreq`.

The tests tell the same story in their own words, a photograph, a mask, and a
groom, with `egria`, the place in Bohemia Holmes looks up in his gazetteer, as
the word no resource knows; they read none of these files.

## What the tool makes of it

`wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --local --level B1
--no-dictionary` finds 1,509 vocabulary items in the story's 8,530 words; with
the level model `make model` trains, it puts 832 of them on a B1 learner's study
list, the FOCUS and STRETCH words, and with the frequency heuristic alone,
798.

The list is vocabulary a learner can use, such as *rush*, *gentleman*, *remark*,
*observe*, *client*, *mask*, *clergyman*, *precisely*, *glance*, *glimpse*, and *deduce*.

It also holds *landau* and *brougham*, two kinds of carriage the model places at
C1, which are rare and safely skipped: the tool measures how rare a word is,
and whether it is worth learning is still the learner's call.

The plainest mistake is *holmes* on the list, where the tagger reads the
detective's name as a common noun; [`docs/product.md`](../../docs/product.md) explains why.

## Where it comes from

| Field | Value |
| --- | --- |
| File | `a-scandal-in-bohemia.txt`, the complete story |
| Title | "A Scandal in Bohemia" |
| Author | Arthur Conan Doyle, 1859–1930 |
| First published | *The Strand Magazine*, July 1891; collected in *The Adventures of Sherlock Holmes* (George Newnes, 1892) |
| Kind | An original English short story, written for adult readers of a magazine |
| Status of the work | Public domain in the United States, as published in 1891, and wherever copyright lasts for the author's life and seventy years, since 2001 |
| Edition | Standard Ebooks' *The Adventures of Sherlock Holmes*, whose editors dedicate their own work to the worldwide public domain under CC0 1.0 |
| Source | <https://standardebooks.org/ebooks/arthur-conan-doyle/the-adventures-of-sherlock-holmes> |
| Taken from | [`src/epub/text/a-scandal-in-bohemia.xhtml`](https://github.com/standardebooks/arthur-conan-doyle_the-adventures-of-sherlock-holmes/blob/48ec456ea50987a81f564821457b9fb32cc2bfc6/src/epub/text/a-scandal-in-bohemia.xhtml) of <https://github.com/standardebooks/arthur-conan-doyle_the-adventures-of-sherlock-holmes>, commit `48ec456ea50987a81f564821457b9fb32cc2bfc6` |
| Taken at | `2026-10-01T00:00:00Z`, midnight UTC at the start of October 1, 2026 |
| Source file SHA-256 | `f43eb48c0e360f113ea47b9b9154f0a47d5946c38e5b3b82f0fa0bc665c182a1` |

The text was converted from the edition's XHTML with no change to its words:

- The title comes first, then each paragraph on a line of its own, separated
  by a blank line, Irene Adler's letter included.
- The section numerals I, II, and III are left out, since the tool would read
  each as the pronoun.
- The markup is removed, and so are the invisible word joiners the edition
  puts before a dash.

The spelling is the edition's, which keeps the original's British spelling.

`wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --local --level B1
--no-dictionary` is the quickest way to see the tool work.
