import json
import logging
import os
import threading
import time
from collections import OrderedDict

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from bot.message_formatter import format_error, format_response
from bot.message_parser import extract_text, is_bot_mentioned, strip_bot_mention
from claude_integration.invoker import invoke_claude_streaming, stop_claude, _get_workspace
from claude_integration.prompt_builder import build_prompt
from config import Config
from lark_client.message_api import (
    add_reaction,
    download_message_resource,
    list_messages,
    remove_reaction,
    reply_message,
    reply_message_with_id,
    update_message,
)

logger = logging.getLogger(__name__)

# Emoji used as a "thinking" indicator while Claude processes
_THINKING_EMOJI = "OnIt"

# Minimum interval between Lark message updates (seconds)
_UPDATE_INTERVAL = 3


class _DeduplicateCache:
    """Simple LRU cache for message deduplication."""

    def __init__(self, maxsize: int = 1000):
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def seen(self, key: str) -> bool:
        """Return True if key was already seen (and mark it as seen)."""
        with self._lock:
            if key in self._cache:
                return True
            self._cache[key] = time.time()
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
            return False


class _SessionStore:
    """Thread-safe mapping of chat_id → Claude session_id, persisted to disk."""

    _SESSION_FILE = "session.json"

    def __init__(self):
        self._lock = threading.Lock()

    def _session_path(self, chat_id: str) -> str:
        workspace = _get_workspace(chat_id)
        return os.path.join(workspace, self._SESSION_FILE)

    def get(self, chat_id: str) -> str | None:
        with self._lock:
            path = self._session_path(chat_id)
            if os.path.isfile(path):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    return data.get("session_id")
                except (json.JSONDecodeError, OSError):
                    return None
            return None

    def set(self, chat_id: str, session_id: str) -> None:
        with self._lock:
            path = self._session_path(chat_id)
            with open(path, "w") as f:
                json.dump({"session_id": session_id}, f)

    def clear(self, chat_id: str) -> None:
        with self._lock:
            path = self._session_path(chat_id)
            if os.path.isfile(path):
                os.remove(path)


def _build_progress_card(status_lines: list[str], final: bool = False) -> str:
    """Build a Lark interactive card showing progress or final result."""
    content = "\n".join(status_lines)
    return json.dumps({
        "elements": [
            {
                "tag": "markdown",
                "content": content,
            }
        ],
    })


def create_event_handler(
    client: lark.Client,
    config: Config,
    bot_open_id: str,
    bot_name: str,
) -> lark.EventDispatcherHandler:
    """Create and return a Lark event dispatcher handler."""
    dedup = _DeduplicateCache()
    sessions = _SessionStore()

    def handle_message(data: P2ImMessageReceiveV1) -> None:
        message = data.event.message
        sender = data.event.sender
        message_id = message.message_id
        chat_id = message.chat_id
        chat_type = message.chat_type
        msg_type = message.message_type
        root_id = message.root_id

        # Deduplicate (Lark may redeliver events)
        if dedup.seen(message_id):
            logger.debug("Skipping duplicate message: %s", message_id)
            return

        # In group chats, only respond when @mentioned
        if chat_type == "group":
            if not is_bot_mentioned(message.mentions, bot_open_id):
                return

        # Extract text content
        raw_text = extract_text(message.content, msg_type)

        # Extract image/file key for visual content
        image_key = None
        file_key = None
        file_name = None
        try:
            content = json.loads(message.content)
            if msg_type == "image":
                image_key = content.get("image_key")
            elif msg_type == "file":
                file_key = content.get("file_key")
                file_name = content.get("file_name", "")
        except (json.JSONDecodeError, TypeError):
            pass

        has_attachment = bool(image_key or file_key)

        if not raw_text and not has_attachment:
            logger.debug("Skipping non-text/non-image message: %s", message_id)
            return

        # Don't use the placeholder text for file/image messages
        user_text = ""
        if raw_text and msg_type in ("text", "post", "rich_text"):
            user_text = strip_bot_mention(raw_text, bot_name)
        elif raw_text and not has_attachment:
            user_text = strip_bot_mention(raw_text, bot_name)

        sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"

        # Handle /new command: reset session for this chat
        if user_text.strip() == "/new":
            stop_claude(chat_id)
            sessions.clear(chat_id)
            logger.info("Session reset for chat %s by %s", chat_id, sender_id)
            reply_message(
                client,
                message_id,
                "text",
                json.dumps({"text": "New session started."}),
            )
            return

        # Handle /stop command: interrupt the running request
        if user_text.strip() == "/stop":
            stopped = stop_claude(chat_id)
            msg = "Request interrupted." if stopped else "No active request to stop."
            logger.info("Stop requested for chat %s by %s: %s", chat_id, sender_id, msg)
            reply_message(
                client,
                message_id,
                "text",
                json.dumps({"text": msg}),
            )
            return

        if not user_text and not has_attachment:
            logger.debug("Empty message after stripping mention: %s", message_id)
            return

        logger.info(
            "Processing message from %s in %s: %s",
            sender_id,
            chat_id,
            user_text[:100],
        )

        # Stop any existing request for this chat before starting a new one
        stop_claude(chat_id)

        # Process in background thread to avoid blocking the event handler
        thread = threading.Thread(
            target=_process_message,
            args=(
                client,
                config,
                sessions,
                message_id,
                chat_id,
                root_id,
                user_text,
                sender_id,
                image_key,
                file_key,
                file_name,
            ),
            daemon=True,
        )
        thread.start()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build()
    )
    return handler


def _process_message(
    client: lark.Client,
    config: Config,
    sessions: _SessionStore,
    message_id: str,
    chat_id: str,
    root_id: str,
    user_text: str,
    sender_id: str,
    image_key: str | None = None,
    file_key: str | None = None,
    file_name: str | None = None,
) -> None:
    """Process a message in a background thread with streaming progress."""
    # Add thinking reaction as typing indicator
    reaction_id = add_reaction(client, message_id, _THINKING_EMOJI)
    reply_id = None
    work_dir = None

    try:
        workspace = _get_workspace(chat_id)
        attachment_ref = ""

        # Download image if sent
        if image_key:
            save_path = os.path.join(workspace, f"input_{image_key}.png")
            if not os.path.exists(save_path):
                ok = download_message_resource(
                    client, message_id, image_key, "image", save_path
                )
                if ok:
                    logger.info("Downloaded image to %s", save_path)
                else:
                    logger.warning("Failed to download image %s", image_key)
                    save_path = None
            if save_path:
                attachment_ref = f"\n\n[The user sent an image. It has been saved to: {save_path}. Use the Read tool to view it.]"

        # Download file if sent
        if file_key:
            ext = os.path.splitext(file_name)[1] if file_name else ""
            save_name = file_name or f"input_{file_key}{ext}"
            save_path = os.path.join(workspace, save_name)
            if not os.path.exists(save_path):
                ok = download_message_resource(
                    client, message_id, file_key, "file", save_path
                )
                if ok:
                    logger.info("Downloaded file to %s", save_path)
                else:
                    logger.warning("Failed to download file %s", file_key)
                    save_path = None
            if save_path:
                # Check if the file is an image by extension
                if ext.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    attachment_ref = f"\n\n[The user sent an image. It has been saved to: {save_path}. Use the Read tool to view it.]"
                else:
                    attachment_ref = f"\n\n[The user sent a file: {save_name}. It has been saved to: {save_path}. Use the Read tool to view it.]"

        # Pre-fetch thread context
        thread_messages = []
        try:
            thread_messages = list_messages(client, chat_id, page_size=30)
        except Exception as e:
            logger.warning("Failed to fetch thread context: %s", e)

        # Build prompt with context
        default_text = "Please look at the attachment I sent."
        prompt = build_prompt(
            user_text=(user_text or default_text) + attachment_ref,
            thread_messages=thread_messages,
            sender_name=sender_id,
            chat_id=chat_id,
            root_id=root_id if root_id else None,
            message_id=message_id,
        )

        # Look up existing session for this chat
        session_id = sessions.get(chat_id)

        # Note: file sending is handled by the agent via MCP tools

        # Create initial progress message
        reply_id = reply_message_with_id(
            client, message_id, "interactive",
            _build_progress_card(["Thinking..."]),
        )

        # Stream Claude's output with live updates
        status_lines = []
        last_update = 0.0

        for event in invoke_claude_streaming(prompt, config, chat_id, session_id):
            etype = event["type"]

            if etype == "progress":
                tool = event["tool"]
                status_lines.append(f"⚙ Using **{tool}**...")
                now = time.time()
                if reply_id and now - last_update >= _UPDATE_INTERVAL:
                    update_message(client, reply_id, _build_progress_card(status_lines))
                    last_update = now

            elif etype == "text":
                status_lines.append(event["content"])
                now = time.time()
                if reply_id and now - last_update >= _UPDATE_INTERVAL:
                    update_message(client, reply_id, _build_progress_card(status_lines))
                    last_update = now

            elif etype == "result":
                work_dir = event.get("work_dir", "")
                new_session_id = event.get("session_id", "")
                result_text = event.get("content", "")

                if new_session_id:
                    sessions.set(chat_id, new_session_id)

                # Update the message with the final response
                if reply_id and result_text:
                    _, final_content = format_response(result_text)
                    update_message(client, reply_id, final_content)
                elif not reply_id and result_text:
                    msg_type, content = format_response(result_text)
                    reply_message(client, message_id, msg_type, content)


            elif etype == "interrupted":
                if reply_id:
                    update_message(client, reply_id, _build_progress_card(
                        status_lines + ["**Interrupted by user.**"]
                    ))
                return

            elif etype == "error":
                error_msg = event.get("message", "Unknown error")
                logger.error("Claude streaming error: %s", error_msg)
                if reply_id:
                    _, err_content = format_error(error_msg)
                    update_message(client, reply_id, err_content)
                else:
                    err_type, err_content = format_error(error_msg)
                    reply_message(client, message_id, err_type, err_content)

    except Exception as e:
        logger.error("Error processing message %s: %s", message_id, e, exc_info=True)
        try:
            if reply_id:
                _, err_content = format_error(str(e))
                update_message(client, reply_id, err_content)
            else:
                err_type, err_content = format_error(str(e))
                reply_message(client, message_id, err_type, err_content)
        except Exception:
            logger.error("Failed to send error reply", exc_info=True)
    finally:
        # Remove thinking reaction
        if reaction_id:
            remove_reaction(client, message_id, reaction_id)


