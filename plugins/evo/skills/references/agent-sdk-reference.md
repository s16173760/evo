# Evo Agent SDK Reference

Use this when benchmark code needs to report scores and traces to evo. The SDK
is separate from the evo CLI: the CLI orchestrates experiments; the SDK runs
inside the benchmark process.

## Contract

Evo sets these env vars for benchmark processes:

- `EVO_RESULT_PATH`: absolute path where final result JSON must be written.
- `EVO_TRACES_DIR`: absolute directory for per-task `task_<id>.json` traces.
- `EVO_EXPERIMENT_ID`: current experiment id.
- `EVO_ATTEMPT`: attempt label.
- `EVO_WORKTREE`: experiment workspace path.

Final result JSON must contain:

```json
{"score": 0.75}
```

Optional fields:

```json
{
  "score": 0.75,
  "tasks": {"task_a": 1.0, "task_b": 0.5},
  "tasks_meta": {"task_b": {"direction": "max"}}
}
```

Each trace file should be enough to debug a failing task without reading a
parallel recorder.

## Install

Use the project's package manager/runtime, after the user has chosen SDK mode:

```bash
uv add --dev evo-hq-agent
python -m pip install evo-hq-agent
npm install @evo-hq/evo-agent
```

Do not install packages silently from a skill.

## Python Benchmark

```python
from evo_agent import Run

run = Run()
try:
    for task in tasks:
        task_id = task["id"]
        run.log(task_id, {"event": "start", "input": task["input"]})
        try:
            result = evaluate(task)
            run.log(task_id, {"event": "model_output", "output": result.output})
            run.report(
                task_id,
                score=result.score,
                summary=f"score={result.score:.2f}",
                failure_reason=None if result.passed else "wrong_answer",
            )
        except Exception as exc:
            run.log(task_id, {"event": "exception", "error": repr(exc)})
            run.report(task_id, score=0.0, failure_reason="exception")
finally:
    run.finish()
```

`finish()` writes the final result JSON and task traces. Catch expected per-task
errors and still call `report()`. If an uncaught exception escapes before
`finish()`, evo correctly treats the benchmark as crashed.

Avoid relying on `with Run() as run:` unless you understand its exception
behavior. If an exception leaks through the context, `finish()` may be skipped
to avoid publishing a misleading score. The robust pattern is explicit
`try/finally`.

## Node Benchmark

```javascript
import { Run } from '@evo-hq/evo-agent';

const run = new Run();
try {
  for (const task of tasks) {
    run.log(task.id, {event: 'start', input: task.input});
    try {
      const result = await evaluate(task);
      run.log(task.id, {event: 'model_output', output: result.output});
      run.report(task.id, {
        score: result.score,
        summary: `score=${result.score}`,
        failure_reason: result.passed ? null : 'wrong_answer',
      });
    } catch (error) {
      run.log(task.id, {event: 'exception', error: String(error)});
      run.report(task.id, {score: 0.0, failure_reason: 'exception'});
    }
  }
} finally {
  await run.finish();
}
```

## Run.report() reference

Records the eval result for one task and writes its trace file. Call once per task.

**Python signature:**

```python
run.report(
    task_id,                      # required: str
    score,                        # required: float
    *,
    status=None,                  # "passed" | "failed"; default: derived from score >= pass_threshold
    pass_threshold=0.5,           # threshold for the auto-derived status
    summary=None,                 # short human-readable description of the result
    failure_reason=None,          # short tag (e.g. "wrong_answer", "timeout", "exception")
    cost=None,                    # dict; e.g. {"input_tokens": 1234, "output_tokens": 567, "usd": 0.012}
    started_at=None,              # ISO8601; default: time of first log() for this task, else Run start
    ended_at=None,                # ISO8601; default: now
    artifacts=None,               # dict[str, str]; e.g. {"screenshot": "/path/to/png"}
    direction=None,               # "max" | "min"; only set when this task differs from the workspace metric
    **extra,                      # any extra keys are merged into the trace as-is
)
```

**Node signature:**

```js
run.report(taskId, {
    score,                        // required: number
    status,                       // 'passed' | 'failed'; default derived from passThreshold
    passThreshold = 0.5,
    summary,
    failureReason,                // snake-cased to failure_reason in the trace
    cost,
    startedAt, endedAt,
    artifacts,
    direction,                    // 'max' | 'min'
    ...extra,
});
```

**Field semantics:**

| Param | Required | Lands in trace as | Notes |
| --- | --- | --- | --- |
| `task_id` / `taskId` | yes | `task_id` | String-coerced. Distinct task IDs become distinct trace files (`task_<id>.json`). |
| `score` | yes | `score` | Numeric. Aggregated by `finish()` as the mean across reported tasks unless overridden. |
| `status` | no | `status` | Pass through. If omitted, derived: `score >= pass_threshold ? "passed" : "failed"`. |
| `pass_threshold` / `passThreshold` | no | (not stored) | Used only to derive `status` when `status` is omitted. |
| `summary` | no | `summary` | Short string. Surfaced in `evo show <id>`. |
| `failure_reason` / `failureReason` | no | `failure_reason` | Short tag. Cross-experiment scans cluster on this; keep tags stable across runs. |
| `cost` | no | `cost` | Free-form dict. Convention: `{input_tokens, output_tokens, usd, model}`. |
| `started_at` / `ended_at` | no | `started_at`, `ended_at` | ISO8601. Filled automatically when omitted. |
| `artifacts` | no | `artifacts` | `{name: path}`. Paths must be readable by the orchestrator. |
| `direction` | no | `direction` | `"max"` or `"min"`. Required when this task's optimal direction differs from the workspace metric (e.g. latency tasks in a max-accuracy benchmark). Validated; raises on other values. Also propagates to the final result's `tasks_meta`. |
| `**extra` / `...extra` | no | (merged into trace) | Any additional keys are written verbatim. Useful for benchmark-specific fields. |

**Side effects of `report()`:**

- Writes `task_<id>.json` into `EVO_TRACES_DIR` immediately (so traces survive crashes).
- Drains any prior `log(task_id, ...)` entries for this task into the trace's `log` array.
- Records the score in the Run's in-memory tally for `finish()` to aggregate.
- Records `direction` (if given) into `tasks_meta` for the final result.

**Run.finish():**

```python
run.finish(score=None)            # Python: score override is positional or keyword
```

```js
await run.finish({ score });      // Node: returns a promise
```

If `score` is omitted, `finish()` computes the mean of all reported task scores. Always call exactly once. The `with Run() as run:` / try-finally pattern in the examples above ensures it runs even on benchmark crashes.

## Gates

Gates are pass/fail commands. They must exit non-zero on regression.

Python:

```python
from evo_agent import Gate

with Gate() as gate:
    for task in critical_tasks:
        result = evaluate(task)
        gate.check(task["id"], score=result.score)
```

Node:

```javascript
import { Gate } from '@evo-hq/evo-agent';

const gate = new Gate();
for (const task of criticalTasks) {
  const result = await evaluate(task);
  gate.check(task.id, {score: result.score});
}
await gate.finish();
```

## Trace Quality Bar

After baseline, the user should be able to reconstruct a single failing task
from:

```bash
evo traces <exp_id> <task_id>
```

If not, the benchmark is under-instrumented. Add `run.log()` calls or richer
fields to `run.report()`.

For LLM-agent benchmarks, log at least:

- task input and expected outcome summary
- observation/frame summary
- prompt or message summary
- model/tool response summary
- selected action
- retries and errors
- final task outcome and score

Do not log raw secrets. If prompts contain keys/tokens, redact before logging.

## LLM-Agent Example

```python
from evo_agent import Run

run = Run()
try:
    for task in tasks:
        tid = task["id"]
        state = env.reset(task)
        messages = build_initial_messages(task)
        run.log(tid, {
            "event": "task_start",
            "task": task["name"],
            "goal": task["goal"],
            "observation": summarize_observation(state),
        })

        score = 0.0
        failure_reason = None
        for step in range(MAX_STEPS):
            run.log(tid, {
                "event": "llm_request",
                "step": step,
                "messages_summary": summarize_messages(messages),
            })
            try:
                response = model_call(messages)
            except Exception as exc:
                failure_reason = "model_error"
                run.log(tid, {"event": "llm_error", "step": step, "error": repr(exc)})
                break

            action = parse_action(response)
            run.log(tid, {
                "event": "llm_response",
                "step": step,
                "response_summary": summarize_response(response),
                "action": action,
            })

            state, reward, done, info = env.step(action)
            run.log(tid, {
                "event": "env_step",
                "step": step,
                "reward": reward,
                "done": done,
                "info": info,
                "observation": summarize_observation(state),
            })
            if done:
                score = reward
                failure_reason = None if reward > 0 else "task_failed"
                break

        run.report(tid, score=score, failure_reason=failure_reason)
finally:
    run.finish()
```

If an existing harness already writes rich recordings, decide explicitly:

- Mirror the important fields into evo traces and make evo the dashboard source
  of truth; or
- Put a clear artifact pointer in the evo trace so the user can jump to the
  existing recorder.

Do not accidentally maintain two disconnected observability systems.

## Inline Alternative

If the user chooses inline mode instead of SDK mode, use:

- `plugins/evo/skills/discover/references/inline_instrumentation.py`
- `plugins/evo/skills/discover/references/inline_instrumentation.js`

The wire protocol is the same: final result JSON at `EVO_RESULT_PATH`, traces
under `EVO_TRACES_DIR`.
