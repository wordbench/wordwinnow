"""
Metrics and traces.

Every process builds one `Metrics` object against its own registry and serves
it; every process that talks to another carries a trace across the call, so
one analysis is one trace from the request to the facts.
"""
