# WhatsApp Order Bot

A sample ordering assistant for **Saffron Lane Kitchen**, a fictional restaurant. Customers can ask about the menu, hours, delivery and location, then place an order: items and quantities, name, delivery address, and a confirmation summary. The backend uses FastAPI, SQLite and Claude.

> **Sample project.** The restaurant, menu, prices, address and policies are made up. It isn't used by a real business.

## Features

- **Conversation engine** (`engine.py`): a small state machine runs the order flow (items, name, address, confirm). Claude reads free-form messages ("2 butter chicken and a naan") and answers questions using only `data/menu.yaml` and `data/faq.yaml`. Simple commands (`menu`, `order`, `done`, `yes`, `cancel`) and FAQ keywords are answered without an LLM call.
- **LLM provider interface** (`llm.py`): the engine depends on a one-method `LLM` protocol. `AnthropicLLM` is the real implementation; tests use a fake.
- **Web chat** at `/`, so you can try the demo without WhatsApp.
- **WhatsApp Cloud API webhook** at `/webhook` (GET verification, POST messages). It uses the same engine.
- **Orders page** at `/orders`: a read-only table for the owner.
- Orders are stored in SQLite (`orders.db` by default).

## Run it

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env        # then set ANTHROPIC_API_KEY
uv run --env-file .env uvicorn orderbot.main:app --reload
```

Open http://localhost:8000 to chat and http://localhost:8000/orders to see orders.

Without `ANTHROPIC_API_KEY`, menu, FAQ and the command-driven flow still work. Free-form messages get a fallback reply.

### Environment variables

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API key |
| `ANTHROPIC_MODEL` | Optional model override (default `claude-opus-5-5`) |
| `VERIFY_TOKEN` | Any string you choose; must match the token you enter in Meta's webhook setup |
| `WHATSAPP_TOKEN` | WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_ID` | Phone number ID that sends replies |
| `WHATSAPP_APP_SECRET` | Optional. If set, POSTs must carry a valid `X-Hub-Signature-256` |
| `ADMIN_PASSWORD` | Optional. If set, `/orders` requires HTTP Basic auth (any username) |
| `ORDERBOT_DB` | SQLite file path (default `orders.db`) |

Keep real values in `.env`, which is gitignored.

### Connect WhatsApp (optional)

1. Create an app in Meta for Developers and add the WhatsApp product.
2. Expose your local server, for example with `ngrok http 8000`.
3. Set the callback URL to `https://<your-tunnel>/webhook` and the verify token to your `VERIFY_TOKEN`. Subscribe to the `messages` field.
4. Message the test number. The bot replies through the Cloud API.

## Development

```bash
uv run pytest                                      # tests
uv run ruff check . && uv run ruff format --check . # lint + format check
```

## Layout

- `src/orderbot/main.py`: FastAPI app (chat page, chat API, webhook, orders page)
- `src/orderbot/engine.py`: conversation engine
- `src/orderbot/llm.py`: LLM interface and Anthropic provider
- `src/orderbot/store.py`: SQLite order store
- `src/orderbot/whatsapp.py`: webhook parsing, signature check, sending replies
- `data/menu.yaml`, `data/faq.yaml`: the fictional restaurant's menu and FAQ
- `tests/`: pytest suite (order flow with a fake LLM, webhook verification)

## Limitations

- Conversation state is kept in memory, so it resets on restart and assumes a single worker process. Orders are saved in SQLite.
- Only text messages are handled on WhatsApp.
