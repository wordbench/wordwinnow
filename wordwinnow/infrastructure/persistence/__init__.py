"""
The analysis store: the tables, the mapping between rows and the domain, the
repository and the unit of work over them, and the migrations that shape the
schema.

One set of models serves SQLite for the local mode and PostgreSQL for the
distributed one, so nothing here uses a type only one of them has.
"""
