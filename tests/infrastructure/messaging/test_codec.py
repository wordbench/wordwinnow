"""
Domain events become versioned messages and come back unchanged, and the
committed golden files prove the wire shape has not drifted.
"""

from json import (
    dumps,
    loads,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)
from uuid import (
    UUID,
)

from pytest import (
    mark,
    raises,
)

from tests.infrastructure.messaging.analyses import (
    EPOCH,
    completed_analysis,
    requested_analysis,
)
from wordwinnow.application.dto import (
    facts_of,
)
from wordwinnow.domain.analysis import (
    AnalysisRequested,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.messaging.codec import (
    decode,
    encode,
    facts_from,
    message_for,
)
from wordwinnow.infrastructure.messaging.messages import (
    AnalysisCompletedV1,
    AnalysisRequestedV1,
    Message,
)
from wordwinnow.infrastructure.messaging.topics import (
    ANALYSIS_COMPLETED_V1,
    ANALYSIS_REQUESTED_V1,
)

_GOLDEN: Final = (
    Path(
        __file__,
    ).parents[2]
    / "fixtures"
    / "messaging"
)


@final
class TestMessageFor:
    def test_a_request_becomes_a_requested_message_on_its_topic(
        self,
    ) -> None:
        analysis = requested_analysis()

        (event,) = analysis.pull_events()

        (
            topic,
            message,
        ) = message_for(
            analysis=analysis,
            event=event,
        )

        assert topic == ANALYSIS_REQUESTED_V1

        assert isinstance(
            message,
            AnalysisRequestedV1,
        )

        assert message.schema_version == 1

        assert message.analysis_id == analysis.id.value

        assert message.occurred_at == EPOCH

    def test_a_completion_carries_the_facts_of_the_analysis(
        self,
    ) -> None:
        analysis = completed_analysis()

        (event,) = analysis.pull_events()

        (
            topic,
            message,
        ) = message_for(
            analysis=analysis,
            event=event,
        )

        assert topic == ANALYSIS_COMPLETED_V1

        assert isinstance(
            message,
            AnalysisCompletedV1,
        )

        assert message.occurred_at == analysis.finished_at

        assert message.origin is DocumentOrigin.CUSTOM_TEXT

        assert message.learner_level is CefrLevel.B1

        assert tuple(fact.lemma for fact in message.facts) == (
            "groom",
            "drunken",
        )

        assert facts_from(
            message=message,
        ) == facts_of(
            analysis=analysis,
        )

    def test_an_event_of_another_analysis_is_refused(
        self,
    ) -> None:
        analysis = requested_analysis()

        with raises(
            expected_exception=ValueError,
            match="cannot make a message about analysis",
        ):
            message_for(
                analysis=analysis,
                event=AnalysisRequested(
                    analysis_id=AnalysisId.new(),
                    occurred_at=EPOCH,
                ),
            )

    def test_every_message_survives_a_round_trip(
        self,
    ) -> None:
        for analysis in (
            requested_analysis(),
            completed_analysis(),
        ):
            (event,) = analysis.pull_events()

            (
                _topic,
                message,
            ) = message_for(
                analysis=analysis,
                event=event,
            )

            assert (
                decode(
                    data=encode(
                        message=message,
                    ),
                    model=type(
                        message,
                    ),
                )
                == message
            )


@final
class TestGoldenFiles:
    @mark.parametrize(
        argnames=(
            "file_name",
            "model",
        ),
        argvalues=(
            (
                "analysis_requested_v1.json",
                AnalysisRequestedV1,
            ),
            (
                "analysis_completed_v1.json",
                AnalysisCompletedV1,
            ),
        ),
    )
    def test_a_golden_file_decodes_and_re_encodes_to_the_same_document(
        self,
        *,
        file_name: str,
        model: type[Message],
    ) -> None:
        golden = (_GOLDEN / file_name).read_bytes()

        message = decode(
            data=golden,
            model=model,
        )

        assert message.schema_version == 1

        assert message.analysis_id == UUID(
            int=2,
        )

        assert loads(
            s=encode(
                message=message,
            ),
        ) == loads(
            s=golden,
        )

    def test_the_completed_golden_file_yields_facts(
        self,
    ) -> None:
        message = decode(
            data=(_GOLDEN / "analysis_completed_v1.json").read_bytes(),
            model=AnalysisCompletedV1,
        )

        facts = facts_from(
            message=message,
        )

        assert len(
            facts,
        ) == len(
            message.facts,
        )

        assert {fact.analysis_id.value for fact in facts} == {
            message.analysis_id,
        }

    def test_another_schema_version_is_refused(
        self,
    ) -> None:
        golden = loads(
            s=(_GOLDEN / "analysis_requested_v1.json").read_bytes(),
        )

        golden["schema_version"] = 2

        with raises(
            expected_exception=ValueError,
            match="schema_version",
        ):
            decode(
                data=dumps(
                    obj=golden,
                ).encode(
                    encoding="utf-8",
                ),
                model=AnalysisRequestedV1,
            )
