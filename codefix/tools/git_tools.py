"""Git safety tools (spec sections 21, 24, 25). Real `git` subprocess calls,
no simulated output.

Honesty about the limits of this slice's rollback: `git reset --hard`
affects tracked files only. Files that were untracked at checkpoint time
and are still untracked are not touched by rollback (git never has
authority over them), so this does not "silently destroy" work the way a
raw `rm` would - but it also cannot restore something that was never
committed. That tradeoff is documented, not hidden.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Tool, ToolResult


def _run_git(args: list[str], cwd: Path, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def _is_git_repo(cwd: Path) -> bool:
    try:
        result = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


class GitCheckpointTool(Tool):
    name = "git_checkpoint"
    description = (
        "Create a git checkpoint of the current working tree so changes can be "
        "rolled back later. Returns the checkpoint's commit hash."
    )
    parameters = {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "Short label for this checkpoint."}},
        "required": [],
    }

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def execute(self, label: str = "checkpoint") -> ToolResult:
        if not _is_git_repo(self.project_root):
            return ToolResult(
                success=False, output="", error="Not a git repository. Run 'git init' first."
            )

        add = _run_git(["add", "-A"], self.project_root)
        if add.returncode != 0:
            return ToolResult(success=False, output="", error=f"git add failed: {add.stderr.strip()}")

        diff_check = _run_git(["diff", "--cached", "--quiet"], self.project_root)
        if diff_check.returncode == 0:
            # Nothing staged - working tree already matches HEAD.
            head = _run_git(["rev-parse", "HEAD"], self.project_root)
            if head.returncode != 0:
                return ToolResult(
                    success=False,
                    output="",
                    error="No changes to checkpoint and no existing commits (empty repo).",
                )
            commit_hash = head.stdout.strip()
            return ToolResult(
                success=True,
                output=f"No changes since last commit. Checkpoint = existing HEAD {commit_hash}",
            )

        commit = _run_git(["commit", "-m", f"[codefix checkpoint] {label}"], self.project_root)
        if commit.returncode != 0:
            return ToolResult(
                success=False, output="", error=f"git commit failed: {commit.stderr.strip()}"
            )

        head = _run_git(["rev-parse", "HEAD"], self.project_root)
        commit_hash = head.stdout.strip()
        return ToolResult(success=True, output=f"Checkpoint created: {commit_hash} ({label})")


class GitRollbackTool(Tool):
    name = "git_rollback"
    description = (
        "Roll the working tree back to a previous checkpoint commit hash. "
        "DESTRUCTIVE: any tracked-file changes made since that commit are discarded."
    )
    parameters = {
        "type": "object",
        "properties": {"commit_hash": {"type": "string", "description": "Checkpoint commit hash."}},
        "required": ["commit_hash"],
    }

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def execute(self, commit_hash: str) -> ToolResult:
        if not _is_git_repo(self.project_root):
            return ToolResult(success=False, output="", error="Not a git repository.")

        verify = _run_git(["cat-file", "-e", commit_hash], self.project_root)
        if verify.returncode != 0:
            return ToolResult(success=False, output="", error=f"Unknown commit: {commit_hash}")

        reset = _run_git(["reset", "--hard", commit_hash], self.project_root)
        if reset.returncode != 0:
            return ToolResult(success=False, output="", error=f"git reset failed: {reset.stderr.strip()}")

        return ToolResult(success=True, output=f"Rolled back to {commit_hash}.\n{reset.stdout.strip()}")


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the current git status (real 'git status --porcelain -b')."
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def execute(self) -> ToolResult:
        if not _is_git_repo(self.project_root):
            return ToolResult(success=False, output="", error="Not a git repository.")
        result = _run_git(["status", "--porcelain=v1", "-b"], self.project_root)
        return ToolResult(success=result.returncode == 0, output=result.stdout, error=result.stderr or None)


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show the current git diff (real 'git diff'), for reviewing changes before finishing."
    parameters = {
        "type": "object",
        "properties": {"staged": {"type": "boolean", "description": "Show staged diff instead."}},
        "required": [],
    }

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def execute(self, staged: bool = False) -> ToolResult:
        if not _is_git_repo(self.project_root):
            return ToolResult(success=False, output="", error="Not a git repository.")
        args = ["diff"] + (["--cached"] if staged else [])
        result = _run_git(args, self.project_root)
        return ToolResult(success=result.returncode == 0, output=result.stdout, error=result.stderr or None)
