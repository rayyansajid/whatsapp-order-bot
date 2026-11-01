# CLAUDE.md

Portfolio demo: a WhatsApp ordering bot for **Saffron Lane Kitchen**, a fictional restaurant. All business data is made up.

## Stack
- Python 3.12, managed with **uv**
- **FastAPI** + uvicorn
- **pytest** for tests, **ruff** for lint and format
- Code in `src/orderbot/`, tests in `tests/`, menu in `data/menu.yaml`

## Commands
- Run: `uv run uvicorn orderbot.main:app --reload`
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`

## Rules
- **One feature per branch and per PR.** Branch off `main` (`feat/...`, `fix/...`, `chore/...`).
- **Test only what's required.** Cover the behavior that matters. Skip exhaustive edge cases and redundant tests.
- **Never commit secrets.** Real keys go in `.env`, which is gitignored. Only `.env.example` with empty values is committed.
- Run the tests and ruff before every commit.
