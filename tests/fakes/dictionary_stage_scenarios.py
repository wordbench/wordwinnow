"""
Seven scripts for the dictionary stage's recorder, each of one analysis's
dictionary stage against a provider that misbehaves in its own way.

The analysis is the sample story's, 116 words to look up, 17 of them already
in the cache and 6 unknown to the provider.

A frame of the operator view is drawn from a recording of one of these.
"""

from typing import (
    Final,
)

from tests.fakes.dictionary_stage import (
    Answering,
    Chapter,
    Down,
    Flaky,
    Hanging,
    Refusing,
    Script,
    Settings,
    Silent,
    Window,
    Word,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)

# NOTE:
# The lemmas to look up and their parts of speech, in the order the stage asks them.
_WORDS: Final = """
emotion noun
clear verb
extraordinary adjective
keen adjective
rise verb
abandon verb
absorb verb
accomplish verb
admirable adjective
akin noun
alternate verb
associate verb
attract verb
balance verb
blind noun
brilliantly adverb
chamber noun
cigar noun
civil adjective
cocaine noun
companion noun
crack noun
delicate noun
delicately adverb
desire noun
distracting noun
disturbing adjective
doings noun
drift verb
drug-created adjective
dubious adjective
eagerly adverb
eclipse verb
employ verb
energy noun
establishment noun
excellent noun
factor noun
faculty noun
fierce adjective
finely adverb
former adjective
formerly adverb
gasogene noun
high-power noun
home-centred adjective
hopeless noun
incident noun
intrusion noun
kindly adverb
lately adverb
lead verb
light verb
lodging noun
marriage noun
master noun
mental adjective
mention verb
merely adverb
mission noun
motif noun
observation noun
observe verb
observer noun
occupy verb
particularly adverb
pas noun
passion noun
place verb
pound verb
precise noun
press noun
questionable adjective
reasoner noun
reasoning noun
reign verb
remark verb
scent noun
seize verb
seldom verb
seldom adverb
sensitive adjective
sex noun
sneer noun
soul noun
spare adjective
spirit noun
sufficient adjective
summons noun
sunk noun
swiftly adverb
tall noun
tragedy noun
trained adjective
twentieth noun
twice noun
vague adjective
veil noun
wave verb
well-remembered adjective
whole noun
wooing noun
abhorrent adjective
admirably adverb
bohemian adjective
clasp verb
drowsiness noun
effusive adjective
gibe noun
immense adjective
introspective adjective
loathe verb
pace verb
predominate verb
silhouette noun
temperament noun
"""

_NOT_FOUND: Final = frozenset(
    (
        "drug-created",
        "gasogene",
        "high-power",
        "home-centred",
        "reasoner",
        "well-remembered",
    ),
)

_CACHED: Final = frozenset(
    (
        "alternate",
        "attract",
        "brilliantly",
        "crack",
        "delicately",
        "disturbing",
        "gasogene",
        "lately",
        "mental",
        "motif",
        "occupy",
        "pas",
        "passion",
        "remark",
        "summons",
        "twice",
        "vague",
    ),
)


def _words() -> tuple[Word, ...]:
    pairs = tuple(line.split() for line in _WORDS.splitlines() if line)

    return tuple(
        Word(
            lemma=lemma,
            part_of_speech=PartOfSpeech(
                value=part,
            ),
            found=lemma not in _NOT_FOUND,
            cached=lemma in _CACHED,
        )
        for (
            lemma,
            part,
        ) in pairs
    )


# NOTE:
# Every mechanism in turn.
EVERY_MECHANISM_IN_TURN: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=14.0,
            behavior=Flaky(),
        ),
        Chapter(
            at=24.0,
            behavior=Down(),
        ),
        Chapter(
            at=40.0,
            behavior=Answering(),
        ),
        Chapter(
            at=52.0,
            behavior=Refusing(
                retry_after_seconds=6,
            ),
        ),
        Chapter(
            at=58.0,
            behavior=Answering(),
        ),
        Chapter(
            at=72.0,
            behavior=Hanging(
                retry_after_seconds=None,
            ),
        ),
        Chapter(
            at=100.0,
            behavior=Answering(),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=170.0,
        seed=11,
    ),
)


# NOTE:
# A healthy run against the provider's own limit.
HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Window(
                answers=20,
                window_seconds=20.0,
            ),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=200.0,
        seed=11,
    ),
)


# NOTE:
# A provider that answers, slowly.
PROVIDER_THAT_ANSWERS_SLOWLY: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=8.0,
            behavior=Answering(
                least_seconds=4.0,
                spread_seconds=6.0,
            ),
        ),
        Chapter(
            at=50.0,
            behavior=Answering(),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=200.0,
        seed=11,
    ),
)


# NOTE:
# An outage longer than the analysis waits.
OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=10.0,
            behavior=Down(),
        ),
        Chapter(
            at=400.0,
            behavior=Answering(),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=60.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=140.0,
        seed=11,
    ),
)


# NOTE:
# Twenty-four lookups at once, as three workers give one enrichment service.
TWENTY_FOUR_LOOKUPS_AT_ONCE: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(
                least_seconds=1.5,
                spread_seconds=2.0,
            ),
        ),
        Chapter(
            at=20.0,
            behavior=Refusing(
                retry_after_seconds=8,
            ),
        ),
        Chapter(
            at=21.0,
            behavior=Answering(
                least_seconds=1.5,
                spread_seconds=2.0,
            ),
        ),
        Chapter(
            at=40.0,
            behavior=Down(),
        ),
        Chapter(
            at=58.0,
            behavior=Answering(
                least_seconds=1.5,
                spread_seconds=2.0,
            ),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=24,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=200.0,
        seed=11,
    ),
)


# NOTE:
# Long pauses, and requests that never come back.
LONG_PAUSES_AND_REQUESTS_THAT_NEVER_COME_BACK: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=8.0,
            behavior=Refusing(
                retry_after_seconds=25,
            ),
        ),
        Chapter(
            at=9.0,
            behavior=Answering(),
        ),
        Chapter(
            at=50.0,
            behavior=Refusing(
                retry_after_seconds=45,
            ),
        ),
        Chapter(
            at=51.0,
            behavior=Answering(),
        ),
        Chapter(
            at=110.0,
            behavior=Silent(),
        ),
        Chapter(
            at=175.0,
            behavior=Answering(),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=300.0,
        seed=11,
    ),
)


# NOTE:
# The provider's outage as the operating guide describes it: every request held 20 s and then failed with 522, and the
# provider asking for 120 s.
OUTAGE_AS_THE_OPERATING_GUIDE_DESCRIBES_IT: Final = Script(
    chapters=(
        Chapter(
            at=0.0,
            behavior=Answering(),
        ),
        Chapter(
            at=10.0,
            behavior=Hanging(
                retry_after_seconds=120,
            ),
        ),
        Chapter(
            at=140.0,
            behavior=Answering(),
        ),
    ),
    words=_words(),
    settings=Settings(
        concurrency=8,
        patience_seconds=180.0,
        timeout_seconds=30.0,
        starts_seconds=0.5,
        after_seconds=3.0,
        limit_seconds=320.0,
        seed=11,
    ),
)
