# CodeFix Agent — Vertical Slice

This is a **working slice** of the full CodeFix Agent spec, not a mockup. Every
piece here actually executes: real file I/O, real subprocess execution, real
Anthropic API calls, real error classification and failover. Nothing is
faked or simulated.

## What's implemented

- `codefix/providers/base.py` — canonical `AIProvider` interface, `AgentResponse`,
  `ToolCall`, `Capabilities`, and `ErrorType` normalization
- `codefix/providers/anthropic_provider.py` — real Anthropic adapter (network calls,
  real error classification from SDK exceptions)
- `codefix/providers/mock_provider.py` — deterministic offline provider for
  `codefix demo` and tests (clearly labeled as demo mode, never pretends to be real)
- `codefix/router.py` — MANUAL / AUTO routing with real failover: retryable
  errors (rate limits, timeouts, server errors) fail over to the next
  provider; auth/config errors surface immediately instead of wasting calls
- `codefix/state.py` — **task-state engine** (spec section 10): SQLite-backed
  persistence of the full conversation + progress after every turn, so a
  failed or interrupted task can be resumed exactly where it left off
  instead of restarting
- `codefix/tools/file_tools.py` — `read_file` with path-traversal protection
- `codefix/tools/command_tools.py` — `run_command` with an allowlist, denylist,
  timeout, and output truncation
- `codefix/tools/git_tools.py` — **real git checkpoint/rollback/status/diff**
  (spec sections 21, 24, 25), verified against an actual temp git repo in tests
- `codefix/agent.py` — bounded tool-calling loop (max_iterations, default 8),
  persists `TaskState` after every turn, resumable via `task_id`
- `codefix/cli.py` — `codefix ask [--resume TASK_ID]`, `codefix doctor`,
  `codefix demo`, `codefix tasks`, `codefix checkpoint`, `codefix rollback`
- `tests/` — 28 tests, all runnable without any API key

## What's NOT implemented (see the full spec for these)

Planner/Coder/Debugger/Reviewer role split, context/token budgeting and
summarization, Streamlit UI, SMART routing (cost/latency aware), GitHub
integration, security/dependency engines, `fix --all`, permission-mode
confirmation flow, PostgreSQL support, and packaging for distribution beyond
local `pip install -e .`. This slice exists to prove the provider/router/
tool/agent-loop/task-state architecture works end-to-end before building
those larger pieces on top of it.

## Try it

```bash
pip install -e .

# No API key needed:
codefix doctor
codefix demo

# Real provider (requires ANTHROPIC_API_KEY):
export ANTHROPIC_API_KEY=sk-ant-...
codefix ask "what does app.py do?" --project sample_project

# Git safety, for real, on your actual repo:
codefix checkpoint "before risky change" --project .
codefix rollback <commit_hash> --project .

# See saved task state (including failed/interrupted tasks):
codefix tasks --project .

# Resume a task that failed partway through (e.g. after a rate limit):
codefix ask "..." --project . --resume <task_id>

# Run the tests (no network / API key required):
pytest tests/ -v
```

## Architecture notes

The mock provider isn't a placeholder for the real one — it's a permanent
part of the design, used for offline tests and the zero-config demo path
(spec rules 37, 50, 52: tests and demos must not require paid API access,
and must never fake real provider output).

Task state is persisted as a JSON blob per row in SQLite rather than a fully
normalized schema — a deliberate simplification while the spec's `TaskState`
shape is still evolving; it still gives real persistence, real
resumability, and real queryability by task_id/status/project.

Git rollback uses `git reset --hard` against a checkpoint commit. This is
honest about its limits: it restores tracked files to their checkpointed
content, but cannot resurrect anything that was never committed. Untracked
files are left alone either way, so it won't destroy work outside git's view
— but "checkpoint" here means "committed", not "everything on disk."
