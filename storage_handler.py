"""
Storage Handler for Raw Telegram Messages & LLM Structured Leads
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/storage_handler.py
"""

import json
import csv
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    from config import RAW_MESSAGES_FILE, JSON_OUTPUT_FILE, CSV_OUTPUT_FILE
except ImportError:
    BASE_DIR = Path("/home/mdkamruzzamanirak_gmail_com/telegram-bot")
    RAW_MESSAGES_FILE = BASE_DIR / "raw_messages.json"
    JSON_OUTPUT_FILE = BASE_DIR / "buyers.json"
    CSV_OUTPUT_FILE = BASE_DIR / "buyers.csv"


class StorageHandler:
    def __init__(
        self,
        raw_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
        csv_path: Optional[Path] = None
    ):
        self.raw_path = Path(raw_path or RAW_MESSAGES_FILE)
        self.json_path = Path(json_path or JSON_OUTPUT_FILE)
        self.csv_path = Path(csv_path or CSV_OUTPUT_FILE)

    def save_raw_messages(self, raw_messages: List[Dict[str, Any]]) -> int:
        """
        RAW DATA LAYER: Persist 100% of raw Telegram messages without any filtering or classification.
        Enables historical auditing and LLM re-classification at any time.
        """
        if not raw_messages:
            return 0

        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if self.raw_path.exists():
            try:
                with open(self.raw_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        existing = data
            except (json.JSONDecodeError, OSError):
                existing = []

        existing_map = {(m.get("chat_id"), m.get("message_id")): m for m in existing}
        for rm in raw_messages:
            key = (rm.get("chat_id"), rm.get("message_id"))
            existing_map[key] = rm

        all_raw = list(existing_map.values())
        with open(self.raw_path, "w", encoding="utf-8") as f:
            json.dump(all_raw, f, ensure_ascii=False, indent=2)

        # Also persist to SQLite database
        try:
            import database
            for rm in raw_messages:
                database.insert_raw_message(rm)
        except Exception:
            pass

        return len(all_raw)

    def load_existing_buyers(self) -> List[Dict[str, Any]]:
        """Load existing structured buyer records from JSON file."""
        if not self.json_path.exists():
            return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def save_buyers(self, buyers: List[Dict[str, Any]]) -> int:
        """
        Persist structured LLM-classified buyer leads into JSON and CSV.
        Deduplicates by (chat_id, message_id).
        """
        existing = self.load_existing_buyers()
        existing_map = {(b.get("chat_id"), b.get("message_id")): b for b in existing}

        for b in buyers:
            key = (b.get("chat_id"), b.get("message_id"))
            if key not in existing_map:
                existing_map[key] = b
            else:
                existing_map[key].update(b)

        all_records = list(existing_map.values())

        # Ensure directories exist
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        # Save to JSON
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=2)

        # Save to CSV
        fieldnames = [
            "message_id",
            "chat_id",
            "chat_title",
            "date",
            "sender_id",
            "sender_name",
            "sender_username",
            "buyer",
            "need",
            "budget",
            "quantity",
            "urgency",
            "confidence",
            "evidence",
            "raw_text",
            "message_link"
        ]

        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in all_records:
                writer.writerow(r)

        # Also persist buyers to SQLite database
        try:
            import database
            for b in buyers:
                database.insert_buyer(b)
        except Exception:
            pass

        return len(all_records)
