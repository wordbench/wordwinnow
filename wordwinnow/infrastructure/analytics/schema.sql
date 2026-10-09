-- NOTE:
-- A ReplacingMergeTree keyed by analysis, lemma, and part of speech keeps one row per fact however many times the
-- completed message that carried it is delivered; readers ask for FINAL so they see one row before the parts merge.
CREATE TABLE IF NOT EXISTS {database:Identifier}.vocabulary_facts
(
    analysis_id UUID,
    completed_at DateTime64(3, 'UTC'),
    origin LowCardinality(String),
    learner_level LowCardinality(String),
    lemma String,
    part_of_speech LowCardinality(String),
    level LowCardinality(String),
    level_source LowCardinality(String),
    tier LowCardinality(String),
    occurrence_count UInt32,
    lookup_outcome Nullable(String)
)
ENGINE = ReplacingMergeTree
ORDER BY (analysis_id, lemma, part_of_speech)
