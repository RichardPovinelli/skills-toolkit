"""Identity of the tooling that produces and grades evaluation runs.

These constants travel with the runner so every run's ``metadata.json`` records
which tool version produced it, independently of the collection's own revision.
"""

TOOLKIT_NAME = "evals-runner"
TOOLKIT_VERSION = "0.1.0"
