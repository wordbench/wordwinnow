# Performance

What an analysis costs, what grows with the text, what the dictionary costs,
and what more workers buy.

Every measured number comes from a row in [`benchmarks/results/`](../benchmarks/results/), which
[`benchmarks/figures/`](../benchmarks/figures/) also draws; a number worked out rather than measured
says so, and so does the one observation below that no benchmark recorded.

The measurements were taken on one Apple Silicon laptop, with the stack under
Docker Desktop, so they describe that machine, not a deployment.

Two things are newer than the measurements: how a worker stops and hands over
a partition, which [`architecture.md`](architecture.md) describes, and the versions of the
services the stack runs; the throughput numbers, the only ones that involve
Kafka, were measured before both and not repeated.

## In short

| Question | Answer | Kind |
| --- | --- | --- |
| How long does the whole 8,530-word sample story take without the dictionary, analyzed again in the same process? | 0.29 s, or 0.31 s with the level model | Measured |
| What grows with the text? | Tagging grows with the words; WordNet grows with the distinct words, which grow more slowly | Measured |
| What does the dictionary cost? | One network round trip per distinct word: 1,401 for the story, 19 s with eight in flight against a provider that answers in 0.1 s | Measured |
| And against the Free Dictionary API? | About 700 s at the least for the story's first analysis, because its budget is two requests a second | Worked out |
| And a second time? | About 0.015 s: a warm cache answers every lookup | Measured |
| What do more workers buy? | 116, 180, and 274 analyses per minute with one, two, and four workers | Measured |

## The pipeline, and what grows with the text

**Parameter.**

The text's length, and whether a level model is installed.

**Experiment.**

`wordwinnow bench stages` runs the pipeline without the dictionary over the
sample story, cut after whole paragraphs to its first quarter, its first half,
and the whole, five times each, and records every stage.

One story cut three ways keeps the vocabulary and the style fixed, so length
is the only thing that changes, and the median of five runs leaves out the
slower first run of each cut.

**Measurement.**

Median seconds per stage, without a model:

| Share of the story | Words | Items | Linguistic analysis | Lexical semantics | Level assessment | Total |
| --- | --- | --- | --- | --- | --- | --- |
| A quarter | 2,162 | 608 | 0.036 | 0.067 | 0.001 | 0.106 |
| A half | 4,320 | 987 | 0.075 | 0.098 | 0.001 | 0.178 |
| The whole | 8,530 | 1,509 | 0.146 | 0.137 | 0.002 | 0.291 |

With the level model installed, level assessment costs 0.008 to 0.010 s at
every length, and the whole story 0.308 s in total.

**Interpretation.**

Tagging doubles as the words double, while the vocabulary items grow more
slowly, because a story keeps using the words it has already used; WordNet
grows with the items, so it is the larger stage in the shorter cuts and
tagging draws level with it at the whole story.

The level model costs the same at every length because it is asked once, in
one call, about all the words the lists do not cover.

Three points show which way each stage grows, not the shape of the curve.

**Recommendation.**

Nothing in the pipeline needs tuning for texts of this size; the dictionary is
where an analysis spends its time.

## The dictionary

**Parameter.**

How many lookups an analysis keeps in flight, the provider's request budget,
and whether the cache is warm.

**Experiment.**

`wordwinnow bench enrichment` runs the pipeline over the whole story, 1,509
vocabulary items and 1,401 provider requests once the repeated lemmas are
merged, against a replay provider that answers in 0.1 s and reports ten
percent of words unknown.

**Measurement.**

Median seconds for the dictionary stage, over two runs without a budget, one
with a budget, and three with a warm cache:

| Lookups in flight | No budget, cold cache | Budget of 20 per second, cold cache | No budget, warm cache |
| --- | --- | --- | --- |
| 1 | 147.23 | 147.40 | 0.015 |
| 2 | 74.88 | | 0.013 |
| 4 | 38.63 | 70.08 | 0.014 |
| 8 | 18.98 | | 0.015 |
| 16 | 9.36 | 69.36 | 0.015 |

**Interpretation.**

Without a budget the stage takes the provider's latency times the requests,
divided by the lookups in flight; with a budget it cannot go below the
requests divided by the budget, 70 s for 1,401 requests at 20 a second, and
more lookups in flight buy nothing past that.

The Free Dictionary API's own budget in Wordwinnow is two requests a second
after a burst of four, so the story's first analysis against it takes about
700 s at the least; that is worked out from the budget, not measured.

A real provider's latency also varies: one cold analysis of a 731-word text
against the Free Dictionary API, observed once, took about 21 minutes, with
278 requests of about 20 s each and 94 lookups unavailable after retries.

A warm cache removes the provider entirely, and in the distributed mode every
worker shares one cache through the enrichment service.

**Recommendation.**

Keep eight lookups in flight, which is the default; send every worker through
the one enrichment service so the budget and the cache are shared; and use
`--no-dictionary` or the WordNet dictionary when the time matters more than the
provider's definitions.

## Throughput in the distributed mode

**Parameter.**

The number of analysis workers.

**Experiment.**

`wordwinnow bench throughput --label workers=N --analyses 32` submits thirty-two
copies of the whole story to intake at once, without the dictionary, and each
run starts once every worker has joined the consumer group.

**Measurement.**

One run per worker count:

| Workers | Makespan (s) | Analyses per minute | Latency p50 (s) | Latency p95 (s) | Mean queue wait (s) | Mean processing (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 16.51 | 116 | 7.92 | 15.38 | 7.61 | 0.38 |
| 2 | 10.66 | 180 | 4.60 | 9.10 | 4.26 | 0.43 |
| 4 | 7.00 | 274 | 2.85 | 5.49 | 2.40 | 0.50 |

**Interpretation.**

The queue is where the time goes: with one worker a request at the 95th
percentile takes about fifteen seconds from submission to result, while
processing any one analysis takes under half a second, and each doubling of
the workers cuts the queue wait, the tail latency, and the makespan.

Each doubling buys about half as much again rather than twice as much, and
processing slows from 0.38 s to 0.50 s, because the workers, the brokers, and
the polling client share one laptop's cores.

The requested-analyses topic has four partitions, so four workers is the most
this stack can use.

**Recommendation.**

Four workers for texts without the dictionary; with the dictionary, more
workers do not help until the dictionary's budget does.

## What these numbers do not say

- They come from one machine, with one run per worker count, and with the
  containers sharing the laptop's cores with the client that submits and
  polls.
- The replay provider answers in a constant time; a real provider does not.
- The client polls once a second, so a measured makespan can run up to a
  second past the last result; the latencies come from the server's own
  timestamps.
- The model series depends on the file `make model` produces, which is derived
  and not committed.

## Reproducing

Every `bench` command appends its rows to its file in `benchmarks/results/`, and
`bench plot` draws the medians of every row a file holds, so a rerun in this
checkout adds its rows to the committed ones.

To measure your own machine alone, run the commands in a separate clone and
delete its `benchmarks/results/` files first.

The heuristic series runs without a model file, and the model series after
`make model`:

```sh
WORDWINNOW_LEVEL_MODEL_PATH=/nonexistent/model.joblib uv run wordwinnow bench stages --repetitions 5 --series heuristic
```

```sh
uv run wordwinnow bench stages --repetitions 5 --series model
```

```sh
uv run wordwinnow bench enrichment --latency 0.1 --repetitions 2
```

```sh
uv run wordwinnow bench enrichment --latency 0.1 --repetitions 3 --warm
```

```sh
uv run wordwinnow bench enrichment --latency 0.1 --requests-per-second 20 --concurrency 1 --concurrency 4 --concurrency 16 --repetitions 1
```

The throughput scenario runs once per worker count, against the stack
`make up-full` starts:

```sh
docker compose --profile full up -d --wait --scale analysis-worker=1
```

Wait until the consumer group's state is `Stable` with that many members:

```sh
docker compose --profile full exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:19092 --describe --group wordwinnow-analysis-workers --state
```

```sh
uv run wordwinnow bench throughput --label workers=1 --analyses 32
```

Repeat with two and four workers, then draw the figures:

```sh
uv run wordwinnow bench plot
```

The sample story ships with the repository, and [`data/corpus/README.md`](../data/corpus/README.md) says
where it comes from, so anyone can repeat every run.
