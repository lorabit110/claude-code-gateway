# Claude Code Gateway for Lark

A Lark (Feishu) bot that connects to Claude Code CLI, enabling AI-powered conversations in Lark group chats.

## Architecture

```
main.py                     Entry point — loads config, connects WebSocket
config.py                   Dataclass config from .env
system_prompt.txt           System prompt loaded at runtime (editable without code changes)
bot/
  event_handler.py          Receives Lark events, dispatches to background threads
  message_formatter.py      Formats Claude responses as interactive markdown cards
  message_parser.py         Extracts text from Lark message JSON, strips @mentions
claude_integration/
  invoker.py                Runs `claude -p` subprocess with streaming output, session mgmt
  prompt_builder.py         Builds prompt with conversation context
lark_client/
  client.py                 Creates the Lark API client
  message_api.py            Lark API helpers: reply, update, reactions, file/image upload
mcp_tools/
  lark_server.py            MCP stdio server giving Claude access to Lark read/download APIs
```

## Key patterns

- **WebSocket events** — Lark delivers messages via `lark_oapi.ws.Client`; the bot only responds to @mentions in group chats.
- **Streaming progress** — Claude's output is streamed via `--output-format stream-json`. A live-updating card shows tool usage and intermediate text, then is replaced with the final response.
- **Persistent sessions** — Each chat_id maps to a Claude session_id stored on disk (`workspaces/<chat_id>/session.json`) so conversations survive server restarts. Stale sessions automatically fall back to new ones.
- **Persistent workspaces** — Each chat gets a dedicated directory under `workspaces/<chat_id>/` instead of temp dirs, enabling session resumption and file persistence.
- **MCP tools** — Claude can call `lark_read_thread`, `lark_read_chat_history`, `lark_get_message`, and `lark_download_resource` to fetch context and download attachments.
- **Image/file support** — When users send images or files, the bot downloads them to the workspace and instructs Claude to view them via the Read tool.
- **File generation** — After Claude responds, any new files in the workspace are uploaded and sent to the chat. Internal files (`session.json`, `WORKSPACE.md`, `input_*`) are excluded.
- **Bot commands** — `/new` resets the session (and stops any running request), `/stop` interrupts a running request.
- **System prompt** — Loaded from `system_prompt.txt` at runtime. Per-chat `WORKSPACE.md` in the workspace is appended automatically.
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
