# Getting started

From nothing to a first report, on your own machine.

## Before you start

You need Python 3.14 and [`uv`](https://docs.astral.sh/uv/), which fetches the interpreter if your system
lacks it.

You need network access to set it up, for the packages and the NLTK data.

Docker with Compose is needed only for the distributed mode and the
integration tests.

To run the gate or commit through the repository's hooks, you also need
[gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 on your `PATH`, the version the gate checks for: `make verify`
and the `pre-push` hook run its secret scan, and the `pre-commit` hook runs the
scan alone.

Its [release page](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) has an archive for each platform, and a package manager
that carries that version works as well; analyzing a text never needs it.

The environment I develop Wordwinnow in, these tools included, is set up by my
[dotfiles](https://github.com/olegturzhanskii/dotfiles); nothing here depends on them.

## Setting it up

```sh
make setup
```

```sh
make nltk-data
```

The first command creates the locked environment; the second downloads
whichever of NLTK's sentence splitter, tagger, stop words, and WordNet are
missing, as [one fixed commit of the NLTK project's data repository](https://github.com/nltk/nltk_data/tree/550b6625bcef1f2abff2ff770a5a0d272c9c6b2a) holds
them, into your NLTK data directory.

```sh
make model
```

That trains the level model from the reference lists in under a minute and is
optional: without it, a word outside the lists gets the frequency heuristic's
level instead, and every level in a report names its source.

`uv run wordwinnow doctor` says what is present and what is missing.

## The first analysis

```sh
uv run wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --local --level A2 --target B1 --no-dictionary
```

The analysis runs inside the command, against a SQLite file under `data/local/`
that the command creates and migrates itself.

`--no-dictionary` skips the dictionary, so the report comes back in a few
seconds with WordNet's glosses as meanings; drop the flag to get the Free
Dictionary API's definitions, examples, and pronunciation, which take longer,
because every distinct word is a request to a service outside Wordwinnow.

`--detail` prints one card per word, `--json` the whole analysis, and `--csv FILE`
the shown words for a flashcard tool.

`uv run wordwinnow list --local` lists what was stored, and
`uv run wordwinnow show ID --local` shows one analysis again.

## Texts from The New York Times

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

```sh
uv run wordwinnow analyze --source nyt-rss --topic Science --level B1 --local --no-dictionary
```

The feeds need no credential.

The report names The New York Times as the source and links every story, and
nobody can read the analysis after a day, because the text is theirs.

With a developer key in `WORDWINNOW_NYT_API_KEY`, the API sources appear as
well; `wordwinnow doctor` lists the sources the configuration enables,
`wordwinnow analyze --help` lists what each source expects as a topic, and
[`operating.md`](operating.md) says where to keep the key, with or without direnv.

## The distributed mode

```sh
make up-full
```

That builds the image, which trains its own level model, and starts
PostgreSQL, Redis, Kafka, ClickHouse, the migration job, intake, enrichment,
one analysis worker, the aggregation worker, Prometheus, Tempo, and Grafana.

```sh
uv run wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --level B1
```

Without `--local`, the command posts the text to intake, waits for a worker to
finish, and shows the result; `wordwinnow stats` then summarizes every completed
analysis from ClickHouse.

The story's 1,508 vocabulary items come to 1,400 requests to the provider, one
per distinct lemma, and the enrichment service sends them at up to two a
second, but when last measured the Free Dictionary API answered about one a
second and refused the rest until each 20-second window ended, so the first
analysis took over twenty-two minutes, and later ones reuse the cache.

The command waits up to fifteen minutes, which `--timeout` changes, and the
analysis goes on whether or not it waits: when the wait ends, or Ctrl-C stops
it, the command names the analysis, `wordwinnow show ID --wait` waits for it
again, and `wordwinnow show ID` shows how far it has come, or the result once it
is done.

`--view operator` shows, under the progress line, what the enrichment service's
dictionary is doing meanwhile: whether the provider is answering,
rate-limited, or failing, the last minute of the client's requests, and the
lookups in flight.

Its counts are of lookups, not words: a word the dictionary turns away and
asks again counts each time, so `turned away` can pass the number of words;
[the operating guide](operating.md#the-terms-and-what-each-row-counts) says what each row counts.

`WORDWINNOW_DICTIONARY=wordnet` in `.env` before `make up-full` answers from WordNet
instead, and the same analysis then took about five seconds, observed once on
the laptop the performance numbers come from.

`make down` stops everything and keeps the data, the analyses and the dictionary
cache among it, for the next `make up-full`; `make reset CONFIRM=1` deletes it.

## Checking it

```sh
make verify
```

That is the gate [`operating.md`](operating.md) describes, with the integration lane beside
it.

If you intend to commit, opt into the repository's hooks once per clone:

```sh
make hooks
```

The `pre-commit` hook runs the secret scan over the history and what you stage,
the `commit-msg` hook checks each message, and the `pre-push` hook runs
`make verify`.

## Where to go next

[`product.md`](product.md) says what the tool is for and what it will not do.

[`operating.md`](operating.md) covers every command, the configuration, and recovery.

[`domain.md`](domain.md) defines the terms and says how a word gets its level.
