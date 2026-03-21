import json


def format_response(text: str) -> tuple[str, str]:
    """Format Claude's response for Lark as an interactive card with markdown.

    Args:
        text: the response text from Claude

    Returns:
        (msg_type, content_json) tuple ready for reply_message.
    """
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
    """Format an error message for Lark as an interactive card.

    Returns:
        (msg_type, content_json) tuple.
    """
    card = _build_markdown_card(f"Sorry, something went wrong: {error_msg}")
    return "interactive", json.dumps(card)
