# Security policy

Report a vulnerability privately, through GitHub's private vulnerability
reporting, from the repository's [Security tab](https://github.com/wordbench/wordwinnow/security), and never in a public issue.

## In scope

A report is in scope when it concerns:

- the wheel attached to a release;
- what `wordwinnow setup` downloads, and how it checks what it downloads;
- the local store and the files the command writes;
- the distributed mode, run from a clone on one host, within the boundary that
  [the operating guide](docs/operating.md#security) describes: what its services and its stack listen on,
  which requests they refuse, and how its generated passwords are kept.

## Out of scope

The services Wordwinnow calls and the lists it downloads belong to their
owners, who are the ones to fix a problem in them.

Reaching the distributed mode's services from another machine, from another
user of the same host, or from the internet is unsupported, so a report that
needs one of them describes a use the guide rules out, and the guide's section
lists what its protections do not cover.

## What to expect

A fix, when one is made, comes in a later release, as [the README](README.md#status) says of
every error.

There is no promise of a response time.
