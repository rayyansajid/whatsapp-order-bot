"""Conversation engine shared by the web chat and the WhatsApp webhook.

The order flow is a small state machine so it stays predictable. The LLM is only
used to read free-form messages: pull menu items and quantities out of a message,
or answer a question grounded in the menu and FAQ.
"""

import json
import logging
import threading
from dataclasses import dataclass, field

from orderbot.catalog import Catalog, delivery_fee, money
from orderbot.llm import LLM
from orderbot.store import OrderStore

log = logging.getLogger(__name__)

MAX_QUANTITY = 20

INTERPRET_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["order", "question", "other"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "quantity": {"type": "integer"},
                },
                "required": ["id", "quantity"],
                "additionalProperties": False,
            },
        },
        "reply": {"type": "string"},
    },
    "required": ["intent", "items", "reply"],
    "additionalProperties": False,
}

GREETINGS = {"hi", "hello", "hey", "help", "start", "salam", "hiya"}
START_ORDER = {"order", "start order", "new order", "place order", "place an order"}
CANCEL = {"cancel", "reset", "start over", "stop"}
DONE = {"done", "that's all", "thats all", "that's it", "thats it", "no", "nope", "checkout"}
YES = {"yes", "y", "yep", "yeah", "confirm", "ok", "okay"}


@dataclass
class Session:
    state: str = "idle"  # idle -> items -> name -> address -> confirm
    cart: dict[str, int] = field(default_factory=dict)
    name: str = ""
    address: str = ""


class ConversationEngine:
    def __init__(self, catalog: Catalog, llm: LLM, store: OrderStore) -> None:
        self.catalog = catalog
        self.llm = llm
        self.store = store
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def handle(self, customer_id: str, text: str) -> str:
        with self._lock:
            session = self._sessions.setdefault(customer_id, Session())
        text = text.strip()
        low = text.lower().rstrip(".!")

        if low in CANCEL:
            self._sessions[customer_id] = Session()
            return "No problem, I've cleared your order. Say *order* whenever you're ready."

        if session.state == "name":
            session.name = text[:80]
            session.state = "address"
            return f"Thanks, {session.name}! What's the delivery address?"
        if session.state == "address":
            session.address = text[:300]
            session.state = "confirm"
            return self._summary(session) + "\n\nReply *yes* to place the order or *cancel*."
        if session.state == "confirm":
            if low in YES:
                return self._place_order(customer_id, session)
            return "Please reply *yes* to place the order, or *cancel* to start over."

        # idle / items: commands first, then FAQ, then the LLM
        if low in GREETINGS:
            return self._welcome()
        if "menu" in low.split():
            return self.catalog.menu_text() + "\n\nTell me what you'd like to order."
        if session.state == "items" and low in DONE:
            if not session.cart:
                return "Your cart is empty. What would you like to order?"
            session.state = "name"
            return "Great! What name should we put the order under?"
        if low in START_ORDER:
            session.state = "items"
            return "What would you like? For example: _2 butter chicken and 1 garlic naan_."
        faq = self.catalog.match_faq(low)
        if faq:
            return faq.answer
        return self._interpret(session, text)

    def _interpret(self, session: Session, text: str) -> str:
        try:
            raw = self.llm.complete_json(self._system_prompt(), text, INTERPRET_SCHEMA)
            result = json.loads(raw)
        except Exception:
            log.exception("LLM call failed")
            return "Sorry, I didn't catch that. " + self._help()

        added = []
        for entry in result.get("items") or []:
            item = self.catalog.items.get(str(entry.get("id")))
            qty = entry.get("quantity")
            if item and isinstance(qty, int) and 0 < qty <= MAX_QUANTITY:
                session.cart[item.id] = min(session.cart.get(item.id, 0) + qty, MAX_QUANTITY)
                added.append(f"{qty} × {item.name}")

        if added:
            session.state = "items"
            return (
                "Added "
                + ", ".join(added)
                + ".\n\n"
                + self._cart_text(session)
                + "\n\nAnything else? Reply *done* when you're finished."
            )
        reply = str(result.get("reply") or "").strip()
        return reply or "Sorry, I didn't catch that. " + self._help()

    def _place_order(self, customer_id: str, session: Session) -> str:
        items, subtotal = self._lines(session)
        fee = delivery_fee(subtotal)
        order_id = self.store.save(
            customer_id=customer_id,
            name=session.name,
            address=session.address,
            items=items,
            subtotal_cents=subtotal,
            delivery_cents=fee,
        )
        self._sessions[customer_id] = Session()
        return (
            f"✅ Order #{order_id} confirmed! Total {money(subtotal + fee)}, "
            "paid on delivery. It should arrive in 35–50 minutes."
        )

    def _lines(self, session: Session) -> tuple[list[dict], int]:
        items, subtotal = [], 0
        for item_id, qty in session.cart.items():
            item = self.catalog.items[item_id]
            line_total = item.price_cents * qty
            subtotal += line_total
            items.append(
                {"id": item_id, "name": item.name, "quantity": qty, "line_cents": line_total}
            )
        return items, subtotal

    def _cart_text(self, session: Session) -> str:
        items, subtotal = self._lines(session)
        lines = [f"• {i['quantity']} × {i['name']} — {money(i['line_cents'])}" for i in items]
        return "*Your cart*\n" + "\n".join(lines) + f"\nSubtotal: {money(subtotal)}"

    def _summary(self, session: Session) -> str:
        _, subtotal = self._lines(session)
        fee = delivery_fee(subtotal)
        return (
            "*Order summary*\n"
            + self._cart_text(session).removeprefix("*Your cart*\n")
            + f"\nDelivery: {money(fee) if fee else 'free'}"
            + f"\n*Total: {money(subtotal + fee)}*"
            + f"\n\nName: {session.name}\nDeliver to: {session.address}"
        )

    def _welcome(self) -> str:
        return f"Hi! Welcome to *{self.catalog.restaurant}* 🍛\n" + self._help()

    def _help(self) -> str:
        return (
            "You can say *menu* to see what we serve, *order* to start an order, "
            "or ask about our hours, delivery or location."
        )

    def _system_prompt(self) -> str:
        menu = "\n".join(
            f"- id={i.id} | {i.name} | {i.category} | {money(i.price_cents)}"
            for i in self.catalog.items.values()
        )
        return (
            f"You are the WhatsApp ordering assistant for {self.catalog.restaurant}, "
            "a restaurant.\n\n"
            "Read the customer's message and return JSON:\n"
            '- intent "order" if they name dishes they want. Put each dish in `items` '
            "using the exact menu id and a quantity (default 1). Skip dishes not on the menu "
            "and mention them in `reply`.\n"
            '- intent "question" if they ask something. Answer in `reply` using only the '
            "menu and FAQ below; if the answer isn't there, say you're not sure and suggest "
            "calling the restaurant. Keep it to 1-3 short sentences.\n"
            '- intent "other" for anything else, with a short friendly `reply` that steers '
            "back to the menu or ordering.\n"
            "Never invent dishes, prices, or policies.\n\n"
            f"MENU\n{menu}\n\nFAQ\n{self.catalog.faq_text()}"
        )
