# Documentation

Each document here answers one question and owns the answer.

| Question | Document |
| --- | --- |
| What is Wordwinnow, and who is it for? | [`product.md`](product.md) |
| How do I get it running? | [`getting-started.md`](getting-started.md) |
| What do the terms mean, and how does a word get its level? | [`domain.md`](domain.md) |
| How is the system structured, and why is each technology in it? | [`architecture.md`](architecture.md) |
| How do I operate it? | [`operating.md`](operating.md) |
| How fast is it, and what was measured? | [`performance.md`](performance.md) |
| Why does it exist, and why this sample story? | [`data/corpus/README.md`](../data/corpus/README.md) |

Each fact has one home.

Where a document needs a fact another one owns, it links there, and repeats
the fact only where a reader needs it on the spot.

## Reading orders

**To use it:** [`product.md`](product.md), then [`getting-started.md`](getting-started.md).

**To run it:** [`getting-started.md`](getting-started.md), then [`operating.md`](operating.md), with [`performance.md`](performance.md)
for sizing.

**To understand it:** [`product.md`](product.md), then [`domain.md`](domain.md), then [`architecture.md`](architecture.md).

**To know why it exists:** [`data/corpus/README.md`](../data/corpus/README.md), then [`product.md`](product.md).

## Code conventions

This project conforms to the [engineering conventions standard](https://github.com/olegturzhanskii/engineering-conventions/blob/v1.1.0/STANDARD.md), **v1.1.0**.

The standard is referenced rather than copied, so a difference between this
project and the standard can be told apart as a violation or a version gap.

Its enforcement is vendored: `tools/check_conventions.py`,
`tools/check_commit_message.py`, and `.taplo.toml` are byte-for-byte copies, and
`pyproject.toml` carries the rule selection beside this project's own line
length, source roots, and recorded exceptions.

`make conventions` runs the file checker over the Python, the Markdown, the
notebook cell by cell, and `SHA256SUMS` with the files that could download a
tool, positive control first, and `make verify` includes it.

`make check-commits` runs the message checker over every commit not yet on
`origin/main`; the `commit-msg` hook runs it on each commit for a clone that opted
in with `make hooks`.

`make drift` proves the vendored copies match, byte for byte, the commit of the
standard this project pins, the one its v1.1.0 tag names, read from a checkout
of it (`CONVENTIONS=path`).

It stays out of `make verify`, because the gate must keep working when the
standard's repository is not reachable, and between upgrades this project's
own history shows that the copies have not changed.
