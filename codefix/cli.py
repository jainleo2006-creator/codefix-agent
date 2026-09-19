"""CodeFix CLI - vertical slice.

Implements a real subset of the full spec's CLI (rule 42): `ask`, `doctor`,
`demo`. Everything else (`fix --all`, `review`, `security`, `rollback`, ...)
depends on the orchestrator/planner/debugger roles that this slice doesn't
build yet.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .agent import run_agent
from .providers.anthropic_provider import AnthropicProvider
from .providers.base import ErrorType, ProviderError
from .providers.mock_provider import MockProvider
from .router import NoProviderAvailableError, ProviderRouter, RoutingMode
from .state import TaskStateStore
from .tools.command_tools import RunCommandTool
from .tools.file_tools import ReadFileTool
from .tools.git_tools import GitCheckpointTool, GitDiffTool, GitRollbackTool, GitStatusTool

_AUTH_HINTS = {
    ErrorType.MISSING_API_KEY: "Set the required API key as an environment variable, then retry.",
    ErrorType.INVALID_API_KEY: "Your API key was rejected. Double-check it and retry.",
    ErrorType.INVALID_CONFIGURATION: "Fix the configuration issue above, then retry.",
}

SYSTEM_PROMPT = (
    "You are CodeFix, a software engineering assistant. You have tools to "
    "read files and run allowlisted commands in the user's project. Use "
    "read_file before making claims about code content. Be concise."
)


def _build_router(project_root: str, demo: bool) -> ProviderRouter:
    if demo:
        return ProviderRouter([MockProvider()], mode=RoutingMode.MANUAL)
    return ProviderRouter([AnthropicProvider()], mode=RoutingMode.AUTO)


def _build_tools(project_root: str) -> list:
    return [
        ReadFileTool(project_root),
        RunCommandTool(project_root),
        GitCheckpointTool(project_root),
        GitRollbackTool(project_root),
        GitStatusTool(project_root),
        GitDiffTool(project_root),
    ]


def _task_store_for(project_root: str) -> TaskStateStore:
    return TaskStateStore(Path(project_root) / ".codefix" / "codefix.db")


def cmd_ask(args: argparse.Namespace) -> int:
    project_root = args.project or "."
    router = _build_router(project_root, demo=args.provider == "mock")
    tools = _build_tools(project_root)
    task_store = _task_store_for(project_root)

    try:
        result = run_agent(
            router,
            tools,
            user_prompt=args.prompt,
            system=SYSTEM_PROMPT,
            provider_name=None if args.provider == "auto" else args.provider,
            max_iterations=args.max_iterations,
            task_store=task_store,
            task_id=args.resume,
            project=project_root,
        )
    except ProviderError as exc:
        print(f"\u274c {exc.provider} failed.\n")
        print(f"Reason:\n{exc}\n")
        hint = _AUTH_HINTS.get(exc.error_type)
        if hint:
            print(f"Fix:\n{hint}")
        last_task = task_store.list(project=project_root)
        if last_task:
            print(f"\nProgress was saved. Resume once fixed with:\n"
                  f"  codefix ask \"{args.prompt}\" --project {project_root} --resume {last_task[0].task_id}")
        return 1
    except NoProviderAvailableError as exc:
        print(f"\u274c No provider could handle this request.\n\n{exc}")
        last_task = task_store.list(project=project_root)
        if last_task:
            print(f"\nProgress was saved. Resume once providers are back with:\n"
                  f"  codefix ask \"{args.prompt}\" --project {project_root} --resume {last_task[0].task_id}")
        return 1
    except ValueError as exc:
        print(f"\u274c {exc}")
        return 1

    print(result.final_text)
    print()
    print(f"--- task: {result.task_id} | status: {result.status} | turns: {result.turns} ---")
    print(f"--- providers used: {result.provider_history} ---")
    if result.tool_calls_made:
        print(f"--- tools called: {result.tool_calls_made} ---")
    if result.failover_events:
        print("--- failover events ---")
        for e in result.failover_events:
            print(f"  ⚠ {e}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    print("Running CodeFix in DEMO MODE (mock provider, no API key, no network calls).")
    print("This shows the agent loop and tool-calling wiring - not real model output.\n")
    args.provider = "mock"
    args.project = args.project or "sample_project"
    args.prompt = args.prompt or "Look at app.py and tell me what it does."
    args.max_iterations = 4
    return cmd_ask(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    py_ok = sys.version_info >= (3, 10)
    checks.append(("Python >= 3.10", py_ok, sys.version.split()[0]))

    git_path = shutil.which("git")
    checks.append(("Git installed", git_path is not None, git_path or "not found"))

    try:
        import anthropic  # noqa: F401

        checks.append(("anthropic package installed", True, ""))
    except ImportError:
        checks.append(("anthropic package installed", False, "pip install anthropic"))

    anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    checks.append(
        (
            "ANTHROPIC_API_KEY configured",
            anthropic_key,
            "set" if anthropic_key else "not set - real provider calls will fail",
        )
    )

    openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    checks.append(
        (
            "OPENAI_API_KEY configured",
            openai_key,
            "set" if openai_key else "not set (optional in this slice)",
        )
    )

    print("CODEFIX DOCTOR\n")
    all_critical_ok = True
    for label, ok, detail in checks:
        symbol = "✓" if ok else "⚠"
        print(f"{symbol} {label}{f' ({detail})' if detail else ''}")
        if not ok and label in ("Python >= 3.10", "Git installed"):
            all_critical_ok = False

    print()
    if not anthropic_key:
        print("No real provider is configured. You can still run: codefix demo")
    return 0 if all_critical_ok else 1


def cmd_tasks(args: argparse.Namespace) -> int:
    project_root = args.project or "."
    store = _task_store_for(project_root)
    tasks = store.list(project=project_root)
    if not tasks:
        print("No saved tasks for this project.")
        return 0
    for t in tasks:
        print(f"{t.task_id}  [{t.status:12s}]  phase={t.current_phase:28s}  \"{t.user_request[:50]}\"")
        print(f"           files_inspected={t.files_inspected}  providers={t.provider_history}")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    project_root = args.project or "."
    tool = GitCheckpointTool(project_root)
    result = tool.execute(label=args.label)
    if result.success:
        print(f"\u2705 {result.output}")
        return 0
    print(f"\u274c {result.error}")
    return 1


def cmd_rollback(args: argparse.Namespace) -> int:
    project_root = args.project or "."
    tool = GitRollbackTool(project_root)
    result = tool.execute(commit_hash=args.commit_hash)
    if result.success:
        print(f"\u2705 {result.output}")
        return 0
    print(f"\u274c {result.error}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codefix", description="CodeFix Agent (vertical slice)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="Ask the agent to do something in a project")
    p_ask.add_argument("prompt", help="Natural-language task")
    p_ask.add_argument("--project", default=".", help="Project root directory")
    p_ask.add_argument("--provider", default="auto", choices=["auto", "anthropic", "mock"])
    p_ask.add_argument("--max-iterations", type=int, default=8)
    p_ask.add_argument("--resume", default=None, help="Resume a previous task by its task_id")
    p_ask.set_defaults(func=cmd_ask)

    p_demo = sub.add_parser("demo", help="Run a zero-config demo (no API key needed)")
    p_demo.add_argument("prompt", nargs="?", default=None)
    p_demo.add_argument("--project", default=None)
    p_demo.set_defaults(func=cmd_demo)

    p_doctor = sub.add_parser("doctor", help="Check environment and configuration")
    p_doctor.set_defaults(func=cmd_doctor)

    p_tasks = sub.add_parser("tasks", help="List saved task state for a project")
    p_tasks.add_argument("--project", default=".")
    p_tasks.set_defaults(func=cmd_tasks)

    p_checkpoint = sub.add_parser("checkpoint", help="Create a git checkpoint of the project")
    p_checkpoint.add_argument("label", nargs="?", default="manual checkpoint")
    p_checkpoint.add_argument("--project", default=".")
    p_checkpoint.set_defaults(func=cmd_checkpoint)

    p_rollback = sub.add_parser("rollback", help="Roll back the project to a checkpoint commit")
    p_rollback.add_argument("commit_hash")
    p_rollback.add_argument("--project", default=".")
    p_rollback.set_defaults(func=cmd_rollback)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
