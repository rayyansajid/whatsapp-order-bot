"""Menu and FAQ loaded from YAML."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DELIVERY_FEE_CENTS = 399
FREE_DELIVERY_OVER_CENTS = 3000


@dataclass(frozen=True)
class MenuItem:
    id: str
    name: str
    category: str
    price_cents: int


@dataclass(frozen=True)
class FaqEntry:
    topic: str
    keywords: tuple[str, ...]
    answer: str


def money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def delivery_fee(subtotal_cents: int) -> int:
    return 0 if subtotal_cents > FREE_DELIVERY_OVER_CENTS else DELIVERY_FEE_CENTS


class Catalog:
    def __init__(self, restaurant: str, items: list[MenuItem], faq: list[FaqEntry]):
        self.restaurant = restaurant
        self.items = {item.id: item for item in items}
        self.faq = faq

    @classmethod
    def load(cls, data_dir: Path) -> "Catalog":
        menu = yaml.safe_load((data_dir / "menu.yaml").read_text(encoding="utf-8"))
        faq = yaml.safe_load((data_dir / "faq.yaml").read_text(encoding="utf-8"))
        items = [
            MenuItem(i["id"], i["name"], i["category"], round(float(i["price"]) * 100))
            for i in menu["items"]
        ]
        entries = [FaqEntry(f["topic"], tuple(f["keywords"]), f["answer"]) for f in faq]
        return cls(menu["restaurant"]["name"], items, entries)

    def menu_text(self) -> str:
        lines = [f"*{self.restaurant} menu*"]
        category = None
        for item in self.items.values():
            if item.category != category:
                category = item.category
                lines.append(f"\n_{category.title()}_")
            lines.append(f"• {item.name} — {money(item.price_cents)}")
        return "\n".join(lines)

    def faq_text(self) -> str:
        return "\n".join(f"{e.topic}: {e.answer}" for e in self.faq)

    def match_faq(self, text: str) -> FaqEntry | None:
        words = set(re.findall(r"[a-z]+", text.lower()))
        for entry in self.faq:
            if words.intersection(entry.keywords):
                return entry
        return None
