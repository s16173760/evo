# Evo CLI Quick Reference

Use this when you need to operate an evo workspace. The CLI orchestrates
experiments; the Agent SDK instruments benchmark code.

## Mental Model

- `evo init` creates `.evo/`, config, graph state, and the dashboard.
- `evo new` creates one experiment workspace from a parent node.
- `evo run` executes benchmark + inherited gates, then commits only if the
  score improves and gates pass.
- `evo run --check` validates wiring without mutating experiment state.
- `evo gate ...` defines branch policy; gates inherit down the tree.
- `evo config runtime ...` and `evo env ...` describe runtime state.
- Workspace ops (`bash/read/write/edit/glob/grep`) are the safe way to touch
  remote experiment files.

## Setup

```bash
evo init \
  --name "<project name>" \
  --target <entrypoint-file> \
  --benchmark "<command using {worktree} and/or {target}>" \
  --metric <max|min> \
  --host <claude-code|codex|opencode|openclaw|hermes|generic> \
  [--instrumentation-mode <sdk|inline>] \
  [--gate "<command>"] \
  [--commit-strategy <all|tracked-only>]
```

- `--name` is dashboard display text. Existing unnamed workspaces fall back to
  the repo directory name.
- `--target` is the evaluation entrypoint passed to `{target}`. It is not the
  entire optimization boundary.
- `--benchmark` is the command evo runs. Use `{worktree}` for files created in
  experiment branches.
- `--host` records the orchestrator runtime; it controls whether `dispatch` is
  available.

## Configuration

```bash
evo config show [--json]
evo config set project-name "<name>"
evo config set target <path>
evo config set benchmark "<command>"
evo config set metric <max|min>
evo config set commit-strategy <all|tracked-only>
```

Do not hand-edit `.evo/run_*/config.json` unless debugging the CLI itself.

## Runtime Recipe

```bash
evo config runtime show [--json]
evo config runtime set \
  [--prepare "<cmd>"] \
  [--before-run "<cmd>"] \
  [--prefix "<cmd>"]
```

- `prepare` runs in the experiment workspace before benchmark/gates.
- `before-run` runs in the experiment workspace before each attempt.
- `prefix` prepends benchmark and gate commands, e.g. `uv run` or `pnpm exec`.
- Use this instead of hard-coding local paths like `{worktree}/.venv/bin/python`.

## Runtime Env

```bash
evo env show [--json]
evo env inherit-shell <on|off>
evo env load <path> --all
evo env load <path> --allow KEY1,KEY2
evo env clear
```

- Env values resolve fresh on each `evo run`.
- Config stores source metadata and key names, not secret values.
- Dotenv files are read by the orchestrator and injected into local/remote
  process env. Remote workers do not read your local `.env` file directly.
- Gates receive runtime env but not `EVO_*` artifact variables.


## Backends

```bash
evo config backend worktree
evo config backend pool --workspaces /abs/slot-a,/abs/slot-b
evo config backend remote --provider <provider> [--provider-config k=v,...]
```

Per-experiment overrides are also available on `evo new`:

```bash
evo new --parent <id> -m "<hypothesis>" --backend remote --provider e2b
evo new --parent <id> -m "<hypothesis>" --remote modal
```

Provider auth and SDK packages are separate from benchmark runtime env.

## Experiment Lifecycle

```bash
evo new --parent <parent_id> -m "<hypothesis>"
evo run <exp_id> [--timeout <seconds>]
evo run <exp_id> --check [--timeout <seconds>]
evo done <exp_id> --score <float> [--traces <dir>] [--no-compare]
evo discard <exp_id> --reason "<why>"
evo prune <exp_id> --reason "<why>"
evo gc
```

Outcomes:

- `COMMITTED`: score improved and gates passed; node is kept.
- `EVALUATED`: run completed but score regressed or gates failed; inspect and
  either retry the same node or discard it.
- `FAILED`: infra/runtime/benchmark crash; does not consume retry budget.

`evo done` is for externally scored runs only. Do not call it after a successful
`evo run`.

## Gates

```bash
evo gate add <node_id> --name <name> --command "<cmd>"
evo gate list <node_id>
evo gate remove <node_id> --name <name>
evo gate check <node_id> [--timeout <seconds>]
```

- Gates are node-scoped policy and inherit to descendants.
- `evo run exp_N` evaluates gates inherited from the parent path.
- Gate pass/fail is exit-code based only. A command that prints a low score and
  exits 0 passes. Use tests or `--min-score` style gates that exit non-zero on
  regression.
- `evo gate check` writes `gate_check.json` under checks and does not run the
  benchmark or mutate node state.

## Inspection

```bash
evo status
evo tree
evo frontier [--strategy <kind>] [--params '<json>'] [--seed <n>]
evo scratchpad
evo get <exp_id> [filename]
evo path <exp_id>
evo diff <exp_id> [other_id]
evo traces <exp_id> [task_id]
evo log <exp_id> <filename>
evo annotations [--task <id>] [--exp <id>]
evo annotate <exp_id> [task_id] "<analysis>"
evo set <exp_id> [--tag <tag>] [--note <note>]
evo infra -m "<message>" [--breaking]
```

Useful files under `.evo/run_*/experiments/<exp_id>/`:

- `attempts/NNN/outcome.json`
- `attempts/NNN/benchmark.log`
- `attempts/NNN/benchmark_err.log`
- `attempts/NNN/gate_<name>.log`
- `checks/NNN/check.json`
- `checks/NNN/gate_check.json`

## Workspace Ops

Use these when an experiment may be remote, or when the orchestrator gave you
an explicit experiment id:

```bash
evo bash --exp-id <exp_id> "<command>" [--cwd <path>] [--timeout <seconds>]
evo read --exp-id <exp_id> <path>
evo write --exp-id <exp_id> <path> [--content "<text>"]
evo edit --exp-id <exp_id> <path> --old "<old>" --new "<new>" [--replace-all]
evo edit --exp-id <exp_id> <path> --json-stdin
evo glob --exp-id <exp_id> "<pattern>" [--path <dir>]
evo grep --exp-id <exp_id> "<pattern>" [--path <dir>]
```

`--exp-id` is required by design. Concurrent subagents may own different
remote containers; there is no safe default active experiment.

For local worktree/pool backends, native file tools are fine if you use the
actual worktree path returned by `evo new`.

## Dispatch

```bash
evo dispatch run --parent <id> -m "<brief>" [--budget N] [--background]
evo dispatch wait [job_ids...] [--quiet]
evo dispatch list [--running] [--recent N]
evo dispatch status <job_id>
evo dispatch kill <job_id>
```

`dispatch` is subagent async for `claude-code` fork-cache. It is not background
benchmark execution. `evo run` remains a blocking evaluation transaction.

## Common Mistakes

- Do not hand-edit config JSON; use `evo config ...`, `evo env ...`, or
  dashboard settings.
- Do not create `mktemp` validation wrappers; use `evo run --check` or
  `evo gate check`.
- Do not assume `.venv`, `node_modules`, caches, or downloaded assets exist in
  experiment worktrees. Use `evo config runtime`.
- Do not copy `.env` into worktrees or sandboxes; use `evo env`.
- Do not register decorative gates that exit 0 on failure.
- Do not use native file tools against remote worktree paths; use workspace ops.
- Do not run from inside an experiment worktree; run `evo` from the main repo
  root unless using workspace ops with explicit `--exp-id`.
