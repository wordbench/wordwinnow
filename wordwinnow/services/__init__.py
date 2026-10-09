"""
The deployable processes, one package each, and the composition that wires
them.

`cli` is the learner's and the operator's only interface; `intake`,
`enrichment`, `analysis_worker`, and `aggregation_worker` are the services the
distributed mode runs.
"""
