import json
import logging
import os
import subprocess
import tempfile

from config import Config

logger = logging.getLogger(__name__)

# Directory of this file, used to locate mcp_tools/lark_server.py
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_SERVER_PATH = os.path.join(_PROJECT_ROOT, "mcp_tools", "lark_server.py")

_SYSTEM_PROMPT = (
    "You are a helpful assistant in a Lark (Feishu) group chat. "
    "Answer the user's question based on the conversation context provided. "
    "If you need more context from the conversation, use the Lark MCP tools. "
    "Keep responses concise and helpful. "
    "Respond in the same language as the user's message."
)


def _generate_mcp_config(config: Config) -> str:
    """Generate a temporary MCP config JSON file.

    Returns the path to the generated config file.
    """
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


def invoke_claude(
    prompt: str, config: Config, session_id: str | None = None
) -> tuple[str, str, str]:
    """Invoke Claude Code CLI with the given prompt.

    Runs `claude -p` as a subprocess with MCP tools configured.
    Supports session resumption for persistent conversations.
    Creates a temporary working directory so Claude can write files.

    Args:
        prompt: the full prompt to send to Claude
        config: application config
        session_id: optional session ID to resume a previous conversation

    Returns:
        (response_text, session_id, work_dir) tuple.
        Caller is responsible for cleaning up work_dir.

    Raises:
        RuntimeError: if the subprocess fails or times out.
    """
    mcp_config_path = _generate_mcp_config(config)
    work_dir = tempfile.mkdtemp(prefix="claude_work_")

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--model", config.claude_model,
        "--max-turns", str(config.claude_max_turns),
        "--mcp-config", mcp_config_path,
        "--allowedTools",
        "mcp__lark-tools__lark_read_thread,"
        "mcp__lark-tools__lark_read_chat_history,"
        "mcp__lark-tools__lark_get_message,"
        "WebSearch,WebFetch,Write",
        "--append-system-prompt", _SYSTEM_PROMPT,
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    logger.info(
        "Invoking Claude Code CLI (session=%s, work_dir=%s)...",
        session_id or "new",
        work_dir,
    )

    # Clear CLAUDECODE env var to allow nested invocation
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.claude_timeout,
            cwd=work_dir,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Claude Code timed out after {config.claude_timeout}s"
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Claude Code CLI not found. Make sure 'claude' is in your PATH."
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("Claude Code failed (rc=%d): %s", result.returncode, stderr)
        raise RuntimeError(f"Claude Code failed: {stderr or 'unknown error'}")

    # Parse JSON output
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("Claude Code returned empty output")

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("Claude output was not valid JSON, returning raw")
        return stdout, session_id or "", work_dir

    # Extract response text and session ID
    new_session_id = ""
    response_text = ""

    if isinstance(output, dict):
        new_session_id = output.get("session_id", session_id or "")
        if "result" in output:
            response_text = output["result"]
        elif "content" in output:
            response_text = output["content"]
        else:
            response_text = json.dumps(output, ensure_ascii=False)
    else:
        response_text = str(output)

    logger.info("Claude responded (session=%s)", new_session_id)
    return response_text, new_session_id, work_dir
