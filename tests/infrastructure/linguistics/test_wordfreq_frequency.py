"""
How common a word is, from wordfreq.
"""

from typing import (
    final,
)

from wordwinnow.infrastructure.linguistics.wordfreq_frequency import (
    WordfreqFrequency,
)


@final
class TestWordfreqFrequency:
    def test_common_words_are_more_frequent_than_rare_ones(
        self,
    ) -> None:
        frequency = WordfreqFrequency()

        assert frequency.zipf(
            lemma="king",
        ) > frequency.zipf(
            lemma="brougham",
        )

        assert (
            frequency.zipf(
                lemma="egria",
            )
            == 0.0
        )
