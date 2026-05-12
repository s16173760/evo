Use the `optimize` skill to explore optimizations for `target.py` in this
repository.

**Hard requirements** (do not deviate):

- Every experiment MUST be committed through the evo pipeline using the
  skill's documented commands (`evo new`, `evo run <exp_id>`, etc.).
- Do not create separate `target_A.py` / `target_B.py` / etc. files in
  the workspace root. Do not write your own benchmark harness or invoke
  `python bench.py` directly. Use evo's worktree + `evo run` flow only.
- Each subagent edits `target.py` inside its own evo-managed worktree
  and reports back through evo's outcome.json.

**Round 1**: launch exactly 2 experiments in parallel, each trying ONE of
these approaches (do not deviate, do not pick a different algorithm):

- Experiment A: keep the double loop but cache `xs[i]` in a local variable
  in the outer loop. Same O(n²); small constant-factor win.
- Experiment B: rewrite using `itertools.combinations`. Same O(n²); pushes
  the inner loop into C.

**Round 2**: launch exactly 1 experiment:

- Experiment C: sort `xs` first, then for each `i` use `bisect` to find
  `target - xs[i]` in the tail. O(n log n).

Report the best score at the end.
