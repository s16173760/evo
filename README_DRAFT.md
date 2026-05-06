<p align="center">
  <img src="assets/banner.png" alt="evo banner" width="100%" />
</p>

# evo

Evo is an autoresearch orchestrator for agentic coding frameworks.

It is for engineers who already have a codebase and a benchmarkable task: a metric to improve, a workflow to protect with gates, and enough signal for an agent to run autonomous code experiments, measure them, and keep only what works.

Evo does not just ask an agent to “edit code.” It orchestrates an experiment loop:

- propose a change
- run the benchmark
- record traces and outcomes
- keep improvements
- branch when multiple directions are worth exploring

What makes evo an autoresearch orchestrator rather than a generic coding plugin:

- **Tree search, not a single hill climb.** Multiple experiment branches can fork from any committed result.
- **Parallel subagents.** Several agents can explore different directions at the same time.
- **Shared experiment memory.** Traces, failures, notes, and discarded hypotheses stay visible across the run.
- **Measured improvement.** Changes are kept only when the benchmark improves and gates pass.
- **Observability.** A local dashboard shows the experiment tree, attempts, traces, and runtime state.

Inspired by [Karpathy's autoresearch](https://github.com/karpathy/autoresearch), but built as an orchestrator for structured branching, parallel exploration, and persistent experiment state instead of a single linear loop.

## Quickstart

Common requirements: `git`, [uv](https://docs.astral.sh/uv/), Python 3.10+.

### 1. Install evo

Claude Code bundles evo with the plugin. Other hosts call `evo` as an external CLI:

```bash
uv tool install evo-hq-cli   # or: pipx install evo-hq-cli
evo --version
```

### 2. Install the plugin for your host

**Claude Code**

```text
/plugin marketplace add evo-hq/evo
/plugin install evo@evo-hq-evo
```

Invoke with:
- `/evo:discover`
- `/evo:optimize`

**Codex** (requires 0.122.0+)

```bash
codex plugin marketplace add evo-hq/evo
```

Then install the plugin from `/plugins`.

Invoke with:
- `$evo discover`
- `$evo optimize`

**OpenClaw**

```bash
openclaw plugins install evo --marketplace https://github.com/evo-hq/evo
```

Invoke with:
- `/discover`
- `/optimize`

**Hermes**

```bash
hermes skills install evo-hq/evo/plugins/evo/skills/discover --force
hermes skills install evo-hq/evo/plugins/evo/skills/optimize
hermes skills install evo-hq/evo/plugins/evo/skills/subagent
```

Invoke with:
- `/discover`
- `/optimize`

### 3. Run evo on a repo

Typical first run:

```text
you: /evo:discover
evo: explores the repo, identifies a target, sets up the benchmark, runs the baseline, and starts the dashboard

you: /evo:optimize
evo: spawns parallel subagents, runs experiments, keeps improvements, and discards regressions
```

The exact invocation prefix depends on the host, but the workflow is the same.

## How it works

`discover` is the setup pass:

- explores the repo
- identifies a target and metric
- wires up the benchmark and gates
- creates the baseline experiment
- starts the dashboard

`optimize` is the autoresearch loop:

- spawns multiple subagents in parallel
- branches from promising experiments
- runs bounded experiment loops per branch
- commits improvements
- discards regressions or gate failures

Each experiment lives in its own git worktree or remote workspace derived from its parent. Successful experiments are committed into the experiment graph. Failed ones are kept as traces and outcomes, then discarded as workspace state.

## What a first run changes

Running evo is not a no-op. A first run typically:

- creates `.evo/` metadata in the repo
- creates baseline and experiment state
- may instrument the benchmark path inside an experiment workspace
- starts the local dashboard

That is intentional: evo is an autoresearch orchestrator with persistent experiment state, not just a one-shot command runner.

## Remote execution

You can run experiments locally or remotely.

Most users can ignore remote execution at first. Start local unless:

- benchmarks are too heavy for the local machine
- you want isolated sandboxes per experiment
- you need cloud-backed execution

Current remote providers:

- `modal` — managed sandbox via Modal
- `e2b` — managed sandbox via E2B
- `daytona` — managed sandbox via Daytona
- `aws` — EC2-backed remote workspace
- `ssh` — remote workspace on an existing machine you can SSH into
- `manual` — connect to an already-running remote endpoint

User-facing rule of thumb:

- choose `modal`, `e2b`, or `daytona` if you want a managed provider path
- choose `aws` or `ssh` if you want more direct control over the machine
- choose `manual` only if you already operate the remote endpoint yourself

### Remote provider install

If you want a remote provider, install the matching extra on the local evo CLI:

```bash
uv tool install 'evo-hq-cli[modal]'
uv tool install 'evo-hq-cli[e2b]'
uv tool install 'evo-hq-cli[daytona]'
uv tool install 'evo-hq-cli[aws]'
```

With `pipx`, install `evo-hq-cli` once and inject the matching provider package into that environment:

```bash
pipx install evo-hq-cli
pipx inject evo-hq-cli modal
```

### Remote provider expectations

Installing the extra is only the local dependency step.

You still need real provider setup before the first remote allocation:

- `modal` — local Modal auth
- `e2b` — local E2B API key
- `daytona` — local Daytona API key
- `aws` — AWS auth plus real cloud config such as image, key pair, and usually network details
- `ssh` — a reachable host, working SSH user, and correct key/port
- `manual` — an already-running remote endpoint

Examples:

```bash
evo config backend remote --remote modal
evo config backend remote --remote e2b
evo config backend remote --remote daytona
evo config backend remote --remote aws --provider-config region=...,image_id=...,key_name=...,key=/abs/path/key.pem
evo config backend remote --remote ssh:user@host --provider-config key=/abs/path/key
```

Or per experiment:

```bash
evo new --parent root -m "try remote" --remote modal
evo new --parent root -m "try AWS" --remote aws --provider-config region=...,image_id=...,key_name=...,key=/abs/path/key.pem
```

Incomplete provider setup usually surfaces on the first real `evo new --remote ...`, because that is where allocation and remote setup actually happen.

## Dashboard

The dashboard is part of the normal workflow, not an optional debug extra.

It shows:

- the experiment tree
- benchmark and gate outcomes
- per-attempt traces and logs
- backend/runtime state
- execution settings

It starts automatically during `discover` and `evo init`. Evo prints the live URL when it starts:

```text
Dashboard live: http://127.0.0.1:8080 (pid 12345)
```

If `8080` is busy, evo increments the port and prints the actual one in use.

You can also start it manually:

```bash
uv run --project /path/to/evo/plugins/evo evo dashboard --port 8080
```

## Architecture

```text
Orchestrator (main agent)
  - reads experiment state
  - identifies cross-cutting failures and promising branches
  - writes a structured brief per subagent
  - collects results and updates the search strategy

Subagents (parallel)
  - inspect traces and failures
  - propose a bounded hypothesis
  - edit, run, and evaluate within their branch budget
  - return outcomes and learnings
```

The important point is not just “many agents.” It is that evo keeps the autoresearch loop coherent across rounds: branching structure, measurements, traces, failures, and accepted improvements all stay in one system.

## Dev install

For working on evo itself:

```bash
git clone https://github.com/evo-hq/evo
cd evo
uv run --project plugins/evo evo --version
```

The language SDKs live separately:

- `sdk/python/` — `evo-hq-agent`
- `sdk/node/` — `@evo-hq/evo-agent`

## TODO

- [ ] Distributed evaluation via [Harbor](https://github.com/harbor-framework/harbor)

## License

Licensed under the [Apache License 2.0](LICENSE).
