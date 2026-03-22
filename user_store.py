from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class UserProfile:
    user_id: int
    points: int
    sub_until: int
    sub_plan: str

    @property
    def is_active(self) -> bool:
        return self.sub_plan == "lifetime" or self.sub_until > int(time.time())


class UserStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users(
                    user_id INTEGER PRIMARY KEY,
                    points INTEGER NOT NULL DEFAULT 0,
                    sub_until INTEGER NOT NULL DEFAULT 0,
                    sub_plan TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_registry(
                    user_id INTEGER PRIMARY KEY,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invoices(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    amount_usdt REAL NOT NULL,
                    invoice_id TEXT NOT NULL,
                    pay_url TEXT NOT NULL,
                    paid INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_contrib(
                    user_id INTEGER PRIMARY KEY,
                    valid_sessions INTEGER NOT NULL DEFAULT 0,
                    valid_proxies INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_sessions(
                    fingerprint TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_proxies(
                    proxy_value TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS moderation_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_codes(
                    code TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    uses_left INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_redemptions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    redeemed_at INTEGER NOT NULL,
                    UNIQUE(code, user_id)
                )
                """
            )

    def get_or_create_user(self, user_id: int) -> UserProfile:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                conn.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
                conn.execute(
                    "INSERT OR IGNORE INTO user_registry(user_id, created_at) VALUES(?, ?)",
                    (user_id, int(time.time())),
                )
                row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO user_registry(user_id, created_at) VALUES(?, ?)",
                    (user_id, int(time.time())),
                )
            return UserProfile(
                user_id=row["user_id"],
                points=row["points"],
                sub_until=row["sub_until"],
                sub_plan=row["sub_plan"],
            )

    def add_points(self, user_id: int, points: int) -> UserProfile:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(user_id, points) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET points = points + ?",
                (user_id, points, points),
            )
        return self.get_or_create_user(user_id)

    def consume_points_for_week(self, user_id: int, points_cost: int = 10) -> Optional[UserProfile]:
        profile = self.get_or_create_user(user_id)
        if profile.points < points_cost:
            return None
        self.add_subscription(user_id, "week")
        with self._connect() as conn:
            conn.execute("UPDATE users SET points=points-? WHERE user_id=?", (points_cost, user_id))
        return self.get_or_create_user(user_id)

    def add_subscription(self, user_id: int, plan: str) -> UserProfile:
        now = int(time.time())
        durations = {
            "week": 7 * 24 * 3600,
            "month": 30 * 24 * 3600,
            "year": 365 * 24 * 3600,
            "lifetime": 0,
        }
        with self._connect() as conn:
            current = conn.execute("SELECT sub_until, sub_plan FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not current:
                conn.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
                current_until = 0
            else:
                current_until = current["sub_until"]

            if plan == "lifetime":
                conn.execute("UPDATE users SET sub_plan=?, sub_until=? WHERE user_id=?", ("lifetime", 0, user_id))
            else:
                base = max(current_until, now)
                new_until = base + durations[plan]
                conn.execute("UPDATE users SET sub_plan=?, sub_until=? WHERE user_id=?", (plan, new_until, user_id))
        return self.get_or_create_user(user_id)

    def create_invoice(self, user_id: int, plan: str, amount_usdt: float, invoice_id: str, pay_url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO invoices(user_id, plan, amount_usdt, invoice_id, pay_url, paid, created_at)
                VALUES(?, ?, ?, ?, ?, 0, ?)
                """,
                (user_id, plan, amount_usdt, invoice_id, pay_url, int(time.time())),
            )

    def get_last_unpaid_invoice(self, user_id: int):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM invoices WHERE user_id=? AND paid=0 ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()

    def mark_invoice_paid(self, invoice_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE invoices SET paid=1 WHERE invoice_id=?", (invoice_id,))

    def get_unpaid_invoices(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM invoices WHERE paid=0 ORDER BY id ASC").fetchall()
            return list(rows)

    def add_moderation_event(self, user_id: int, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO moderation_events(user_id, count, created_at) VALUES(?, ?, ?)",
                (user_id, count, int(time.time())),
            )

    def create_promo_code(
        self,
        code: str,
        plan: str,
        uses_left: int,
        expires_at: int,
        created_by: int,
    ) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO promo_codes(code, plan, uses_left, expires_at, active, created_by, created_at)
                    VALUES(?, ?, ?, ?, 1, ?, ?)
                    """,
                    (code.upper(), plan, uses_left, expires_at, created_by, int(time.time())),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def redeem_promo_code(self, user_id: int, code: str) -> tuple[bool, str]:
        now = int(time.time())
        code = code.strip().upper()
        with self._connect() as conn:
            promo = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            if not promo:
                return False, "Промокод не найден."
            if int(promo["active"]) != 1:
                return False, "Промокод неактивен."
            if int(promo["expires_at"]) < now:
                return False, "Срок действия промокода истек."
            if int(promo["uses_left"]) <= 0:
                return False, "Лимит активаций промокода исчерпан."
            already = conn.execute(
                "SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?",
                (code, user_id),
            ).fetchone()
            if already:
                return False, "Вы уже активировали этот промокод."
            conn.execute(
                "INSERT INTO promo_redemptions(code, user_id, redeemed_at) VALUES(?, ?, ?)",
                (code, user_id, now),
            )
            conn.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
        self.add_subscription(user_id, str(promo["plan"]))
        return True, f"Промокод активирован: +тариф {promo['plan']}."

    def get_stats(self) -> dict[str, dict[str, int]]:
        now = int(time.time())
        ranges = {
            "day": now - 86400,
            "week": now - 7 * 86400,
            "month": now - 30 * 86400,
            "year": now - 365 * 86400,
            "all": 0,
        }
        result: dict[str, dict[str, int]] = {}
        with self._connect() as conn:
            for key, ts in ranges.items():
                if ts > 0:
                    attacks_row = conn.execute(
                        "SELECT COALESCE(SUM(count), 0) AS total FROM moderation_events WHERE created_at>=?",
                        (ts,),
                    ).fetchone()
                    users_row = conn.execute(
                        "SELECT COUNT(*) AS total FROM user_registry WHERE created_at>=?",
                        (ts,),
                    ).fetchone()
                else:
                    attacks_row = conn.execute(
                        "SELECT COALESCE(SUM(count), 0) AS total FROM moderation_events"
                    ).fetchone()
                    users_row = conn.execute(
                        "SELECT COUNT(*) AS total FROM user_registry"
                    ).fetchone()
                result[key] = {"attacks": int(attacks_row["total"]), "users": int(users_row["total"])}
        return result

    def register_session(self, user_id: int, fingerprint: str) -> tuple[bool, int, int]:
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM known_sessions WHERE fingerprint=?", (fingerprint,)).fetchone()
            if exists:
                total = conn.execute(
                    "SELECT valid_sessions FROM user_contrib WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                return False, 0, int(total["valid_sessions"]) if total else 0
            conn.execute(
                "INSERT INTO known_sessions(fingerprint, user_id, created_at) VALUES(?, ?, ?)",
                (fingerprint, user_id, int(time.time())),
            )
            conn.execute(
                """
                INSERT INTO user_contrib(user_id, valid_sessions)
                VALUES(?, 1)
                ON CONFLICT(user_id) DO UPDATE SET valid_sessions=valid_sessions+1
                """,
                (user_id,),
            )
        self.add_points(user_id, 1)
        total = self.get_contrib(user_id)["valid_sessions"]
        return True, 1, total

    def register_proxies(self, user_id: int, proxy_values: list[str]) -> tuple[int, int, int]:
        added = 0
        with self._connect() as conn:
            current_row = conn.execute(
                "SELECT valid_proxies FROM user_contrib WHERE user_id=?",
                (user_id,),
            ).fetchone()
            current_valid = int(current_row["valid_proxies"]) if current_row else 0
            for value in proxy_values:
                exists = conn.execute("SELECT 1 FROM known_proxies WHERE proxy_value=?", (value,)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO known_proxies(proxy_value, user_id, created_at) VALUES(?, ?, ?)",
                    (value, user_id, int(time.time())),
                )
                added += 1
            if added > 0:
                conn.execute(
                    """
                    INSERT INTO user_contrib(user_id, valid_proxies)
                    VALUES(?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET valid_proxies=valid_proxies+?
                    """,
                    (user_id, added, added),
                )
        total_valid = current_valid + added
        awarded = (total_valid // 50) - (current_valid // 50)
        if awarded > 0:
            self.add_points(user_id, awarded)
        return added, awarded, total_valid

    def get_contrib(self, user_id: int) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_contrib WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return {"valid_sessions": 0, "valid_proxies": 0}
            return {"valid_sessions": int(row["valid_sessions"]), "valid_proxies": int(row["valid_proxies"])}
