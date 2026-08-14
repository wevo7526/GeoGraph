"""The wire corpus: the GDELT event stream the model and the game train on.

Stored in Postgres rather than the graph because every bulk reader of it
groups by dyad and orders by time. See `core.ontology.pg_schema` for why, and
for the invariant that keeps both stores derived from one ontology.
"""
