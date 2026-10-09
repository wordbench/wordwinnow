"""
The enrichment service: the one process that talks to the dictionary provider.

Every worker asks it instead of the provider, so the provider sees one budget,
one circuit breaker, and one cache however many workers run.
"""
