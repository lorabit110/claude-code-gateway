# Claude Code Gateway for Lark

A Lark (Feishu) bot that bridges group chat conversations to [Claude Code](https://docs.anthropic.com/en/docs/claude-code), giving your team an AI assistant that can search the web, read chat history, and even generate and share files — all from within Lark.

## Prerequisites

- **Python 3.10+**
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** installed and authenticated (`claude` must be in your PATH)
- **A Lark app** with bot capabilities enabled — create one at the [Lark Developer Console](https://open.larksuite.com/app) (or [Feishu Developer Console](https://open.feishu.cn/app) for Feishu)

## Lark App Setup

### 1. Create the app

1. Go to the Developer Console and create a new app
2. Under **Bot**, enable the bot capability

### 2. Add permissions

Navigate to **Permissions & Scopes** and add:

| Permission | Purpose |
|---|---|
| `im:message` or `im:message:send_as_bot` | Send and reply to messages |
| `im:message:readonly` | Read chat history for context |
| `im:resource` | Upload images and files |

### 3. Enable events

1. Go to **Events & Callbacks**
2. Under **Event Subscriptions**, add the `im.message.receive_v1` event
3. For the subscription method, select **WebSocket** (long connection)

### 4. Publish

Create a new version and publish the app. If your organization requires admin approval, request it.

### 5. Add the bot to a group

Invite your bot into any Lark group chat where you want it available.

## Installation

```bash
git clone git@github.com:lorabit110/claude-code-gateway.git
cd claude-code-gateway

python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
```

Edit `.env` and fill in your Lark app credentials:

```
LARK_APP_ID=cli_xxxxxxxxxx
LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Optional settings

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-4-6` | Claude model to use |
| `CLAUDE_MAX_TURNS` | `10` | Max agentic turns per invocation |
| `CLAUDE_TIMEOUT` | `120` | Subprocess timeout in seconds |
| `LARK_DOMAIN` | `https://open.larksuite.com` | Use `https://open.feishu.cn` for Feishu |

## Running

```bash
python main.py
```

The bot connects to Lark via WebSocket and starts listening for messages. You should see:

```
Bot identity: name=YourBot, open_id=ou_xxxxxx
Starting WebSocket connection...
```

## Usage

- **In a group chat** — @mention the bot followed by your question
- **File generation** — ask the bot to write a script, generate an image, etc. It will send the file as an attachment in the chat alongside its text reply

## How It Works

1. The bot receives messages via Lark's WebSocket event stream
2. On @mention, it builds a prompt with recent conversation context
3. It invokes `claude -p` as a subprocess with MCP tools that let Claude read more chat history if needed
4. Claude's text response is sent as a reply; any files Claude wrote to its temporary working directory are uploaded and sent to the chat
5. Each chat maintains a persistent Claude session for multi-turn conversations

## Project Structure

```
main.py                        Entry point
config.py                      Configuration from environment variables
bot/
  event_handler.py             WebSocket event handling and message dispatch
  message_formatter.py         Response formatting (text / interactive card)
  message_parser.py            Lark message parsing and @mention stripping
claude_integration/
  invoker.py                   Claude Code CLI subprocess management
  prompt_builder.py            Prompt construction with conversation context
lark_client/
  client.py                    Lark API client factory
  message_api.py               Message, reaction, file, and image API helpers
mcp_tools/
  lark_server.py               MCP server exposing Lark read tools to Claude
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `Claude Code CLI not found` | Make sure `claude` is installed and in your PATH |
| Bot doesn't respond in group | Verify the bot is @mentioned and has `im:message:readonly` permission |
| File uploads fail | Add the `im:resource` permission and re-publish the app |
| `Get bot info failed` | Check `LARK_APP_ID` and `LARK_APP_SECRET` in `.env` |
| Timeout errors | Increase `CLAUDE_TIMEOUT` for complex queries |
