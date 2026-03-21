import json
import logging
import os
import subprocess
import threading
from typing import Generator

from config import Config

logger = logging.getLogger(__name__)

# Thread-safe registry of running claude processes per chat_id
_running_processes: dict[str, subprocess.Popen] = {}
_process_lock = threading.Lock()


def _register_process(chat_id: str, proc: subprocess.Popen) -> None:
    with _process_lock:
        _running_processes[chat_id] = proc


def _unregister_process(chat_id: str) -> None:
    with _process_lock:
        _running_processes.pop(chat_id, None)


def stop_claude(chat_id: str) -> bool:
    """Stop the running Claude process for a chat. Returns True if a process was stopped."""
    with _process_lock:
        proc = _running_processes.pop(chat_id, None)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("Stopped Claude process for chat %s", chat_id)
        return True
    return False


# Directory of this file, used to locate mcp_tools/lark_server.py
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_SERVER_PATH = os.path.join(_PROJECT_ROOT, "mcp_tools", "lark_server.py")
_WORKSPACES_ROOT = os.path.join(_PROJECT_ROOT, "workspaces")

_SYSTEM_PROMPT_FILE = os.path.join(_PROJECT_ROOT, "system_prompt.txt")
_WORKSPACE_PROMPT_FILE = "WORKSPACE.md"


def _load_system_prompt(work_dir: str) -> str:
    """Load the system prompt from file, appending WORKSPACE.md if it exists."""
    prompt = ""
    if os.path.isfile(_SYSTEM_PROMPT_FILE):
        with open(_SYSTEM_PROMPT_FILE) as f:
            prompt = f.read().strip()

    workspace_prompt_path = os.path.join(work_dir, _WORKSPACE_PROMPT_FILE)
    if os.path.isfile(workspace_prompt_path):
        with open(workspace_prompt_path) as f:
            workspace_prompt = f.read().strip()
        if workspace_prompt:
            prompt += f"\n\nWORKSPACE.md instructions:\n{workspace_prompt}"

    return prompt


def _generate_mcp_config(config: Config) -> str:
    """Generate a temporary MCP config JSON file."""
    mcp_config = {
        "mcpServers": {
            "lark-tools": {
                "command": "python3",
                "args": [_MCP_SERVER_PATH],
                "env": {
                    "LARK_APP_ID": config.lark_app_id,
                    "LARK_APP_SECRET": config.lark_app_secret,
                    "LARK_DOMAIN": config.lark_domain,
                },
            }
        }
    }
    config_path = os.path.join(_PROJECT_ROOT, ".mcp_config.json")
    with open(config_path, "w") as f:
        json.dump(mcp_config, f)
    return config_path


def _get_workspace(chat_id: str) -> str:
    """Get or create a persistent workspace directory for a chat."""
    workspace = os.path.join(_WORKSPACES_ROOT, chat_id)
    os.makedirs(workspace, exist_ok=True)
    return workspace


def _build_cmd(
    prompt: str, config: Config, mcp_config_path: str,
    system_prompt: str, session_id: str | None, streaming: bool,
) -> list[str]:
    """Build the claude CLI command."""
    output_format = "stream-json" if streaming else "json"

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", output_format,
        "--model", config.claude_model,
        "--max-turns", str(config.claude_max_turns),
        "--mcp-config", mcp_config_path,
        "--dangerously-skip-permissions",
        "--append-system-prompt", system_prompt,
    ]

    if streaming:
        cmd.append("--verbose")

    if session_id:
        cmd.extend(["--resume", session_id])

    return cmd


def _get_env() -> dict:
    """Get environment for claude subprocess."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _extract_tool_name(content: list | str) -> str | None:
    """Extract tool name from an assistant message's content blocks."""
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("name")
    return None


def _extract_text(content: list | str) -> str | None:
    """Extract text from an assistant message's content blocks."""
    if not isinstance(content, list):
        return str(content) if content else None
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                texts.append(text)
    return "\n".join(texts) if texts else None


def _stream_proc(proc: subprocess.Popen, session_id: str | None) -> Generator[dict, None, None]:
    """Read streaming JSON from a claude process and yield events."""
    last_tool = None

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "assistant":
            msg = event.get("message", {})
            content = msg.get("content", "")
            tool = _extract_tool_name(content)
            if tool and tool != last_tool:
                last_tool = tool
                yield {"type": "progress", "tool": tool}
            text = _extract_text(content)
            if text:
                yield {"type": "text", "content": text}

        elif event_type == "result":
            yield {
                "type": "result",
                "content": event.get("result", ""),
                "session_id": event.get("session_id", session_id or ""),
            }

    proc.wait()

    # Process was terminated (interrupted)
    if proc.returncode and proc.returncode < 0:
        yield {"type": "interrupted"}


def invoke_claude_streaming(
    prompt: str, config: Config, chat_id: str, session_id: str | None = None
) -> Generator[dict, None, None]:
    """Invoke Claude Code CLI with streaming output.

    Yields event dicts with these types:
        {"type": "progress", "tool": "ToolName"}  — Claude is using a tool
        {"type": "text", "content": "..."}         — Claude produced text
        {"type": "result", "content": "...", "session_id": "...", "work_dir": "..."}
        {"type": "interrupted"}                    — Process was stopped by user
        {"type": "error", "message": "..."}

    Falls back to a new session if resumption fails.
    """
    mcp_config_path = _generate_mcp_config(config)
    work_dir = _get_workspace(chat_id)
    system_prompt = _load_system_prompt(work_dir)
    env = _get_env()

    cmd = _build_cmd(prompt, config, mcp_config_path, system_prompt, session_id, streaming=True)

    logger.info("Invoking Claude Code CLI streaming (session=%s, work_dir=%s)...", session_id or "new", work_dir)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=work_dir, env=env,
        )
    except FileNotFoundError:
        yield {"type": "error", "message": "Claude Code CLI not found. Make sure 'claude' is in your PATH."}
        return

    _register_process(chat_id, proc)
    got_result = False

    try:
        for event in _stream_proc(proc, session_id):
            if event["type"] == "result":
                event["work_dir"] = work_dir
                got_result = True
                yield event
                return
            elif event["type"] == "interrupted":
                yield event
                return
            else:
                yield event
    finally:
        _unregister_process(chat_id)

    # If we get here without a result, process failed
    if proc.returncode != 0 and session_id:
        # Stale session — retry without resume
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        logger.warning("Session %s failed, retrying as new session: %s", session_id, stderr)

        cmd = _build_cmd(prompt, config, mcp_config_path, system_prompt, None, streaming=True)
        try:
            proc2 = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=work_dir, env=env,
            )
        except FileNotFoundError:
            yield {"type": "error", "message": "Claude Code CLI not found."}
            return

        _register_process(chat_id, proc2)
        try:
            for event in _stream_proc(proc2, None):
                if event["type"] == "result":
                    event["work_dir"] = work_dir
                    yield event
                    return
                elif event["type"] == "interrupted":
                    yield event
                    return
                else:
                    yield event

            if proc2.returncode != 0:
                stderr = proc2.stderr.read().strip() if proc2.stderr else ""
                yield {"type": "error", "message": f"Claude Code failed: {stderr or 'unknown error'}"}
        finally:
            _unregister_process(chat_id)

    elif proc.returncode != 0:
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        yield {"type": "error", "message": f"Claude Code failed: {stderr or 'unknown error'}"}
