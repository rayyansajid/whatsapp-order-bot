# WhatsApp Order Bot

A portfolio demo of a WhatsApp ordering assistant for **Saffron Lane Kitchen**, a fictional restaurant. Customers message the bot on WhatsApp to browse the menu and place orders. The backend uses FastAPI and Claude.

> Demo project. The business, menu, and prices are made up.

## Quickstart

```bash
uv sync
cp .env.example .env   # fill in your keys
uv run uvicorn orderbot.main:app --reload
```

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
```

## Layout

- `src/orderbot/`: application code
- `tests/`: pytest suite
- `data/menu.yaml`: the fictional restaurant's menu
