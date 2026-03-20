import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from bot.message_formatter import format_error, format_response
from bot.message_parser import extract_text, is_bot_mentioned, strip_bot_mention
from claude_integration.invoker import invoke_claude
from claude_integration.prompt_builder import build_prompt
from config import Config
from lark_client.message_api import (
    IMAGE_EXTENSIONS,
    add_reaction,
    list_messages,
    remove_reaction,
    reply_message,
    send_chat_message,
    upload_file,
    upload_image,
)

logger = logging.getLogger(__name__)

# Emoji used as a "thinking" indicator while Claude processes
_THINKING_EMOJI = "OnIt"


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
    """Thread-safe mapping of chat_id → Claude session_id."""

    def __init__(self):
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, chat_id: str) -> str | None:
        with self._lock:
            return self._sessions.get(chat_id)

    def set(self, chat_id: str, session_id: str) -> None:
        with self._lock:
            self._sessions[chat_id] = session_id


def create_event_handler(
    client: lark.Client,
    config: Config,
    bot_open_id: str,
    bot_name: str,
) -> lark.EventDispatcherHandler:
    """Create and return a Lark event dispatcher handler.

    Args:
        client: Lark API client for sending replies
        config: application config
        bot_open_id: the bot's open_id for @mention detection
        bot_name: the bot's display name

    Returns:
        Configured EventDispatcherHandler.
    """
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
        if not raw_text:
            logger.debug("Skipping non-text message: %s", message_id)
            return

        user_text = strip_bot_mention(raw_text, bot_name)
        if not user_text:
            logger.debug("Empty message after stripping mention: %s", message_id)
            return

        sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
        logger.info(
            "Processing message from %s in %s: %s",
            sender_id,
            chat_id,
            user_text[:100],
        )

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
) -> None:
    """Process a message in a background thread."""
    # Add thinking reaction as typing indicator
    reaction_id = add_reaction(client, message_id, _THINKING_EMOJI)
    work_dir = None

    try:
        # Pre-fetch thread context
        thread_messages = []
        try:
            thread_messages = list_messages(client, chat_id, page_size=30)
        except Exception as e:
            logger.warning("Failed to fetch thread context: %s", e)

        # Build prompt with context
        prompt = build_prompt(
            user_text=user_text,
            thread_messages=thread_messages,
            sender_name=sender_id,
            chat_id=chat_id,
            root_id=root_id if root_id else None,
        )

        # Look up existing session for this chat
        session_id = sessions.get(chat_id)

        # Invoke Claude Code (with session resumption)
        response_text, new_session_id, work_dir = invoke_claude(
            prompt, config, session_id
        )

        # Store the session ID for future messages in this chat
        if new_session_id:
            sessions.set(chat_id, new_session_id)

        # Format and send reply
        msg_type, content = format_response(response_text)
        reply_message(client, message_id, msg_type, content)

        # Send any files Claude generated
        _send_generated_files(client, chat_id, work_dir)

    except Exception as e:
        logger.error("Error processing message %s: %s", message_id, e, exc_info=True)
        try:
            err_type, err_content = format_error(str(e))
            reply_message(client, message_id, err_type, err_content)
        except Exception:
            logger.error("Failed to send error reply", exc_info=True)
    finally:
        # Remove thinking reaction
        if reaction_id:
            remove_reaction(client, message_id, reaction_id)
        # Clean up temporary working directory
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def _send_generated_files(
    client: lark.Client, chat_id: str, work_dir: str
) -> None:
    """Scan work_dir for files created by Claude and send them to the chat."""
    if not work_dir or not os.path.isdir(work_dir):
        return

    files = []
    for root, _dirs, filenames in os.walk(work_dir):
        for name in filenames:
            files.append(os.path.join(root, name))

    if not files:
        return

    logger.info("Found %d generated file(s) in %s", len(files), work_dir)

    for file_path in files:
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in IMAGE_EXTENSIONS:
                image_key = upload_image(client, file_path)
                if image_key:
                    content = json.dumps({"image_key": image_key})
                    send_chat_message(client, chat_id, "image", content)
                    logger.info("Sent image: %s", os.path.basename(file_path))
            else:
                file_key = upload_file(client, file_path)
                if file_key:
                    file_name = os.path.basename(file_path)
                    content = json.dumps({"file_key": file_key})
                    send_chat_message(client, chat_id, "file", content)
                    logger.info("Sent file: %s", file_name)
        except Exception:
            logger.error(
                "Failed to send generated file: %s",
                file_path,
                exc_info=True,
            )
