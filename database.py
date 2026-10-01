"""
SQLite Database Layer for Telegram Messages and Buyer Leads (WAL Mode & Persistent State Engine)
File: database.py

Architecture Standards:
- WAL (Write-Ahead Logging) Journal Mode for non-blocking read/write concurrency
- Strict UNIQUE(chat_id, message_id) constraints for duplicate prevention
- Persistent stateful group scheduling in SQLite (replaces JSON state files)
- Full crash resilience: recovers checkpoints directly from SQLite upon restart
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

DB_FILE = Path(__file__).parent.resolve() / "buyers.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Connect to SQLite database with WAL mode and busy timeout."""
    target_path = Path(db_path or DB_FILE)
    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize SQLite tables and migrate columns if needed."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    # 1. Raw Messages Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_title TEXT,
            sender_id INTEGER,
            sender_name TEXT,
            sender_username TEXT,
            date TEXT,
            raw_text TEXT,
            message_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, message_id)
        )
    """)

    # 2. Structured Buyer Leads Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS buyers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_title TEXT,
            sender_id INTEGER,
            sender_name TEXT,
            sender_username TEXT,
            date TEXT,
            need TEXT,
            budget TEXT,
            quantity TEXT,
            urgency TEXT,
            confidence REAL,
            evidence TEXT,
            raw_text TEXT,
            message_link TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, message_id)
        )
    """)

    # 3. Persistent Group State & Scheduler Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scrape_state (
            chat_id INTEGER PRIMARY KEY,
            chat_title TEXT,
            chat_username TEXT,
            last_message_id INTEGER DEFAULT 0,
            last_scraped_at TIMESTAMP,
            next_eligible_at TIMESTAMP,
            status TEXT DEFAULT 'idle',
            error_count INTEGER DEFAULT 0,
            last_error TEXT,
            priority INTEGER DEFAULT 1
        )
    """)

    # Check for column migrations on scrape_state
    cur.execute("PRAGMA table_info(scrape_state)")
    existing_cols = {r["name"] for r in cur.fetchall()}

    columns_to_add = [
        ("chat_username", "TEXT"),
        ("next_eligible_at", "TIMESTAMP"),
        ("status", "TEXT DEFAULT 'idle'"),
        ("error_count", "INTEGER DEFAULT 0"),
        ("last_error", "TEXT"),
        ("priority", "INTEGER DEFAULT 1"),
    ]

    for col_name, col_type in columns_to_add:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE scrape_state ADD COLUMN {col_name} {col_type}")

    # High-performance indices
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, message_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_buyers_urgency ON buyers(urgency)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_buyers_created ON buyers(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scrape_eligible ON scrape_state(next_eligible_at, priority)")

    conn.commit()
    conn.close()


def insert_raw_message(msg: Dict[str, Any], db_path: Optional[Path] = None) -> bool:
    """Insert a raw message into messages table. Returns True if inserted, False if duplicate."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO messages (
                message_id, chat_id, chat_title, sender_id,
                sender_name, sender_username, date, raw_text, message_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg.get("message_id"),
            msg.get("chat_id"),
            msg.get("chat_title"),
            msg.get("sender_id"),
            msg.get("sender_name"),
            msg.get("sender_username"),
            str(msg.get("date")),
            msg.get("raw_text") or msg.get("text") or "",
            msg.get("message_link")
        ))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def insert_buyer(buyer: Dict[str, Any], db_path: Optional[Path] = None) -> bool:
    """Insert or update a structured buyer record. Returns True if new record inserted."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO buyers (
                message_id, chat_id, chat_title, sender_id,
                sender_name, sender_username, date, need,
                budget, quantity, urgency, confidence,
                evidence, raw_text, message_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                need = excluded.need,
                budget = excluded.budget,
                quantity = excluded.quantity,
                urgency = excluded.urgency,
                confidence = excluded.confidence,
                evidence = excluded.evidence
        """, (
            buyer.get("message_id"),
            buyer.get("chat_id"),
            buyer.get("chat_title"),
            buyer.get("sender_id"),
            buyer.get("sender_name"),
            buyer.get("sender_username"),
            str(buyer.get("date")),
            buyer.get("need") or "",
            buyer.get("budget") or "",
            buyer.get("quantity") or "",
            buyer.get("urgency") or "LOW",
            float(buyer.get("confidence") or 0.0),
            buyer.get("evidence") or "",
            buyer.get("raw_text") or "",
            buyer.get("message_link") or ""
        ))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def register_groups(groups: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """Register target groups into persistent scrape_state table if not already present."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    registered = 0
    try:
        for g in groups:
            chat_id = g.get("id")
            if not chat_id:
                continue
            title = g.get("title", f"Chat_{chat_id}")
            uname = g.get("username")
            cur.execute("""
                INSERT INTO scrape_state (chat_id, chat_title, chat_username, last_message_id, next_eligible_at, status)
                VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP, 'idle')
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_title = excluded.chat_title,
                    chat_username = excluded.chat_username
            """, (chat_id, title, uname))
            registered += 1
        conn.commit()
        return registered
    finally:
        conn.close()


def get_next_eligible_group(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the next eligible group whose cooldown has expired."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT chat_id, chat_title, chat_username, last_message_id,
                   last_scraped_at, next_eligible_at, status, error_count
            FROM scrape_state
            WHERE next_eligible_at IS NULL
               OR datetime('now') >= datetime(next_eligible_at)
            ORDER BY
                priority DESC,
                last_scraped_at ASC NULLS FIRST
            LIMIT 1
        """)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_group_checkpoint(
    chat_id: int,
    last_message_id: int,
    cooldown_seconds: float,
    status: str = "idle",
    error: Optional[str] = None,
    db_path: Optional[Path] = None
) -> None:
    """Update checkpoint and schedule next eligibility time in SQLite."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE scrape_state
            SET last_message_id = MAX(last_message_id, ?),
                last_scraped_at = CURRENT_TIMESTAMP,
                next_eligible_at = datetime('now', ?),
                status = ?,
                error_count = CASE WHEN ? IS NOT NULL THEN error_count + 1 ELSE 0 END,
                last_error = ?
            WHERE chat_id = ?
        """, (
            last_message_id,
            f"+{int(cooldown_seconds)} seconds",
            status,
            error,
            error,
            chat_id
        ))
        conn.commit()
    finally:
        conn.close()


def set_global_flood_wait(seconds: int, db_path: Optional[Path] = None) -> None:
    """Push next_eligible_at forward for all groups upon server-dictated FloodWait."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE scrape_state
            SET next_eligible_at = datetime('now', ?),
                status = 'flood_wait'
        """, (f"+{int(seconds)} seconds",))
        conn.commit()
    finally:
        conn.close()


def get_schedule_summary(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get snapshot of current scheduling states across all registered groups."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM scrape_state")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scrape_state WHERE next_eligible_at IS NULL OR datetime('now') >= datetime(next_eligible_at)")
        eligible = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scrape_state WHERE datetime('now') < datetime(next_eligible_at)")
        cooling = cur.fetchone()[0]
        cur.execute("SELECT MIN(next_eligible_at) FROM scrape_state WHERE datetime('now') < datetime(next_eligible_at)")
        next_due = cur.fetchone()[0]
        return {
            "total_registered_groups": total,
            "eligible_now": eligible,
            "in_cooldown": cooling,
            "next_group_due_at": next_due
        }
    finally:
        conn.close()


def get_recent_buyers(limit: int = 20, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve recent buyer leads ordered by newest message date."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, message_id, chat_id, chat_title, sender_id,
                   sender_name, sender_username, date, need,
                   budget, quantity, urgency, confidence,
                   evidence, raw_text, message_link, created_at
            FROM buyers
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Get total counts for messages, buyers, and tracked channels."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM messages")
        msg_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM buyers")
        buyer_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scrape_state")
        state_count = cur.fetchone()[0]
        return {
            "total_messages": msg_count,
            "total_buyers": buyer_count,
            "tracked_chats": state_count
        }
    finally:
        conn.close()


def run_integrity_check(db_path: Optional[Path] = None) -> str:
    """Execute SQLite integrity check."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA integrity_check")
        return cur.fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    status = run_integrity_check()
    counts = get_counts()
    print(f"Database initialized. Integrity: {status}. Total DB State: {counts}")
