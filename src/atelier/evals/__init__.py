"""The agent eval suite (docs/09-testing.md §9.4): the only place quality and token cost are
judged together.

* `task`     a task file: request, inputs, graders, and a reference spec that proves the task
             is solvable and its graders passable (checked offline, zero tokens)
* `graders`  deterministic graders that inspect the outputs themselves, not the ops' metadata
* `rubric`   the LLM-as-judge grader for aesthetic tasks
* `runner`   run tasks through the agent, report, and evaluate the Phase 2 exit gate

Running the agent over the suite spends tokens. Everything else here is free.
"""
