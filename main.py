#!/usr/bin/env python3
"""Lark Bot with Claude Code integration.

Connects to Lark via WebSocket, listens for @mentions,
and responds using Claude Code CLI.
"""

import logging
import sys

import lark_oapi as lark

from bot.event_handler import create_event_handler
from config import Config
from lark_client.client import create_client
from lark_client.message_api import get_bot_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # Load config
    config = Config()
    logger.info("Config loaded: model=%s, max_turns=%d", config.claude_model, config.claude_max_turns)

    # Create Lark REST client
    client = create_client(config)

    # Get bot's own identity
    logger.info("Fetching bot info...")
    bot_info = get_bot_info(client)
    bot_open_id = bot_info.get("open_id", "")
    bot_name = bot_info.get("app_name", "")
    if not bot_open_id:
        logger.error("Failed to get bot open_id. Check your LARK_APP_ID and LARK_APP_SECRET.")
        sys.exit(1)
    logger.info("Bot identity: name=%s, open_id=%s", bot_name, bot_open_id)

    # Create event handler
    event_handler = create_event_handler(client, config, bot_open_id, bot_name)

    # Start WebSocket connection (blocking)
    logger.info("Starting WebSocket connection...")
    ws_client = lark.ws.Client(
        app_id=config.lark_app_id,
        app_secret=config.lark_app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        domain=config.lark_domain,
    )
    ws_client.start()


if __name__ == "__main__":
    main()
