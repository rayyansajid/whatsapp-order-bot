import html
import json
import secrets
from collections import deque
from importlib.resources import files
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from orderbot import whatsapp
from orderbot.catalog import Catalog, money
from orderbot.config import Settings
from orderbot.engine import ConversationEngine
from orderbot.llm import LLM, AnthropicLLM
from orderbot.store import OrderStore


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


class ChatOut(BaseModel):
    reply: str


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = OrderStore(settings.db_path)
    engine = ConversationEngine(Catalog.load(settings.data_dir), llm or AnthropicLLM(), store)
    seen_message_ids: deque[str] = deque(maxlen=1000)  # WhatsApp retries deliveries
    basic = HTTPBasic(auto_error=False)

    app = FastAPI(title="Orderbot")
    app.state.engine = engine
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def chat_page() -> str:
        return files("orderbot").joinpath("static/chat.html").read_text(encoding="utf-8")

    @app.post("/api/chat")
    def chat(body: ChatIn) -> ChatOut:
        return ChatOut(reply=engine.handle(f"web:{body.session_id}", body.message))

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify_webhook(
        mode: Annotated[str, Query(alias="hub.mode")] = "",
        token: Annotated[str, Query(alias="hub.verify_token")] = "",
        challenge: Annotated[str, Query(alias="hub.challenge")] = "",
    ) -> str:
        valid = settings.verify_token and secrets.compare_digest(token, settings.verify_token)
        if mode == "subscribe" and valid:
            return challenge
        raise HTTPException(status_code=403, detail="Verification failed")

    @app.post("/webhook")
    async def receive_webhook(request: Request, tasks: BackgroundTasks) -> dict[str, str]:
        raw = await request.body()
        if settings.whatsapp_app_secret and not whatsapp.signature_is_valid(
            settings.whatsapp_app_secret, raw, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(status_code=401, detail="Bad signature")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON") from None
        for message_id, sender, text in whatsapp.extract_text_messages(payload):
            if message_id in seen_message_ids:
                continue
            seen_message_ids.append(message_id)
            tasks.add_task(reply_on_whatsapp, sender, text)
        return {"status": "received"}  # ack fast; replies are sent in the background

    def reply_on_whatsapp(sender: str, text: str) -> None:
        reply = engine.handle(f"wa:{sender}", text)
        whatsapp.send_text(settings.whatsapp_token, settings.whatsapp_phone_id, sender, reply)

    def require_owner(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    ) -> None:
        if not settings.admin_password:
            return  # local demo mode
        ok = credentials is not None and secrets.compare_digest(
            credentials.password.encode(), settings.admin_password.encode()
        )
        if not ok:
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})

    @app.get("/orders", response_class=HTMLResponse, dependencies=[Depends(require_owner)])
    def orders_page() -> str:
        return render_orders(store)

    return app


def render_orders(store: OrderStore) -> str:
    rows = []
    for o in store.list_orders():
        items = "<br>".join(html.escape(f"{i['quantity']} × {i['name']}") for i in o.items)
        cells = [
            f"#{o.id}",
            html.escape(o.created_at.replace("T", " ")),
            html.escape(o.name),
            html.escape(o.address),
            items,
            money(o.total_cents),
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    body = "".join(rows) or '<tr><td colspan="6" class="empty">No orders yet.</td></tr>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orders · Saffron Lane Kitchen</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 24px 16px; background: #faf7f2;
         color: #2b2118; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  p {{ margin: 0 0 16px; color: #7a6a5a; }}
  .wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eee3d6;
            vertical-align: top; font-size: 0.92rem; }}
  th {{ background: #f3ebe0; }}
  .empty {{ text-align: center; color: #7a6a5a; }}
</style></head>
<body>
<h1>Orders</h1>
<p>Read-only view for the owner. Sample data for a fictional restaurant.</p>
<div class="wrap"><table>
<thead><tr><th>Order</th><th>Placed (UTC)</th><th>Name</th><th>Address</th><th>Items</th>
<th>Total</th></tr></thead>
<tbody>{body}</tbody></table></div>
</body></html>"""


app = create_app()
