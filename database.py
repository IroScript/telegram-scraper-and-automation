"""
SQLite Database Layer for Telegram Messages and Buyer Leads
File: database.py
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

DB_FILE = Path(__file__).parent.resolve() / "buyers.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = Path(db_path or DB_FILE)
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize SQLite tables for messages, buyers, and scraping checkpoints."""
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

    # 3. Channel Scrape Checkpoint Table (for incremental fetching)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scrape_state (
            chat_id INTEGER PRIMARY KEY,
            chat_title TEXT,
            last_message_id INTEGER DEFAULT 0,
            last_scraped_at TIMESTAMP
        )
    """)

    # Indices for high-speed queries
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, message_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_buyers_urgency ON buyers(urgency)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_buyers_created ON buyers(created_at)")

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
        inserted = cur.rowcount > 0
        return inserted
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


def get_last_scraped_id(chat_id: int, db_path: Optional[Path] = None) -> int:
    """Retrieve the last scraped message ID for a given chat."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT last_message_id FROM scrape_state WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return row["last_message_id"] if row else 0
    finally:
        conn.close()


def update_scrape_state(chat_id: int, chat_title: str, last_message_id: int, db_path: Optional[Path] = None) -> None:
    """Update checkpoint for a chat to avoid duplicate reads."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO scrape_state (chat_id, chat_title, last_message_id, last_scraped_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_title = excluded.chat_title,
                last_message_id = MAX(scrape_state.last_message_id, excluded.last_message_id),
                last_scraped_at = excluded.last_scraped_at
        """, (chat_id, chat_title, last_message_id, datetime.now().isoformat()))
        conn.commit()
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


def migrate_json_to_sqlite(base_dir: Optional[Path] = None) -> Dict[str, int]:
    """Migrate existing raw_messages.json and buyers.json into buyers.db."""
    dir_path = Path(base_dir or DB_FILE.parent)
    init_db(dir_path / "buyers.db")

    raw_file = dir_path / "raw_messages.json"
    buyers_file = dir_path / "buyers.json"

    raw_migrated = 0
    buyers_migrated = 0

    if raw_file.exists():
        try:
            with open(raw_file, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
                if isinstance(raw_list, list):
                    for msg in raw_list:
                        if insert_raw_message(msg, dir_path / "buyers.db"):
                            raw_migrated += 1
        except Exception:
            pass

    if buyers_file.exists():
        try:
            with open(buyers_file, "r", encoding="utf-8") as f:
                buyers_list = json.load(f)
                if isinstance(buyers_list, list):
                    for b in buyers_list:
                        if insert_buyer(b, dir_path / "buyers.db"):
                            buyers_migrated += 1
        except Exception:
            pass

    return {
        "raw_migrated": raw_migrated,
        "buyers_migrated": buyers_migrated
    }


if __name__ == "__main__":
    init_db()
    res = migrate_json_to_sqlite()
    counts = get_counts()
    print(f"Database initialized. Migrated: {res}. Total DB State: {counts}")
