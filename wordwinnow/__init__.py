"""
wordwinnow: winnow a text into the vocabulary worth a learner's attention.

The package is layered from the inside out: `domain` holds the vocabulary
analysis model and depends on nothing, `application` holds the use cases and
the ports they need, `infrastructure` holds the adapters that satisfy those
ports, and `services` holds the deployable processes that compose them.
"""
