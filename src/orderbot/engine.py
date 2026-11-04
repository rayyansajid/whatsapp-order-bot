"""Conversation engine shared by the web chat and the WhatsApp webhook.

The order flow is a small state machine so it stays predictable. The LLM is only
used to read free-form messages: pull menu items and quantities out of a message,
or answer a question grounded in the menu and FAQ.
"""

import json
import logging
import re
import threading
from dataclasses import dataclass, field

from orderbot.catalog import Catalog, delivery_fee, money
from orderbot.llm import LLM, LLMUnavailableError
from orderbot.store import OrderStore

log = logging.getLogger(__name__)

MAX_QUANTITY = 20

INTERPRET_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["order", "question", "finish", "confirm", "cancel", "other"],
        },
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

# Phrase checks that work without the LLM (e.g. when the free tier is rate limited).
FINISH_RE = re.compile(
    r"\b(that'?s (all|it|everything)|nothing (else|more)|no (thanks|thank you|more)"
    r"|(i'?m|i am) (done|good|finished|all set)"
    r"|(don'?t|do not) (\w+ ){0,2}(need|want) (anything|any ?more))\b"
)
YES_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm|go ahead|place it|sounds good|do it)\b"
)
NO_RE = re.compile(r"\b(no|nope|nah|cancel|never ?mind|start over|don'?t|do not)\b")
# Menu / price requests get the exact menu text, never an LLM paraphrase.
MENU_RE = re.compile(
    r"\b(menu|prices?|price list|list of (items|dishes|food)"
    r"|what (do|can) you (have|serve|sell|offer)|what'?s available)\b"
)
# Only the engine may say an order was placed/confirmed/delivered. Catches LLM
# free-text replies that falsely claim this so they can be swapped out.
ORDER_CLAIM_RE = re.compile(
    r"\border\b[^.!?\n]{0,40}\b(placed|confirmed|on (its|it's) way|delivered)\b"
    r"|\b(placed|confirmed)\b[^.!?\n]{0,40}\border\b",
    re.IGNORECASE,
)

NAME_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}
NAME_PREFIX_RE = re.compile(
    r"^((hi|hello|hey)[\s,!]+)?(my name is|my name's|name is|name's|i am|i'm|im|this is"
    r"|it's|its|call me|go with|put it under)\s+",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '.-][^\W\d_]+){0,3}$")
ADDRESS_PREFIX_RE = re.compile(
    r"^(my address is|address is|address:|deliver (it )?to|send (it )?to"
    r"|i live (at|in|on)|i'm (at|in)|i am (at|in))\s+",
    re.IGNORECASE,
)
ADDRESS_HINT_RE = re.compile(
    r"\d|\b(street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|way|place"
    r"|court|ct|block|sector|phase|area|town|colony|apartment|apt|flat|house|floor"
    r"|suite|building|near)\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lowercase, straighten apostrophes and drop other punctuation."""
    text = text.lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text).split())


def strip_name(text: str) -> str:
    name = NAME_PREFIX_RE.sub("", text.strip().replace("’", "'"))
    name = re.sub(r"[\s,]*\b(please|thanks|thank you)\b.*$", "", name, flags=re.IGNORECASE)
    return name.strip(" .!,")


def extract_address(text: str) -> tuple[str, bool]:
    """Return the address without 'I live at'-style wording and whether it looks like one.

    It looks like an address if it has a number, a street/area word, or followed
    'I live in / at'. "I live with Tony Stark" has none of these.
    """
    text = text.strip().replace("’", "'")
    match = ADDRESS_PREFIX_RE.match(text)
    address = text[match.end() :].strip(" .!,") if match else text.strip(" .!,")
    return address, bool(address) and (bool(match) or bool(ADDRESS_HINT_RE.search(address)))


@dataclass
class Session:
    state: str = "idle"  # idle -> items -> name -> address -> confirm
    cart: dict[str, int] = field(default_factory=dict)
    name: str = ""
    address: str = ""
    address_retry: bool = False


class ConversationEngine:
    def __init__(self, catalog: Catalog, llm: LLM, store: OrderStore) -> None:
        self.catalog = catalog
        self.llm = llm
        self.store = store
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._menu_words = {
            w for i in catalog.items.values() for w in normalize(i.name).split() if len(w) > 3
        }

    def handle(self, customer_id: str, text: str) -> str:
        with self._lock:
            session = self._sessions.setdefault(customer_id, Session())
        text = text.strip()
        low = text.lower().rstrip(".!")

        if low in CANCEL:
            self._sessions[customer_id] = Session()
            return "No problem, I've cleared your order. Say *order* whenever you're ready."

        if session.state == "name":
            session.name = self._extract_name(text)[:80]
            session.state = "address"
            return f"Thanks, {session.name}! What's the delivery address?"
        if session.state == "address":
            address, looks_valid = extract_address(text)
            if not looks_valid and not session.address_retry:
                session.address_retry = True
                return (
                    "Sorry, that doesn't look like a delivery address. Please send the full "
                    "address with house number, street and area."
                )
            session.address = address[:300]
            session.state = "confirm"
            return self._summary(session) + "\n\nReply *yes* to place the order or *cancel*."
        if session.state == "confirm":
            if low in YES:
                return self._place_order(customer_id, session)
            norm = normalize(text)
            said_yes, said_no = bool(YES_RE.search(norm)), bool(NO_RE.search(norm))
            if said_yes != said_no:
                intent = "confirm" if said_yes else "cancel"
            else:  # neither or both: let the LLM decide
                intent = self._interpret_intent(session, text)
            if intent == "confirm":
                return self._place_order(customer_id, session)
            if intent == "cancel":
                self._sessions[customer_id] = Session()
                return "No problem, I've cancelled that order. Say *order* to start again."
            return "Please reply *yes* to place the order, or *cancel* to start over."

        # idle / items: commands first, then FAQ, then the LLM
        if low in GREETINGS:
            return self._welcome()
        if MENU_RE.search(normalize(text)):
            return self.catalog.menu_text() + "\n\nTell me what you'd like to order."
        if session.state == "items" and (low in DONE or self._says_finished(text)):
            return self._finish_items(session)
        if low in START_ORDER:
            session.state = "items"
            return "What would you like? For example: _2 butter chicken and 1 garlic naan_."
        faq = self.catalog.match_faq(low)
        if faq:
            return faq.answer
        return self._interpret(session, text)

    def _extract_name(self, text: str) -> str:
        """Strip 'I am' / 'my name is'-style wording; ask the LLM only if it's still unclear."""
        name = strip_name(text)
        if NAME_RE.match(name):
            return name
        try:
            raw = self.llm.complete_json(
                "Extract the customer's name from their message. Return only the name, "
                "as written. If there is no name, return an empty string.",
                text,
                NAME_SCHEMA,
            )
            extracted = str(json.loads(raw).get("name") or "").strip()
        except Exception as exc:
            log.warning("LLM name extraction failed: %s", exc)
            extracted = ""
        return extracted or name

    def _says_finished(self, text: str) -> bool:
        """'that's all'-style phrase, as long as the message doesn't also name dishes."""
        norm = normalize(text)
        if any(c.isdigit() for c in norm) or self._menu_words.intersection(norm.split()):
            return False
        return bool(FINISH_RE.search(norm))

    def _finish_items(self, session: Session) -> str:
        if not session.cart:
            return "Your cart is empty. What would you like to order?"
        session.state = "name"
        return "Great! What name should we put the order under?"

    def _interpret_intent(self, session: Session, text: str) -> str:
        try:
            raw = self.llm.complete_json(self._system_prompt(session), text, INTERPRET_SCHEMA)
            return str(json.loads(raw).get("intent") or "other")
        except Exception as exc:
            log.warning("LLM intent check failed: %s", exc)
            return "other"

    def _interpret(self, session: Session, text: str) -> str:
        try:
            raw = self.llm.complete_json(self._system_prompt(session), text, INTERPRET_SCHEMA)
            result = json.loads(raw)
        except LLMUnavailableError as exc:
            log.warning("LLM unavailable: %s", exc)
            return "Sorry, I'm a little busy right now. Please try again in a minute. " + (
                self._help()
            )
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

        # "confirm" during items means "I'm done adding items", not "place my order" -
        # only the confirm step can actually place an order.
        if result.get("intent") in ("finish", "confirm") and session.state == "items":
            prefix = "Added " + ", ".join(added) + ".\n\n" if added else ""
            return prefix + self._finish_items(session)
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
        if reply and ORDER_CLAIM_RE.search(reply):
            log.warning("Blocked LLM reply falsely claiming an order was placed")
            reply = (
                self._help()
                if session.state != "items"
                else (
                    "Let's continue your order - what would you like to add? "
                    "Say *done* when you're finished."
                )
            )
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

    def _system_prompt(self, session: Session) -> str:
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
            '- intent "question" if they ask something about this restaurant or ordering. '
            "Answer in `reply` in 1-3 short sentences, following the grounding rules.\n"
            '- intent "finish" if they say they are done adding items (e.g. "that\'s all", '
            '"I don\'t need anything else"). Put any dishes named in the same message in '
            "`items`.\n"
            '- intent "confirm" if they agree to place the order (e.g. "yeah go ahead").\n'
            '- intent "cancel" if they decline, cancel or want to start over.\n'
            '- intent "other" for anything else, with a short friendly `reply` that steers '
            "back to the menu or ordering.\n\n"
            "GROUNDING RULES (always follow):\n"
            "- Only state facts that appear in the MENU and FAQ below. Never invent dishes, "
            "prices, policies or details.\n"
            "- The data has nothing on ingredients, spice level, allergens, kids' options or "
            "items not listed (e.g. water or other drinks). For anything like that, say you "
            "don't have that information and the restaurant staff will confirm.\n"
            "- Never make claims about allergens, dietary suitability (e.g. for children, "
            "vegans, diabetics) or health, and don't recommend dishes on that basis.\n"
            "- Only help with ordering from this restaurant. Politely decline unrelated "
            "requests and offer to help with an order instead.\n"
            "- Never state or imply that an order has been placed, confirmed, or is on its "
            "way or delivered; the system does that, not you.\n\n"
            f"CURRENT STEP: {session.state} (items = adding dishes, confirm = order summary "
            "shown, waiting for yes/no)\n\n"
            f"MENU\n{menu}\n\nFAQ\n{self.catalog.faq_text()}"
        )
