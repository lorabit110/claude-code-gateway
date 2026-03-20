import json


# Max length for plain text replies; longer responses use an interactive card
_PLAIN_TEXT_LIMIT = 500


def format_response(text: str) -> tuple[str, str]:
    """Format Claude's response for Lark.

    Uses an interactive card with markdown for long responses,
    plain text for short ones.

    Args:
        text: the response text from Claude

    Returns:
        (msg_type, content_json) tuple ready for reply_message.
    """
    if len(text) <= _PLAIN_TEXT_LIMIT:
        return "text", json.dumps({"text": text})

    card = _build_markdown_card(text)
    return "interactive", json.dumps(card)


def _build_markdown_card(text: str) -> dict:
    """Build a Lark interactive card with markdown content."""
    return {
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }


def format_error(error_msg: str) -> tuple[str, str]:
    """Format an error message for Lark.

    Returns:
        (msg_type, content_json) tuple.
    """
    return "text", json.dumps({"text": f"Sorry, something went wrong: {error_msg}"})
