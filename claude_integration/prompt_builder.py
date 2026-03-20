import json


def build_prompt(
    user_text: str,
    thread_messages: list,
    sender_name: str,
    chat_id: str,
    root_id: str | None = None,
) -> str:
    """Build a prompt for Claude Code with pre-fetched conversation context.

    Args:
        user_text: the user's message (with bot mention stripped)
        thread_messages: list of Lark message objects from the thread/chat
        sender_name: display name or ID of the sender
        chat_id: the chat_id for MCP tool reference
        root_id: root message ID if this is a thread reply

    Returns:
        Formatted prompt string.
    """
    parts = []

    # Add conversation context if available
    if thread_messages:
        parts.append("## Conversation context from Lark chat")
        parts.append(
            "Below are recent messages from the conversation. "
            "Use this context to understand what is being discussed."
        )
        parts.append("")
        for msg in thread_messages:
            sender_id = msg.sender.id if msg.sender else "unknown"
            msg_type = msg.msg_type or "unknown"
            content = _extract_content(msg)
            parts.append(f"- {sender_id} ({msg_type}): {content}")
        parts.append("")

    # Add the user's question
    parts.append("## User's message")
    parts.append(f"From: {sender_name}")
    parts.append(f"Message: {user_text}")
    parts.append("")

    # Add context IDs for MCP tools
    parts.append("## Lark context")
    parts.append(f"Chat ID: {chat_id}")
    if root_id:
        parts.append(f"Thread root message ID: {root_id}")
    parts.append(
        "If you need more conversation context, use the lark_read_thread or "
        "lark_read_chat_history MCP tools with the chat_id above."
    )

    return "\n".join(parts)


def _extract_content(msg) -> str:
    """Extract readable content from a Lark message object."""
    if not msg.body or not msg.body.content:
        return "[no content]"
    try:
        content_data = json.loads(msg.body.content)
        msg_type = msg.msg_type or ""
        if msg_type == "text":
            return content_data.get("text", "")
        elif msg_type in ("post", "rich_text"):
            return _extract_post_text(content_data)
        else:
            return f"[{msg_type}]"
    except (json.JSONDecodeError, TypeError):
        return "[unparseable]"


def _extract_post_text(content: dict) -> str:
    """Extract text from post format."""
    parts = []
    for locale in ("zh_cn", "en_us", "ja_jp"):
        post = content.get(locale)
        if post:
            if post.get("title"):
                parts.append(post["title"])
            for paragraph in post.get("content", []):
                for elem in paragraph:
                    tag = elem.get("tag", "")
                    if tag == "text":
                        parts.append(elem.get("text", ""))
                    elif tag == "at":
                        parts.append(f"@{elem.get('user_name', 'user')}")
            break
    return " ".join(parts).strip() if parts else "[post]"
