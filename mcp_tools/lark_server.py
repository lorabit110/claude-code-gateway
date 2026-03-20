#!/usr/bin/env python3
"""MCP stdio server exposing Lark API tools for Claude Code.

This server lets Claude proactively fetch more conversation context
from Lark when needed.

Expects LARK_APP_ID and LARK_APP_SECRET environment variables.
"""

import json
import os
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import ListMessageRequest
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("lark-tools")

# Create a Lark client from env vars (set by the parent process)
_client = None


def _get_client() -> lark.Client:
    global _client
    if _client is None:
        app_id = os.environ["LARK_APP_ID"]
        app_secret = os.environ["LARK_APP_SECRET"]
        domain = os.getenv("LARK_DOMAIN", "https://open.larksuite.com")
        _client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
    return _client


def _format_messages(items: list) -> str:
    """Format a list of Lark message objects into readable text."""
    if not items:
        return "No messages found."

    lines = []
    for msg in items:
        sender_id = msg.sender.id if msg.sender else "unknown"
        msg_type = msg.msg_type or "unknown"
        content = ""
        if msg.body and msg.body.content:
            try:
                content_data = json.loads(msg.body.content)
                if msg_type == "text":
                    content = content_data.get("text", "")
                elif msg_type in ("post", "rich_text"):
                    content = _extract_post_text(content_data)
                else:
                    content = f"[{msg_type}]"
            except (json.JSONDecodeError, TypeError):
                content = "[unparseable]"

        # Include mention names if available
        mention_names = ""
        if msg.mentions:
            names = [m.name for m in msg.mentions if m.name]
            if names:
                mention_names = f" (mentions: {', '.join(names)})"

        create_time = msg.create_time or ""
        lines.append(
            f"[{create_time}] {sender_id} ({msg_type}){mention_names}: {content}"
        )

    return "\n".join(lines)


def _extract_post_text(content: dict) -> str:
    """Extract text from post/rich_text message format."""
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
                    elif tag == "a":
                        parts.append(elem.get("text", elem.get("href", "")))
            break
    return " ".join(parts).strip()


@mcp.tool()
def lark_read_thread(chat_id: str, limit: int = 50) -> str:
    """Read messages in a Lark chat or thread.

    Use this to get the conversation context from a Lark group chat.

    Args:
        chat_id: The chat_id (starts with oc_) or thread_id
        limit: Maximum number of messages to fetch (1-50, default 50)
    """
    client = _get_client()
    request = (
        ListMessageRequest.builder()
        .container_id_type("chat")
        .container_id(chat_id)
        .sort_type("ByCreateTimeAsc")
        .page_size(min(limit, 50))
        .build()
    )
    response = client.im.v1.message.list(request)
    if not response.success():
        return f"Error fetching messages: code={response.code}, msg={response.msg}"
    return _format_messages(response.data.items or [])


@mcp.tool()
def lark_read_chat_history(chat_id: str, limit: int = 20) -> str:
    """Read recent chat history from a Lark group, sorted newest first.

    Use this to see what was recently discussed in the chat.

    Args:
        chat_id: The chat_id (starts with oc_)
        limit: Maximum number of messages to fetch (1-50, default 20)
    """
    client = _get_client()
    request = (
        ListMessageRequest.builder()
        .container_id_type("chat")
        .container_id(chat_id)
        .sort_type("ByCreateTimeDesc")
        .page_size(min(limit, 50))
        .build()
    )
    response = client.im.v1.message.list(request)
    if not response.success():
        return f"Error fetching messages: code={response.code}, msg={response.msg}"
    items = response.data.items or []
    # Reverse so messages read chronologically
    return _format_messages(list(reversed(items)))


@mcp.tool()
def lark_get_message(message_id: str) -> str:
    """Read a specific Lark message by its message_id.

    Args:
        message_id: The message ID (starts with om_)
    """
    client = _get_client()
    # Use the generic request API since get_message may not be in the typed SDK
    from lark_oapi.core.enum import HttpMethod, AccessTokenType

    request = (
        lark.BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri(f"/open-apis/im/v1/messages/{message_id}")
        .token_types({AccessTokenType.TENANT})
        .build()
    )
    response = client.request(request)
    if not response.success():
        return f"Error fetching message: code={response.code}, msg={response.msg}"

    data = json.loads(response.raw.content)
    items = data.get("data", {}).get("items", [])
    if not items:
        return "Message not found."

    msg = items[0]
    msg_type = msg.get("msg_type", "unknown")
    content = ""
    body = msg.get("body", {})
    if body.get("content"):
        try:
            content_data = json.loads(body["content"])
            if msg_type == "text":
                content = content_data.get("text", "")
            else:
                content = json.dumps(content_data, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            content = body.get("content", "")

    sender = msg.get("sender", {})
    return (
        f"Message {message_id}:\n"
        f"  Sender: {sender.get('id', 'unknown')}\n"
        f"  Type: {msg_type}\n"
        f"  Content: {content}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
