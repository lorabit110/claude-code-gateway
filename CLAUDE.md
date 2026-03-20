# Claude Code Gateway for Lark

A Lark (Feishu) bot that connects to Claude Code CLI, enabling AI-powered conversations in Lark group chats.

## Architecture

```
main.py                     Entry point — loads config, connects WebSocket
config.py                   Dataclass config from .env
bot/
  event_handler.py          Receives Lark events, dispatches to background threads
  message_formatter.py      Formats Claude responses (plain text / interactive card)
  message_parser.py         Extracts text from Lark message JSON, strips @mentions
claude_integration/
  invoker.py                Runs `claude -p` subprocess with MCP tools + temp work dir
  prompt_builder.py         Builds prompt with conversation context
lark_client/
  client.py                 Creates the Lark API client
  message_api.py            Lark API helpers: reply, reactions, file/image upload, send
mcp_tools/
  lark_server.py            MCP stdio server giving Claude access to Lark read APIs
```

## Key patterns

- **WebSocket events** — Lark delivers messages via `lark_oapi.ws.Client`; the bot only responds to @mentions in group chats.
- **Session resumption** — Each chat_id maps to a Claude session_id so conversations persist across messages.
- **MCP tools** — Claude can call `lark_read_thread`, `lark_read_chat_history`, `lark_get_message` to fetch more context.
- **File generation** — Each invocation creates a temp working dir. After Claude responds, any files it wrote are uploaded (images via image API, everything else via file API) and sent to the chat, then the dir is cleaned up.
- **Message deduplication** — LRU cache prevents processing redelivered events.

## Running

```bash
cp .env.example .env   # fill in LARK_APP_ID and LARK_APP_SECRET
pip install -r requirements.txt
python main.py
```

## Required Lark permissions

- `im:message` or `im:message:send_as_bot` — send/reply messages
- `im:message:readonly` — read chat history
- `im:resource` — upload images and files

## Environment variables

See `.env.example` for all options. Required: `LARK_APP_ID`, `LARK_APP_SECRET`.
