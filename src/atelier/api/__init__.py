"""The application layer (docs/02 §4, ADR-0007): a Django service around the engine and the agent.

Dependency direction: this package imports `atelier.engine`, `atelier.ops` and `atelier.agent`;
they never import it, and the CLI never needs Django. Web requests never run media work: a
request records a job, a worker process runs it (each step still in its own sandboxed
subprocess), and progress is stored as events that clients read by polling or as a stream.
"""
