import json
import logging
import os

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
    ListMessageRequest,
    ListMessageResponse,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    ReplyMessageResponse,
)
from lark_oapi.core.enum import HttpMethod, AccessTokenType

logger = logging.getLogger(__name__)


def get_bot_info(client: lark.Client) -> dict:
    """Get bot info including open_id."""
    request = (
        lark.BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri("/open-apis/bot/v3/info")
        .token_types({AccessTokenType.TENANT})
        .build()
    )
    response: lark.BaseResponse = client.request(request)
    if not response.success():
        raise RuntimeError(
            f"Get bot info failed: code={response.code}, msg={response.msg}"
        )
    data = json.loads(response.raw.content)
    return data.get("bot", {})


def list_messages(
    client: lark.Client,
    container_id: str,
    container_id_type: str = "chat",
    page_size: int = 50,
    sort_type: str = "ByCreateTimeAsc",
) -> list:
    """Fetch messages from a chat or thread.

    Args:
        container_id: chat_id or thread_id
        container_id_type: "chat" or "thread"
        page_size: number of messages to fetch (1-50)
        sort_type: "ByCreateTimeAsc" or "ByCreateTimeDesc"

    Returns:
        List of message objects.
    """
    request = (
        ListMessageRequest.builder()
        .container_id_type(container_id_type)
        .container_id(container_id)
        .sort_type(sort_type)
        .page_size(page_size)
        .build()
    )
    response: ListMessageResponse = client.im.v1.message.list(request)
    if not response.success():
        logger.error(
            "list_messages failed: code=%s, msg=%s, log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return []
    return response.data.items or []


def reply_message(
    client: lark.Client,
    message_id: str,
    msg_type: str,
    content: str,
) -> bool:
    """Reply to a specific message.

    Args:
        message_id: the message to reply to
        msg_type: "text" or "interactive"
        content: JSON string of the message content

    Returns:
        True if successful.
    """
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        .build()
    )
    response: ReplyMessageResponse = client.im.v1.message.reply(request)
    if not response.success():
        logger.error(
            "reply_message failed: code=%s, msg=%s, log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return False
    return True


def add_reaction(
    client: lark.Client, message_id: str, emoji_type: str = "THUMBSUP"
) -> str | None:
    """Add an emoji reaction to a message.

    Args:
        message_id: the message to react to
        emoji_type: emoji type string (e.g. "THUMBSUP", "OnIt")

    Returns:
        The reaction_id if successful, None otherwise.
    """
    request = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        .build()
    )
    response = client.im.v1.message_reaction.create(request)
    if not response.success():
        logger.warning(
            "add_reaction failed: code=%s, msg=%s",
            response.code,
            response.msg,
        )
        return None
    return response.data.reaction_id


def remove_reaction(
    client: lark.Client, message_id: str, reaction_id: str
) -> bool:
    """Remove an emoji reaction from a message.

    Returns:
        True if successful.
    """
    request = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    response = client.im.v1.message_reaction.delete(request)
    if not response.success():
        logger.warning(
            "remove_reaction failed: code=%s, msg=%s",
            response.code,
            response.msg,
        )
        return False
    return True


# File-type mapping for the Lark file upload API.
# Valid values: opus, mp4, pdf, doc, xls, ppt, stream
_EXT_TO_FILE_TYPE = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".mp4": "mp4",
    ".opus": "opus",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def upload_image(client: lark.Client, file_path: str) -> str | None:
    """Upload an image to Lark and return the image_key.

    Args:
        file_path: path to the image file on disk

    Returns:
        image_key string, or None on failure.
    """
    with open(file_path, "rb") as f:
        body = (
            CreateImageRequestBody.builder()
            .image_type("message")
            .image(f)
            .build()
        )
        request = CreateImageRequest.builder().request_body(body).build()
        response = client.im.v1.image.create(request)

    if not response.success():
        logger.error(
            "upload_image failed: code=%s, msg=%s, log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return None
    return response.data.image_key


def upload_file(client: lark.Client, file_path: str) -> str | None:
    """Upload a file to Lark and return the file_key.

    Args:
        file_path: path to the file on disk

    Returns:
        file_key string, or None on failure.
    """
    file_name = os.path.basename(file_path)
    ext = os.path.splitext(file_name)[1].lower()
    file_type = _EXT_TO_FILE_TYPE.get(ext, "stream")

    with open(file_path, "rb") as f:
        body = (
            CreateFileRequestBody.builder()
            .file_type(file_type)
            .file_name(file_name)
            .file(f)
            .build()
        )
        request = CreateFileRequest.builder().request_body(body).build()
        response = client.im.v1.file.create(request)

    if not response.success():
        logger.error(
            "upload_file failed: code=%s, msg=%s, log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return None
    return response.data.file_key


def send_chat_message(
    client: lark.Client,
    chat_id: str,
    msg_type: str,
    content: str,
) -> bool:
    """Send a standalone message to a chat (not a reply).

    Args:
        chat_id: the chat to send to
        msg_type: "image", "file", "text", etc.
        content: JSON string of the message content

    Returns:
        True if successful.
    """
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        logger.error(
            "send_chat_message failed: code=%s, msg=%s, log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return False
    return True
