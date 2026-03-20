import json
import re
import logging

logger = logging.getLogger(__name__)


def is_bot_mentioned(mentions, bot_open_id: str) -> bool:
    """Check if the bot was @mentioned in the message.

    Args:
        mentions: list of MentionEvent objects from the event message
        bot_open_id: the bot's open_id

    Returns:
        True if the bot was mentioned.
    """
    if not mentions:
        return False
    for m in mentions:
        if m.id and m.id.open_id == bot_open_id:
            return True
    return False


def extract_text(content_json: str, msg_type: str) -> str:
    """Extract plain text from a Lark message content JSON string.

    Supports text, post, and rich_text message types.

    Args:
        content_json: JSON string of the message content
        msg_type: the message type (text, post, rich_text, etc.)

    Returns:
        Extracted plain text.
    """
    try:
        content = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    if msg_type == "text":
        return content.get("text", "")

    if msg_type == "post":
        return _extract_post_text(content)

    if msg_type == "rich_text":
        return _extract_post_text(content)

    # For unsupported types, return a placeholder
    return f"[{msg_type} message]"


def _extract_post_text(content: dict) -> str:
    """Extract text from post/rich_text format.

    Post content structure:
    {
        "zh_cn": {
            "title": "...",
            "content": [[{"tag": "text", "text": "..."}, {"tag": "at", "user_id": "..."}]]
        }
    }
    """
    parts = []
    # Try common locales
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
                    elif tag == "a":
                        parts.append(elem.get("text", elem.get("href", "")))
            break  # Use first available locale
    return " ".join(parts).strip()


def strip_bot_mention(text: str, bot_name: str) -> str:
    """Remove @BotName and @_user_N placeholders from the user's message.

    Args:
        text: the raw message text
        bot_name: the bot's display name

    Returns:
        Cleaned text with bot mentions removed.
    """
    # Remove @_user_N style placeholders (these are mention keys)
    cleaned = re.sub(r"@_user_\d+", "", text)
    # Also remove @BotName if present
    if bot_name:
        cleaned = cleaned.replace(f"@{bot_name}", "")
    return cleaned.strip()
