# Domain

One domain model, vocabulary analysis, expressed in the learner's language.

## The vocabulary

| Term | Module | What it is |
| --- | --- | --- |
| `Document`, `DocumentOrigin`, `Reference` | `document.py` | The text, its title and reference, a label for the kind of source it came from, and, for someone else's text, its attribution, its links, and how long it may be kept |
| `Token`, `Sentence`, `PartOfSpeech` | `language.py` | The tagged, lemmatized units of the text, tagset-agnostic |
| `VocabularyItem` | `vocabulary.py` | A lemma in one part of speech, with its count and a sentence |
| `CefrLevel`, `LevelAssessment`, `LevelSource` | `cefr.py` | A level, and the source that assigned it |
| `LexicalFeatures`, `FeatureFamily` | `difficulty.py` | What a level model may see about a word, by family |
| `LexicalSense` | `senses.py` | One sense with its relations, usage, generality, and category |
| `DictionaryLookup`, `SelectedDefinition` | `dictionary.py` | A lookup's three outcomes, and the definitions a learner is shown |
| `LearnerProfile` | `learner.py` | Level, target level, known lemmas |
| `StudyItem`, `StudyTier`, `Gloss` | `study.py` | A word placed for one learner, and the meaning it is shown with |
| `Analysis` | `analysis.py` | The aggregate: a document, a profile, a lifecycle, a result |

Every value object is a frozen, keyword-only dataclass that validates its
invariants on construction.

## The invariants

**An analysis is the one aggregate.**

It is requested, then processing, then completed; it can fail, with a reason,
while requested or processing, and only a failure is reversible, by reopening
it as requested, because a completed analysis is a result.

It is constructed only inside its own module, and a fitness test proves it.

It records two domain events, requested and completed, because a worker reacts
to the first and the aggregation side to the second; a failure is state,
because nothing reacts to one.

**A level is a place on a scale; everything else is a label.**

`CefrLevel` orders by its position from A1 to C2, so `B1 < B2` means what it says.

Every other string enumeration in the project is an `UnorderedStrEnum`: its
members compare equal to their stored values and refuse `<`, so no code can
order study tiers, statuses, or parts of speech by accident.

**A dictionary lookup has three outcomes, and only two are facts about the word.**

Found and not found are cached; unavailable is never remembered as "no
definition."

**The learner's level is a reference point, never a label.**

A learner profile's target level is at or above its level, and its known
lemmas are lowercase, the form every comparison with a text's lemmas uses.

**Someone else's text is kept only as long as its owner allows.**

An analysis of a document with a retention expires when that time has passed,
and the purge deletes it; the learner's own text never expires.

## How a word gets its level

Three sources, in order of precedence, and every level in a report names which
one answered.

1. **A reference list.**
   [The CEFR-J Vocabulary Profile](../data/reference/ATTRIBUTION.md#cefr-j-vocabulary-profile-version-15) for A1 to B2 and [the Octanove profile](../data/reference/ATTRIBUTION.md#octanove-vocabulary-profile-c1c2-version-10) for
   C1 and C2 are external judgments made by people: 9,520 pairs of a lemma and
   a part of speech, read from the two files' 9,935 rows.
2. **The level model.**
   For a word outside the lists, when the model file exists.
3. **The frequency heuristic.**
   Five Zipf-frequency boundaries: the more common the word in general
   English, the lower the level.

The list reader keeps the 9,614 rows in the four open word classes and leaves
out 321: 319 in the function-word classes and two whose word class is missing
or misspelled.

Of the rows it keeps, 131 hold only multi-word headwords and give no entry,
and each other slash-separated spelling of a headword gets an entry of its
own, 201 more, for 9,684 entries; where a lemma in a part of speech is listed
more than once, the lowest level wins, which removes 164.

### The frequency heuristic

The heuristic reads one number, the lemma's Zipf frequency in [wordfreq](https://github.com/rspeer/wordfreq): the
base-ten logarithm of how often the word occurs in a billion words of general
English, so 3 means once in a million words.

It cuts that scale at five boundaries: a word at or above 5.10 is A1, at or
above 4.65 A2, 4.00 B1, 3.30 B2, and 2.75 C1, and anything rarer is C2.

It sees nothing else, not the part of speech, the sense, the sentence, or the
learner, and a word wordfreq does not know has a Zipf frequency of 0, so it is
C2.

The boundaries were not set by hand: they were fitted once to the reference
lists, by a search that moved one boundary at a time in steps of 0.05 and kept
a move only when it raised the macro-F1 on the experiment's 7,616 training
entries, never its held-out ones, and they have been fixed since.

The search started from the round-numbered boundaries of the academic
prototype Wordwinnow continues, 6.0, 5.2, 4.5, 3.8, and 3.0, whose origin is
not recorded, and it reaches the same five boundaries from four other starting
points.

They sit close to the cuts that would give each level its share of the
training entries, 5.01, 4.55, 3.98, 3.21, and 2.71, so the heuristic amounts
to ranking words by how common they are and dealing out the levels in the
lists' own proportions.

Macro-F1 weighs the six levels equally, which keeps C2 in use: refitted for
the mean level error instead, as the experiment's tuned heuristic is, the
boundaries become 5.5, 4.8, 3.9, 3.15, and 1.5, which lowers that error from
0.843 to 0.811 on the held-out entries but finds 0.116 of their C2 words
instead of 0.455.

The heuristic answers only when no level model is installed, and then for
every word the lists do not cover; with the model, it answers for none.

Read a level from it as how rare the word is, placed on the lists' scale,
rather than a judgment of how hard it is: it is within one level of the lists
on 0.809 of the held-out entries, and the words it is used for, those outside
the lists, are the ones nothing here could measure it on.

### The level model

The model predicts a word's CEFR level, treated as an ordinal quantity: one of
six levels for a lemma in one part of speech.

The chosen model regresses the level's rank and rounds it to the nearest
level, which uses the order of the levels and treats them as evenly spaced.

The primary measure is the mean absolute level error, because a word one level
off usually stays in its tier, while a word three levels off changes tier for
every learner from A2 to B2; PR and ROC areas are not reported, because
neither the heuristic nor the chosen regressor produces class scores.

The model sees three feature families, each switchable by name, so an
experiment can say what each adds and the model file records which ones it
expects.

| Family | Features | Why it is defensible |
| --- | --- | --- |
| frequency | Zipf frequency in wordfreq's English corpus | Frequency is the best single predictor of acquisition order, and the heuristic is nothing but this feature |
| lexical | Character count; part of speech | Length is the classic readability feature; the lists label parts of speech differently |
| wordnet | Sense count; SemCor usage count; hyponym count of the most used sense; its semantic category | Polysemy and corpus usage track how established a word is, generality tracks how basic it is, and a category such as `noun.food` is a coarse proxy for concreteness |

Held out: 1,904 of the 9,520 list entries, a fifth, stratified by level with
seed 42; every method in the table is scored on them.

| Method | Mean level error | Within one level | Macro-F1 | Spearman |
| --- | --- | --- | --- | --- |
| Majority class (B2) | 1.157 | 0.655 | 0.076 | 0.000 |
| Default heuristic, five Zipf boundaries | 0.843 | 0.809 | 0.376 | 0.652 |
| Tuned heuristic, boundaries refitted for level error | 0.811 | 0.846 | 0.320 | 0.641 |
| Ordinal gradient boosting on all three families | 0.732 | 0.888 | 0.348 | 0.697 |

The WordNet family is what buys the difference, and treating the scale as
ordinal matters as much: in five-fold cross-validation on the other 7,616
entries, with all three families, a gradient-boosting classifier that treats
the levels as unrelated classes is off by 0.832 of a level on average, and the
ordinal regressor by 0.753.

The gain comes with a pull toward the middle of the scale: of the held-out
words, 201 are A1 and 189 are C2, and the model answers A1 for 61 words and C2
for 21, where the default heuristic answers C2 for 197.

It recalls 0.074 of those 189 C2 words, 14 of them, against the default
heuristic's 0.455, which is why its macro-F1 is the lower of the two.

On the sample story, the model levels the 402 of its 1,509 vocabulary items
that the lists do not cover and gives C2 to 2 of them, where the heuristic
would give it to 84.

A model trained on the heuristic's own labels agrees with it almost perfectly,
which says nothing about CEFR; that fidelity is reported only so the
distinction stays visible.

The notebook [`notebooks/cefr_level_classification.ipynb`](../notebooks/cefr_level_classification.ipynb) is the executed
experiment; `wordwinnow ml experiment` reruns the experiment and prints its
results, writing `results.json` and the figures only when given `--output-dir`,
and `wordwinnow ml train` writes the model file the pipeline loads.

The notebook is rerun from the top in one session, with no cell recording when
it ran, as the standard asks of a notebook kept with its outputs, once
`make model` has trained the model its last code cell loads:

```sh
uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.record_timing=False notebooks/cefr_level_classification.ipynb
```

### What the result supports

The reference is external: the CEFR levels that people assigned in the two
lists, against which every method in the table is scored.

The baselines are comparison points, not ground truth: the majority class is
what a model that has learned nothing scores, and the two heuristics are what
frequency alone achieves.

No baseline saw the held-out entries: the majority class is the commonest
level of the training entries, and both heuristics' boundaries were fitted on
those entries alone, the default ones once for macro-F1 and the tuned ones for
the level error in every run of the experiment.

The model was selected by five-fold stratified cross-validation on the 7,616
training entries, as the candidate with the lowest cross-validated level
error, and no margin over the baselines was fixed before the experiment.

The 1,904 held-out entries took no part in that selection, and on them the
model's mean absolute level error is 0.732, against 0.811 for the best
baseline, the tuned heuristic, and its share within one level is 0.888, the
highest in the table.

The model file the product loads is the same configuration refitted on all
9,520 entries, so the held-out numbers describe the configuration, not that
file.

The result supports one claim: on held-out words that the lists cover, the
model's levels come closer to the levels people assigned than either frequency
heuristic does, which is the basis, a modest one, for preferring the model to
the heuristic for the words outside the lists.

It does not establish that the model is right about the words outside the
lists, which no labels cover, that it is equally strong at every CEFR
boundary, since it recalls fewer C2 words than the default heuristic, or that
its gain is a universal or absolute threshold for predicting CEFR levels; the
next section lists what else the evidence leaves open.

### What the evidence does not cover

- The lists describe receptive vocabulary from textbooks, not what any one
  learner knows.
- One label per lemma and part of speech ignores senses, as [`product.md`](product.md) shows
  with the noun *dive*.
- The words the model is used for are exactly the ones outside the lists,
  which the evaluation cannot see.
- One split, one seed, fixed hyperparameters: estimates, not a leaderboard.

## The learner

A `LearnerProfile` is a runtime policy handed to one analysis, not an account: a
`level`, a `target_level`, and `known_lemmas`, whose effect on the tiers
[`product.md`](product.md) sets out.

Within a tier, a word that occurs more often in the text comes first, and ties
go to the word that is more common in English generally.

C2 is the top of the scale and the default target of a C1 or C2 learner, so
for them nothing is STRETCH and the report is FOCUS above a long REVIEW: the
tiers say least where the scale runs out.

Nothing else about a learner, such as their interests or native language,
changes an analysis.

## What the domain does not model

Users, sessions, progress, schedules, translations, the choice of a sense in
context, and the providers behind a document: a document says what it is and
what it needs, and labels only the kind of source it came from, your own text
or The New York Times, never how it was fetched.
