"""SQLite order storage."""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    items_json TEXT NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    delivery_cents INTEGER NOT NULL,
    total_cents INTEGER NOT NULL
)
"""


@dataclass(frozen=True)
class Order:
    id: int
    created_at: str
    customer_id: str
    name: str
    address: str
    items: list[dict]
    subtotal_cents: int
    delivery_cents: int
    total_cents: int


class OrderStore:
    def __init__(self, path: str) -> None:
        self.path = path
        with self._connect() as conn:
            conn.execute(SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def save(
        self,
        *,
        customer_id: str,
        name: str,
        address: str,
        items: list[dict],
        subtotal_cents: int,
        delivery_cents: int,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO orders (created_at, customer_id, name, address, items_json,"
                " subtotal_cents, delivery_cents, total_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    customer_id,
                    name,
                    address,
                    json.dumps(items),
                    subtotal_cents,
                    delivery_cents,
                    subtotal_cents + delivery_cents,
                ),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def list_orders(self) -> list[Order]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, customer_id, name, address, items_json,"
                " subtotal_cents, delivery_cents, total_cents FROM orders ORDER BY id DESC"
            ).fetchall()
        return [
            Order(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7], r[8]) for r in rows
        ]
