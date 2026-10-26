# Operating

Every command, the configuration, and what to do when something stops.

## The learner's commands

| Command | What it does |
| --- | --- |
| `wordwinnow analyze FILE --level LEVEL` | Analyze a text; `-` reads standard input |
| `wordwinnow analyze --source NAME --topic TOPIC --level LEVEL` | Analyze a document a source fetches |
| `wordwinnow show ID` | Show a stored analysis again |
| `wordwinnow list` | List recent analyses, the id first |
| `wordwinnow stats` | Summarize every completed analysis from ClickHouse |

`--local` runs `analyze`, `show`, and `list` against the SQLite store in this process;
without it, they talk to the intake service, which `stats` always reads from.

| Option | Meaning |
| --- | --- |
| `--level`, `--target` | The level you have, which every analysis requires, and the one you are working toward, one above your level when omitted |
| `--known FILE` | Words you already know, one per line |
| `--no-dictionary` | Skip the provider, which is the right choice offline and the quickest way to see the tool work |
| `--limit N` | With `--source`, how many of the source's items to combine; with `list`, how many analyses to list |
| `--tier`, `--rows` | Which tiers to show, `focus`, `stretch`, `review`, or `known`, and how many words |
| `--detail` | One card per word with pronunciation, examples, and related words |
| `--json`, `--csv FILE` | The whole analysis for another program, or the shown words for a flashcard tool |
| `--no-wait` | In the distributed mode, return the id at once |
| `--timeout SECONDS` | In the distributed mode, how long to wait for the result, 900 by default |
| `--view operator` | Under the progress line, what the dictionary is doing; `learner`, the default, shows the line alone |

A command that takes more than a moment says on standard error what it is
doing: the step, a count where the number of steps is known, as for the words
the dictionary is asked about, and the time the step has taken.

It does so only on an interactive terminal and clears the line when it ends,
so a pipe, a file, and the output of `--json` and `--csv` never contain it.

In the dictionary stage the count also says how many words went unanswered;
while words wait for the dictionary to answer again, the line counts down to
the next of them asking and to the moment WordNet stands in for every word
still waiting, and while the dictionary is not answering at all and nothing
waits, the line says so, because WordNet's glosses then stand in.

Once the stage has answered at least ten words over ten seconds or more of its
last minute, the count also says about how long the stage may still take at
that minute's pace, rounded as coarsely as such an estimate errs; there is
none while the dictionary is not answering or words wait, so it never counts
down through a pause whose end it cannot know.

When the stage goes without an answer for longer than three of its recent gaps
between answers, and five seconds at the least, the count says for how long in
place of the estimate, because something the line cannot see is holding the
lookups: a pause the provider asked for, a slow answer, or a trial, which the
operator view names.

In the distributed mode the line shows the same stages and counts, from what
the worker processing the analysis last wrote beside it, about once a second;
until that first report, it says whether a worker has taken the analysis.

With `--view operator`, the line stays, and under it a panel shows the
dictionary's mechanics while the analysis runs; its border and title say how
the provider has been answering the client: answering, in green; rate-limited,
in yellow, while a pause the provider asked for holds; and failing, in yellow
while requests fail in a row and in red while the circuit is not closed.

In the panel stand the circuit, with what opened it; the next try, while the
circuit or a pause holds the requests back; the last minute of the client's
own requests, and how long ago one was last answered once that is longer than
a minute; how the dictionary's lookups were answered since the view began,
from the cache or by the provider; the ones it turned away and the retries,
each by reason; the request budget; and the lookups in flight.

The row `last minute` has a mark for each second of the last minute, the newest
on the right: `✓` when a request the client sent was answered in that second,
found or not, `!` when the provider refused one with a 429, `✗` when one failed,
by a server error, a timeout, a connection that failed, or an answer the
client could not read, and `·` when none came back; in a second that held
several, a failure outweighs a refusal, and a refusal an answer.

Every row is what the client saw of its own requests or what it decided, and
none says what the provider did otherwise, which the client cannot see: a
provider that has recovered shows as recovered only once a trial is answered.

While the circuit is open, its row says what opened it, five failed requests
or the trial that failed last, with the reason, and the next try names the
trial that comes next and in how many seconds, or the end of the provider's
pause when that comes later, because the pause holds the trial too; while it
is half-open, its row says which trial is in flight and how many lookups wait
for its outcome.

While the circuit is closed, its row counts the requests that failed in a row
since the last answer, against the five that open the circuit; any answer
starts the count again, a request to slow down included.

The row `turned away` is a different number, a total by reason since the view
began, the words the open circuit refused without asking included, as
`held back`, so it can pass five while the count in a row stays below it and the
circuit stays closed; a word turned away may be asked again and answered, so
the report's count of unavailable words is the one that says what the learner
gets.

The lookups in flight are counted by what they wait for: asking the provider,
retrying after a pause, behind the trial, paused while the provider's pause
lasts, or queued for a token; each one asking or retrying has a row of its
own, with a bar against the request's timeout or against the pause before its
next attempt.

In the local mode the view reads the dictionary in its own process, whose
cache is a file the next command reads too; in the distributed mode it reads
`GET /activity` on the enrichment service at `WORDWINNOW_ENRICHMENT_URL`,
`http://127.0.0.1:8100` unless set, the port the Compose stack publishes, and
counts what the service did since the view began, for every analysis it served
meanwhile, so a second analysis running beside it shows in the counts.

The activity names the words in flight, as each lookup a worker sends does,
and like every port the stack publishes, the enrichment service's listens on
127.0.0.1 alone.

## Sources

`wordwinnow doctor` lists the sources the configuration enables, and
`wordwinnow analyze --help` lists every source with the topic it takes.

| Source | Needs | Topic |
| --- | --- | --- |
| `nyt-rss` | nothing | A feed name as The New York Times spells it: `Science`, `World`, `HomePage` |
| `nyt-top-stories` | the key | A section: `science`, `world`, `books/review` |
| `nyt-most-popular` | the key | `viewed`, `emailed`, or `shared`, with an optional period: `viewed/30` |
| `nyt-article-search` | the key | A query; ten articles per request, newest first |
| `nyt-archive` | the key | A month: `2026-08`; one response of about twenty megabytes |

### The New York Times

<a href="https://developer.nytimes.com">
<picture>
<source
media="(prefers-color-scheme: dark)"
srcset="https://developer.nytimes.com/files/poweredby_nytimes_150c.png">
<img
alt="Data provided by The New York Times"
src="https://developer.nytimes.com/files/poweredby_nytimes_150a.png">
</picture>
</a>

The logo is The New York Times's own, shown unaltered and linked to its
developer network as its branding guide asks of applications that display its
content; Wordwinnow is not affiliated with or endorsed by The New York Times.

The feeds and the APIs give headlines, abstracts, links, and, from article
search and the archive, each article's lead paragraph; none of them gives a
whole article.

The feeds need nothing, and every other source needs a key of your own:

1. Create a free account at <https://developer.nytimes.com>.
2. Create an app there for your own use of Wordwinnow, and enable the APIs the
   sources call: Top Stories, Most Popular, Article Search, and Archive.
3. Put the app's key in `WORDWINNOW_NYT_API_KEY`, kept as [Configuration](#configuration)
   describes, and run `wordwinnow doctor` to see the sources it enables.

The network offers more APIs than these: the Books API, for one, answers with
best-seller lists and links to reviews rather than text to read, so no source
uses it.

The terms allow one key per application, so a key is never shared, and the one
that travels in a request's URL is redacted from logs and spans.

The budget published for a key is five requests a minute and five hundred a
day; article search asks for at most five pages per analysis, and a request
over the budget is reported as the source being unavailable.

What The New York Times asks, and what Wordwinnow does about it:

| The terms ask | Wordwinnow |
| --- | --- |
| Attribution wherever their content is shown | Prints "Data provided by The New York Times" above the list of words, the written form the branding guide allows where a logo cannot be shown |
| A link to their page for every piece | Prints each story's link beside its headline |
| No change to headlines, content, or links | Keeps each headline and abstract as its own paragraph, exactly as fetched |
| No caching for users beyond 24 hours | Deletes an analysis once 24 hours have passed since it was requested, before anyone can read it again, and leaves the text and the sentences out of `--json` and `--csv`; the JSON keeps the attribution and each story's headline and link |
| No commercial use, and no software product or machine learning built on their content | Trains, fits, and evaluates nothing on it: the level model learns its labels from the reference lists alone, and the frequency heuristic's boundaries, fitted once to the same lists, are fixed |
| No archived or cached data sets for others | Keeps no text of theirs in its stores past the day; the per-word facts of an analysis, a lemma with its level, tier, and count, stay in the analytics store |
| A reasonable request volume | Asks for one feed or a few pages per analysis |

This is how the project reads the terms, not legal advice: read the current
[Terms of Use](https://developer.nytimes.com/terms) and the [branding guide](https://developer.nytimes.com/branding) before you register, and use the
sources for your own noncommercial study.

## The operator's commands

| Command | What it does |
| --- | --- |
| `wordwinnow doctor [--install-nltk-data]` | Check the NLTK data, the lists, WordNet, the model, and the sources |
| `wordwinnow db upgrade` | Bring the analysis store's schema up to date |
| `wordwinnow serve intake`, `wordwinnow serve enrichment` | Run one HTTP service |
| `wordwinnow work analysis`, `wordwinnow work aggregation` | Run one worker |
| `wordwinnow admin requeue` | Hand stale analyses back to the workers |
| `wordwinnow ml experiment`, `wordwinnow ml train` | Rerun the level experiment; train the shipped model |
| `wordwinnow bench stages`, `bench enrichment`, `bench throughput`, `bench plot` | The performance experiments |

## The distributed mode

```sh
make up-full
```

```sh
docker compose --profile full up -d --scale analysis-worker=4
```

That is how the worker count changes; the requested-analyses topic has four
partitions, so a fifth worker would idle.

`make down` stops everything and removes the volumes, the dictionary cache among
them; `docker compose --profile full down` stops everything and keeps them.

## Configuration

Every setting is an environment variable read once when a process starts, with
the `WORDWINNOW_` prefix except the OpenTelemetry endpoint, which keeps its
standard name; `.env.example` starts a `.env` with the two settings a reader is
most likely to fill in.

The defaults are what a process uses when nothing sets the variable, as for
the command line in a plain shell; inside the Compose stack,
`docker-compose.yml` sets the addresses of the other services, JSON logs, and
tracing, and the image sets the paths of the NLTK data, the reference lists,
and the model.

| Variable | Default | Meaning |
| --- | --- | --- |
| `WORDWINNOW_DATABASE_URL` | `sqlite+aiosqlite:///data/local/wordwinnow.db` | The analysis store |
| `WORDWINNOW_KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:9092` | The broker |
| `WORDWINNOW_KAFKA_CONSUMER_GROUP` | `wordwinnow-analysis-workers` | The analysis workers' consumer group |
| `WORDWINNOW_REDIS_URL` | unset | The enrichment service's dictionary cache, in its own process when unset; the command line caches in its own file, and a worker without an enrichment URL in its own process |
| `WORDWINNOW_CLICKHOUSE_HOST`, `_PORT`, `_DATABASE` | `127.0.0.1`, `8123`, `wordwinnow` | The fact store |
| `WORDWINNOW_CLICKHOUSE_USER`, `_PASSWORD` | `wordwinnow`, `wordwinnow` | The fact store's account, the one the Compose stack creates |
| `WORDWINNOW_INTAKE_URL` | `http://127.0.0.1:8010` | Where the CLI finds intake |
| `WORDWINNOW_ENRICHMENT_URL` | unset | Where a worker finds enrichment, the provider directly when unset; where the operator view reads the enrichment service's activity, `http://127.0.0.1:8100` when unset |
| `WORDWINNOW_INTAKE_PORT`, `WORDWINNOW_ENRICHMENT_PORT`, `WORDWINNOW_METRICS_PORT` | `8010`, `8100`, `9464` | Where the services listen |
| `WORDWINNOW_LEVEL_MODEL_PATH` | `models/cefr_level_model.joblib` | The level model; the frequency heuristic when the file is absent |
| `WORDWINNOW_REFERENCE_LISTS_DIR` | `data/reference` | The reference lists |
| `WORDWINNOW_NLTK_DATA` | unset | A directory added to NLTK's own search path for its data |
| `WORDWINNOW_NYT_API_KEY` | unset | Enables the New York Times API sources; an empty value counts as unset |
| `WORDWINNOW_DICTIONARY` | `free_dictionary` | `wordnet` answers from WordNet, locally, without the provider |
| `WORDWINNOW_DICTIONARY_BASE_URL` | `https://api.dictionaryapi.dev/api/v2/entries/en` | Where the Free Dictionary API answers |
| `WORDWINNOW_DICTIONARY_CONCURRENCY` | 8 | Lookups in flight per analysis |
| `WORDWINNOW_DICTIONARY_REQUESTS_PER_SECOND`, `WORDWINNOW_DICTIONARY_BURST` | 2, 4 | The provider budget |
| `WORDWINNOW_DICTIONARY_TIMEOUT_SECONDS` | 30 | How long one request to the provider may take |
| `WORDWINNOW_DICTIONARY_PATIENCE_SECONDS` | 180 | How long an analysis waits, in all, for a provider that stopped answering, before WordNet stands in for the words still waiting; 0 fails fast |
| `WORDWINNOW_ENRICHMENT_TIMEOUT_SECONDS` | 180 | How long a worker waits for one lookup from the enrichment service |
| `WORDWINNOW_DICTIONARY_FOUND_TTL_SECONDS`, `WORDWINNOW_DICTIONARY_NOT_FOUND_TTL_SECONDS` | 30 days, 7 days | How long the cache keeps a found and a not-found answer |
| `WORDWINNOW_DICTIONARY_CACHE_PATH` | `data/local/dictionary-cache.db` | The local mode's dictionary cache, kept from one command to the next; a file that cannot be opened leaves the command a cache in its own process and is left as it was, and deleting it only makes the next analysis ask the provider again |
| `WORDWINNOW_STALE_AFTER_SECONDS` | 600 | How long an analysis may wait since it was requested, or process since it started, before `wordwinnow admin requeue` hands it back |
| `WORDWINNOW_LOG_FORMAT`, `WORDWINNOW_LOG_LEVEL` | `console`, unset | `json` for the services; unset, the command line logs warnings and worse and a service logs from `INFO` up |
| `WORDWINNOW_TRACING_ENABLED` | false | Export traces to `OTEL_EXPORTER_OTLP_ENDPOINT` |

The New York Times key is a secret value that is never printed, and it is
redacted from the URLs it travels in; `wordwinnow doctor` prints the analysis
store's address with its password redacted.

The key is the only secret that is not a throwaway: the stores' passwords in
`docker-compose.yml`, which the ClickHouse settings also default to, are fixed
for a stack whose every published port listens on 127.0.0.1, and Redis and
Kafka there have no password at all.

A secret reaches a process only through its environment or an env file that
neither Git nor the image carries: `~/.config/wordwinnow/secrets.env`, outside
the repository, or a `.env` beside `.env.example`.

Both are supported, and neither needs direnv; the file outside the repository
is the one to prefer for the key, because no copy or archive of the working
tree carries it.

Where `XDG_CONFIG_HOME` is set, that file is
`$XDG_CONFIG_HOME/wordwinnow/secrets.env`.

The command line and the services read their settings from different places,
because the command line calls The New York Times itself in the local mode,
and intake calls it in the distributed mode:

| Process | Where it reads a setting | Which wins |
| --- | --- | --- |
| The command line | Its own environment, and `.env` in the directory it runs from | The environment |
| The Compose services | Their values in `docker-compose.yml`, then `secrets.env`, then `.env` in the repository, both files read by Compose itself, then the image's own paths | The first that holds the variable |

The services never see your shell's environment, so direnv makes no difference
to them; the one exception is the Grafana password, which `docker-compose.yml`
takes from the shell or from `.env`, and not from `secrets.env` directly.

The command line never opens `secrets.env` itself, so the key reaches it from
there only through the shell it runs in.

[direnv](https://direnv.net) is an optional convenience that does that: the committed `.envrc`
exports the file into every shell inside the repository and unexports it on
the way out, so the command line finds the key from any directory there.

Without direnv, run the command line from the repository root with the key in
`.env`, or export the file into the shell you run it from, which then holds the
key until it exits:

```sh
set -a; . ~/.config/wordwinnow/secrets.env; set +a
```

To set the file up, create the directory, write the line `.env.example` shows
into the file with an editor rather than a command, so that the key stays out
of your shell's history, and make the file readable by you alone:

```sh
mkdir -p ~/.config/wordwinnow
```

```sh
chmod 600 ~/.config/wordwinnow/secrets.env
```

With direnv, read `.envrc`, which holds no secret, and allow it once:

```sh
direnv allow
```

Then check that the `nyt-*` sources appear; `doctor` reports what the command line
sees, so it lists them only once the key has reached it:

```sh
uv run wordwinnow doctor
```

## Watching it

| Where | What |
| --- | --- |
| `http://127.0.0.1:3000` | Grafana, dashboard `wordwinnow`, user `admin` |
| `http://127.0.0.1:9090` | Prometheus |
| `http://127.0.0.1:8010/metrics`, `:8100/metrics` | The services' raw metrics |
| `http://127.0.0.1:8100/activity` | What the enrichment service's dictionary is doing now, as JSON: what `analyze --view operator` shows |
| `http://127.0.0.1:8010/docs`, `:8100/docs` | Each service's HTTP API, which FastAPI generates from its routes and schemas, to read and to try; `/openapi.json` on either port is the same contract as a document another program can read, and the `/docs` page loads its scripts from a CDN |
| `docker compose logs -f analysis-worker` | JSON log lines with `correlation_id` and `trace_id` |

One analysis is one `correlation_id` across intake, the worker, and enrichment,
and one trace in Tempo from the request to the facts.

The dashboard's dictionary panels show the circuit's state, how many lookups
are waiting for the request budget, for the end of the provider's pause, for
the provider, before a retry, or for a trial's answer, and the retries by
reason.

While an analysis processes, `GET /analyses/{id}` on intake carries the worker's
last report as `progress`: the stage, the count, the words left unanswered,
whether the dictionary is paused, and, while words wait for it to answer
again, the seconds from the report until the next try and until the stage
stops waiting.

## Recovering

The dictionary client asks again after a timeout, a failed connection, or a
server error a retry can change, at most twice per word, after a wait that
doubles each time and is partly random, or as long as the provider asks, up to
thirty seconds.

A word whose attempts all fail this way while the circuit stays closed, with
nothing answered since its first failure, may have failed for the provider
rather than for itself, so it is turned away with the next such wait as its
time to ask again; a word whose failures an answer to another word interrupted
gets WordNet's gloss at once, because asking it again would only fail again.

A request to slow down, HTTP 429, is about the client rather than the word:
the whole client pauses, for every lookup and every analysis it serves, as
long as the provider's `Retry-After` says or, without a usable one, for a second
that doubles with each refusal in a row, up to a minute.

Requests already on their way when the provider began refusing come back
refused too and count as one refusal: only a request sent after the last
refusal counted is refused in a row, and an earlier one's `Retry-After` still
holds the client as long as it says.

The refused word is asked again after the pause without spending one of its
attempts, and a word refused five times ends as rate limited, with the pause's
end as its time to ask again.

A pause ends with one request and then the usual two a second, so the lookups
that waited for it never leave together.

A lookup waits out a pause of up to thirty seconds; a longer one ends it as
rate limited, with the pause's end as its time to ask again, while the pause
stands for every other lookup.

`Retry-After` counts only as whole seconds or a date, as HTTP defines it: a
fraction, a sign, or a word is no wait the provider named, and the client then
decides the wait itself.

A server error that names a wait longer than thirty seconds ends that word's
lookup at once, with that time to ask again, rather than holding one of the
lookups the pipeline runs at a time: the analysis asks it again then if its
waiting time covers the wait, and otherwise WordNet's gloss stands in.

The Free Dictionary API asks for two minutes when its own server cannot be
reached: the network in front of it still answers the words it has cached, and
answers every other word after about twenty seconds with HTTP 522.

After five requests in a row fail, it stops asking, and tries one word with a
single request ten seconds later, and again ten seconds after each such
request that fails, each wait up to a quarter shorter at random, or, when that
is later, until every wait the provider named has ended, whichever request
named it; the words in between wait for that trial, or get WordNet's gloss
when the analysis has no waiting time left, and the report counts those as
unavailable.

Ten seconds keeps a long outage to six to eight trial requests a minute, and
asks a provider that came back within ten seconds unless it asked for longer;
the random quarter spreads the trials of several clients a little, and never
moves one later than the period or sooner than a wait the provider named.

Every word asked while that one word is tried waits for its outcome, and is
then asked as usual or, when the trial went unanswered, gets WordNet's gloss,
so the first analysis after an outage keeps every definition a recovered
provider gives.

The circuit counts as half-open from the moment that wait is over, because the
next word asked is the trial; the dashboard and the operator view show it so
on an idle stack, and starting the next analysis is how to find out whether
the provider answers again.

A word whose request is already on its way when the client stops keeps that
request's answer, but makes no further attempt, and a word waiting for the
budget or for its next attempt makes none either; a request already sent that
fails leaves the trial where it was, because only the trial decides, unless it
asks for a longer wait, which moves the trial to the end of it.

When such a word is answered instead, the client still waits for the trial,
because only the word tried closes the circuit, and a word that ends while
that one is being tried leaves the outcome to it.

An analysis waits for a dictionary provider that stopped answering, three
minutes in all unless `WORDWINNOW_DICTIONARY_PATIENCE_SECONDS` says otherwise: a
word the client turns away with a time to ask again, because the circuit is
open, the provider paused the client for longer than thirty seconds or refused
the word five times, a server error named a longer wait, or its attempts
failed before five failures in a row could open the circuit, gives back its
place among the lookups in flight and is asked again at that time, while the
analysis has waiting time left.

The waiting time counts from a word's first refusal until it is answered or
given up, its asking again included, while any word is in that state, however
many are, so an outage spends it once, and a word whose next try would come
after it has run out gets WordNet's gloss at once.

Waiting never asks a failing provider more often, because the client still
sends only the trial while the circuit is open; it changes only how long the
analysis takes, and while the dictionary answers none of the waiting words, a
word that asks again does so within the time left, so the words still waiting
when it runs out, behind a trial the provider does not answer, are let go then
with the refusals they had; once it answers a waiting word, the others are no
longer waiting for it to come back but for the pace of its answers, and are
not cut off.

The words are asked in the order a learner studies them: the focus words
first, then the stretch, the review, and the known words, and within each the
more frequent in the text first, so a provider that stops answering part way,
or waiting time that runs out, costs the words that matter least; the report
keeps its own order.

A setting of nought makes an analysis fail fast: every word turned away gets
WordNet's gloss at once.

In the distributed mode the worker waits, not the enrichment service: the
service answers a lookup the open circuit refuses at once, with the time to
ask again, so no request to it is held open while the circuit is open; during
a pause the provider asked for, or a word's retries, it holds the request as
the client does, and a lookup it holds longer than
`WORDWINNOW_ENRICHMENT_TIMEOUT_SECONDS` reaches the worker as timed out.

A worker that cannot reach the enrichment service, or waited that long for it,
asks again after as long as the service has been unreachable so far, a second
at the least and fifteen at the most, within the analysis's waiting time, so a
restart of the service costs an analysis a few seconds rather than its
definitions.

Each step is a log event:

| Event | Level | When |
| --- | --- | --- |
| `dictionary.retry` | `DEBUG` | A request is about to be sent again, with its reason, the provider's status, and its wait |
| `dictionary.retry.declined` | `DEBUG` | A failure named a wait longer than thirty seconds, and the lookup ends with that time to ask again |
| `dictionary.cooldown` | `DEBUG` | A 429 paused the whole client, with the wait the provider named, the wait the client chose, and the refusals in a row |
| `dictionary.circuit.opened` | `WARNING` | Five requests in a row failed, and the client stops asking |
| `dictionary.circuit.trial` | `DEBUG` | The wait is over, and one word is tried with a single request |
| `dictionary.circuit.reopened` | `INFO` | The word tried went unanswered too |
| `dictionary.circuit.closed` | `INFO` | The word tried was answered, and the client asks as usual again |

A source that fails or does not answer in time is refused to the learner with
its reason, and intake logs the same reason as `source.unavailable`, at `WARNING`,
because nothing else in its log would show it.

A worker that cannot write its progress logs `progress.unrecorded` once, at
`WARNING`, and goes on with the analysis.

An analysis that has waited since it was requested, or processed since it
started, for longer than `WORDWINNOW_STALE_AFTER_SECONDS`, including one intake
stored but could not publish, is handed back to the workers by
`wordwinnow admin requeue`.

Set that above the longest analysis you expect: an analysis still running when
it is handed back is processed a second time, and a cold analysis against the
Free Dictionary API can run for twelve minutes or more, three more while it
waits for a provider that stopped answering, which the default of ten minutes
does not cover.

A worker that meets a store or a broker that is not there exits, and Compose
restarts it from the last committed offset; a record that cannot be handled is
logged with its topic, partition, and offset, counted, and committed past.

Scaling the workers while analyses run is safe: a rebalance waits up to 30
seconds for each worker's record in hand (`rebalance.waiting_for_record`), and a
worker that loses a partition before committing logs `message.commit_lost` and
goes on, while the partition's next owner handles that record again.

A worker that holds one record for more than five minutes leaves the group on
its own, as [`architecture.md`](architecture.md) explains, so with more than one worker such an
analysis is handled twice, once by each.

Stopping a worker, with `docker compose stop` or `make down`, sends it SIGTERM: a
worker waiting for a record stops at once and leaves the consumer group, so
its partitions move without delay, and one holding a record finishes and
commits it if it can within Compose's grace period.

That period is ten seconds, since the stack sets no `stop_grace_period`, and a
worker still holding a record then is killed, and the record is handled again
by the partition's next owner.

An analysis whose pipeline failed is stored as `failed` with the reason, and
`wordwinnow show ID` shows it.

An analysis of a text that may be kept only for a while is deleted once its
time has passed, before anyone can read it again: intake deletes it before it
creates, lists, or shows an analysis, and a local command when it opens the
store.

## The gate and the lanes

`make verify` is the gate: formatting, linting, type checking, the conventions
checker with its positive control, the tests that need no backing service, and
the secret scan with its own.

The secret scan, `make secrets`, runs [gitleaks](https://github.com/gitleaks/gitleaks) over every commit and over the
staged changes, with its default rules and the one rule `.gitleaks.toml` adds
for the New York Times key, and it redacts every finding, so it never prints
the secret it reports.

It reads only what Git holds, never the working tree, so a key kept in an
ignored `.env` or outside the repository does not trip it, and a key that is
staged or committed does; before the repository has a commit, it scans the
staged changes alone.

Outside a Git repository it has nothing to read, and it says so rather than
reporting a clean scan of files it never saw.

Before it scans, it hands gitleaks one planted key that no file holds and
fails unless the project's own rule reports it, so a rule that stopped
matching cannot pass as a clean scan.

`make up` then `make test-integration` runs the lane that needs PostgreSQL, Kafka,
and ClickHouse; `uv run pytest -m live` runs the one test that reads a real New
York Times feed.
