import subprocess

import pytest

from codefix.tools.git_tools import GitCheckpointTool, GitDiffTool, GitRollbackTool, GitStatusTool


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_checkpoint_requires_git_repo(tmp_path):
    tool = GitCheckpointTool(tmp_path)
    result = tool.execute(label="x")
    assert not result.success
    assert "not a git repository" in result.error.lower()


def test_checkpoint_and_rollback_real_commit(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    # Make a change and checkpoint it.
    (tmp_path / "file.txt").write_text("v2")
    checkpoint_tool = GitCheckpointTool(tmp_path)
    result = checkpoint_tool.execute(label="v2 checkpoint")
    assert result.success
    commit_hash = result.output.split()[2]
    assert (tmp_path / "file.txt").read_text() == "v2"

    # Make another change after the checkpoint.
    (tmp_path / "file.txt").write_text("v3 - broken")

    # Rollback to the checkpoint and verify the real file content changes back.
    rollback_tool = GitRollbackTool(tmp_path)
    rb_result = rollback_tool.execute(commit_hash=commit_hash)
    assert rb_result.success
    assert (tmp_path / "file.txt").read_text() == "v2"


def test_checkpoint_with_no_changes_reports_existing_head(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    tool = GitCheckpointTool(tmp_path)
    result = tool.execute(label="no-op")
    assert result.success
    assert "No changes" in result.output


def test_rollback_rejects_unknown_hash(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    tool = GitRollbackTool(tmp_path)
    result = tool.execute(commit_hash="deadbeef")
    assert not result.success
    assert "unknown commit" in result.error.lower()


def test_git_status_and_diff_reflect_real_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("v1")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    (tmp_path / "file.txt").write_text("v1\nv2 line")

    status_tool = GitStatusTool(tmp_path)
    status_result = status_tool.execute()
    assert status_result.success
    assert "file.txt" in status_result.output

    diff_tool = GitDiffTool(tmp_path)
    diff_result = diff_tool.execute()
    assert diff_result.success
    assert "v2 line" in diff_result.output
