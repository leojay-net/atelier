"""ML plugins (docs/08 Phase 6): ops that run a pinned neural model.

They follow four rules so the engine's promises still hold:

* The op module imports no ML library at import time (the warm standby workers import every
  builtin op module; a model runtime would be loaded by all of them). Runtimes are imported inside
  the op, and a missing one is an OpError that says which extra to install.
* Weights are pinned in `assets/models/models.json`, fetched ahead of time with
  `atelier models fetch`, and their hashes are part of the op's cache key (`resources=`).
* Inference is single-threaded and uses fixed settings, and the op is `SEEDED_ML`: identical bytes
  are expected on the same hardware and runtime versions (tested), not across platforms.
* No network, ever, inside a job.
"""
