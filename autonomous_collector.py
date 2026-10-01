#!/usr/bin/env python3
"""
Autonomous Telegram Buyer Collector & Database Sync Engine
File: autonomous_collector.py

Features:
- Telethon MTProto User Client session integration
- Scans all target groups/channels incrementally (only fetches new messages using min_id)
- Saves 100% raw messages into SQLite DB (buyers.db -> messages) and raw_messages.json
- Classifies each new message using 20-Tier Fallback LLM Architecture (llm_analyzer.py)
- Inserts qualified buyers into SQLite DB (buyers.db -> buyers), buyers.json, and buyers.csv
- Anti-ban safe delays & FloodWait handler
- Can run as a single cycle (--once) or continuous hourly daemon (--daemon)
- Produces latest found buyer report at the end of every cycle
"""

import sys
import os
import json
import time
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UserNotParticipantError,
    ChatAdminRequiredError,
    RPCError
)

from config import API_ID, API_HASH, BASE_DIR
import database
from storage_handler import StorageHandler
from llm_analyzer import LLMBuyerAnalyzer

SESSION_NAME = str(BASE_DIR / "tg_buyer_session")
USER_GROUPS_FILE = BASE_DIR / "user_groups.json"
LATEST_REPORT_FILE = BASE_DIR / "latest_buyer_report.json"


class AutonomousBuyerCollector:
    def __init__(
        self,
        messages_per_group: int = 35,
        max_groups: Optional[int] = None,
        request_delay: float = 0.6,
        group_delay: float = 2.0
    ):
        self.messages_per_group = messages_per_group
        self.max_groups = max_groups
        self.request_delay = request_delay
        self.group_delay = group_delay

        database.init_db()
        self.storage = StorageHandler()
        self.analyzer = LLMBuyerAnalyzer()
        self.client: Optional[TelegramClient] = None

    async def connect(self) -> bool:
        """Connect and verify Telegram user session."""
        if not API_ID or not API_HASH:
            print("❌ Error: API_ID or API_HASH missing in config.")
            return False

        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            print("❌ Error: Telegram session is not authorized. Run auth_telegram.py first.")
            return False

        me = await self.client.get_me()
        print(f"✅ Connected to Telegram as: {me.first_name} (@{me.username or 'N/A'}, ID: {me.id})")
        return True

    async def disconnect(self) -> None:
        """Disconnect Telegram client cleanly."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            print("🔌 Disconnected from Telegram.")

    def load_target_groups(self) -> List[Dict[str, Any]]:
        """Load target groups from user_groups.json or fallback list."""
        if USER_GROUPS_FILE.exists():
            try:
                with open(USER_GROUPS_FILE, "r", encoding="utf-8") as f:
                    groups = json.load(f)
                    if isinstance(groups, list):
                        # Filter to only groups / supergroups / channels that have valid IDs
                        valid = [g for g in groups if g.get("id")]
                        if self.max_groups:
                            valid = valid[:self.max_groups]
                        return valid
            except Exception as e:
                print(f"⚠️ Warning loading user_groups.json: {e}")

        return []

    async def scan_group(self, group: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Scan a single group for new messages since last check."""
        chat_id = group.get("id")
        title = group.get("title", f"Chat_{chat_id}")
        username = group.get("username")

        last_scraped_id = database.get_last_scraped_id(chat_id)
        new_buyers_in_chat: List[Dict[str, Any]] = []

        print(f"\n📂 Scanning: {title} (ID: {chat_id}, Last Seen Msg ID: {last_scraped_id})")

        target_entity = username if username else chat_id
        max_seen_id = last_scraped_id

        try:
            # If last_scraped_id > 0, fetch messages newer than min_id
            kwargs: Dict[str, Any] = {"limit": self.messages_per_group}
            if last_scraped_id > 0:
                kwargs["min_id"] = last_scraped_id

            messages_fetched = 0
            async for msg in self.client.iter_messages(target_entity, **kwargs):
                if not msg or not msg.text:
                    continue

                messages_fetched += 1
                msg_id = msg.id
                if msg_id > max_seen_id:
                    max_seen_id = msg_id

                sender = await msg.get_sender()
                sender_id = getattr(sender, "id", msg.sender_id)
                first_name = getattr(sender, "first_name", "") or ""
                last_name = getattr(sender, "last_name", "") or ""
                sender_name = f"{first_name} {last_name}".strip() or "Unknown"
                sender_username = f"@{getattr(sender, 'username', '')}" if getattr(sender, "username", None) else ""

                msg_date = msg.date.strftime("%Y-%m-%d %H:%M:%S") if msg.date else ""
                clean_username = username.replace("@", "") if username else ""
                link = f"https://t.me/{clean_username}/{msg_id}" if clean_username else f"https://t.me/c/{abs(chat_id)}/{msg_id}"

                raw_record = {
                    "message_id": msg_id,
                    "chat_id": chat_id,
                    "chat_title": title,
                    "date": msg_date,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "sender_username": sender_username,
                    "raw_text": msg.text,
                    "message_link": link
                }

                # 1. Raw Storage Layer (DB + JSON)
                database.insert_raw_message(raw_record)
                self.storage.save_raw_messages([raw_record])

                # 2. LLM Semantic Classification Layer
                analysis = self.analyzer.analyze_message(msg.text)
                if analysis.get("buyer") is True:
                    buyer_record = {
                        "message_id": msg_id,
                        "chat_id": chat_id,
                        "chat_title": title,
                        "date": msg_date,
                        "sender_id": sender_id,
                        "sender_name": sender_name,
                        "sender_username": sender_username,
                        "buyer": True,
                        "need": analysis.get("need") or "",
                        "budget": analysis.get("budget") or "",
                        "quantity": analysis.get("quantity") or "",
                        "urgency": analysis.get("urgency") or "MEDIUM",
                        "confidence": analysis.get("confidence") or 0.8,
                        "evidence": analysis.get("evidence") or "",
                        "raw_text": msg.text,
                        "message_link": link
                    }

                    # Insert to DB and file storage
                    database.insert_buyer(buyer_record)
                    self.storage.save_buyers([buyer_record])
                    new_buyers_in_chat.append(buyer_record)

                    print(f"  🎯 [NEW BUYER DETECTED] {sender_name} ({sender_username})")
                    print(f"     Need: {buyer_record['need']} | Budget: {buyer_record['budget']} | Urgency: {buyer_record['urgency']}")

                # Anti-ban sleep between message calls
                await asyncio.sleep(self.request_delay)

            # Update checkpoint state
            database.update_scrape_state(chat_id, title, max_seen_id)
            print(f"  ✓ Processed {messages_fetched} new messages. New buyers found: {len(new_buyers_in_chat)}")

        except FloodWaitError as e:
            wait_s = e.seconds + 2
            print(f"  ⚠️ FloodWaitError: Sleeping for {wait_s} seconds to respect Telegram rate limits...")
            await asyncio.sleep(wait_s)
        except (ChannelPrivateError, UserNotParticipantError):
            print(f"  ⏭️ Skipped {title}: Channel private or account is not a participant.")
        except ChatAdminRequiredError:
            print(f"  ⏭️ Skipped {title}: Admin rights required to read messages.")
        except Exception as e:
            print(f"  ⚠️ Warning scraping {title}: {type(e).__name__}: {e}")

        # Pacing between groups
        await asyncio.sleep(self.group_delay)
        return new_buyers_in_chat

    async def run_cycle(self) -> Dict[str, Any]:
        """Run one full scanning cycle across all configured groups."""
        cycle_start = time.time()
        groups = self.load_target_groups()
        print(f"\n🚀 [CYCLE START] Scanning {len(groups)} groups for new buyer inquiries...")

        all_new_buyers: List[Dict[str, Any]] = []
        groups_scanned = 0

        for group in groups:
            groups_scanned += 1
            new_buyers = await self.scan_group(group)
            all_new_buyers.extend(new_buyers)

        elapsed = time.time() - cycle_start
        counts = database.get_counts()

        # Save latest cycle report
        report = {
            "cycle_timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "groups_scanned": groups_scanned,
            "new_buyers_found_this_cycle": len(all_new_buyers),
            "total_buyers_in_db": counts.get("total_buyers", 0),
            "total_messages_in_db": counts.get("total_messages", 0),
            "new_buyers": all_new_buyers,
            "latest_recent_buyers": database.get_recent_buyers(limit=15)
        }

        with open(LATEST_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 70)
        print("🏁 [CYCLE COMPLETE]")
        print(f"  Groups Scanned: {groups_scanned}/{len(groups)}")
        print(f"  New Buyers Found This Cycle: {len(all_new_buyers)}")
        print(f"  Total Buyers in Database: {counts.get('total_buyers', 0)}")
        print(f"  Total Messages in Database: {counts.get('total_messages', 0)}")
        print(f"  Elapsed Time: {round(elapsed, 2)}s")
        print("=" * 70)

        return report

    async def run_forever(self, interval_seconds: int = 3600) -> None:
        """Run continuously in an hourly cycle."""
        if not await self.connect():
            return

        try:
            while True:
                print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting hourly collection cycle...")
                await self.run_cycle()
                print(f"💤 Sleeping for {interval_seconds} seconds ({round(interval_seconds/60, 1)} minutes) until next cycle...")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            print("🛑 Autonomous collector stopped.")
        finally:
            await self.disconnect()


def display_recent_buyers_table(buyers: List[Dict[str, Any]]) -> None:
    """Print formatted summary of recent buyers to stdout."""
    print("\n📋 LATEST FOUND BUYERS LIST:")
    print("-" * 100)
    print(f"{'#':<3} | {'Sender':<22} | {'Username':<18} | {'Urgency':<7} | {'Need':<25} | {'Budget'}")
    print("-" * 100)
    for i, b in enumerate(buyers[:15], 1):
        s_name = (b.get("sender_name") or "Unknown")[:20]
        s_user = (b.get("sender_username") or "N/A")[:16]
        urg = b.get("urgency") or "LOW"
        need = (b.get("need") or "Inquiry")[:23]
        budget = (b.get("budget") or "N/A")[:15]
        print(f"{i:<3} | {s_name:<22} | {s_user:<18} | {urg:<7} | {need:<25} | {budget}")
    print("-" * 100 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Autonomous Telegram Buyer Collector & Database Sync Engine")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in an hourly loop")
    parser.add_argument("--interval", type=int, default=3600, help="Interval between cycles in seconds (default: 3600)")
    parser.add_argument("--limit", type=int, default=35, help="Message limit per group per cycle (default: 35)")
    parser.add_argument("--max-groups", type=int, default=None, help="Limit number of groups to scan (default: all)")
    return parser.parse_args()


async def main():
    args = parse_args()
    collector = AutonomousBuyerCollector(
        messages_per_group=args.limit,
        max_groups=args.max_groups
    )

    if args.daemon:
        await collector.run_forever(interval_seconds=args.interval)
    else:
        connected = await collector.connect()
        if not connected:
            sys.exit(1)
        try:
            report = await collector.run_cycle()
            recent = report.get("latest_recent_buyers", [])
            display_recent_buyers_table(recent)
        finally:
            await collector.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
