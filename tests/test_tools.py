from pathlib import Path

from codefix.tools.command_tools import RunCommandTool
from codefix.tools.file_tools import ReadFileTool


def test_read_file_reads_real_content(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')")
    tool = ReadFileTool(tmp_path)
    result = tool.execute(path="hello.py")
    assert result.success
    assert "print" in result.output


def test_read_file_blocks_path_traversal(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")
    tool = ReadFileTool(tmp_path)
    result = tool.execute(path="../secret.txt")
    assert not result.success
    assert "Refused" in result.error


def test_read_file_missing_file(tmp_path):
    tool = ReadFileTool(tmp_path)
    result = tool.execute(path="nope.py")
    assert not result.success
    assert "not found" in result.error.lower()


def test_run_command_allows_allowlisted(tmp_path):
    tool = RunCommandTool(tmp_path)
    result = tool.execute(command='python3 -c "print(1 + 1)"')
    assert result.success
    assert result.output.strip() == "2"


def test_run_command_blocks_non_allowlisted(tmp_path):
    tool = RunCommandTool(tmp_path)
    result = tool.execute(command="curl http://example.com")
    assert not result.success
    assert "not in the allowlist" in result.error


def test_run_command_blocks_denylisted_pattern(tmp_path):
    tool = RunCommandTool(tmp_path)
    result = tool.execute(command="git status; rm -rf /")
    assert not result.success
    assert "denylist" in result.error.lower()


def test_run_command_times_out(tmp_path):
    tool = RunCommandTool(tmp_path)
    result = tool.execute(command='python3 -c "import time; time.sleep(5)"', timeout_seconds=1)
    assert not result.success
    assert "timed out" in result.error.lower()
