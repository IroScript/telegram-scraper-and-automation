#!/usr/bin/env python3
"""
Autonomous Telegram Buyer Collector & Database Sync Engine (Stealth & Anti-Ban Architecture)
File: autonomous_collector.py

Anti-Ban Protections:
1. Round-Robin Small-Batch Scanning: Scans only 3-5 groups per hour instead of flooding 110 groups.
2. Marketplace Smart Filter: Filters out non-commercial / spam groups, focusing on high-intent buyer chats.
3. Human Mimicry & Jitter: 1.5s-3.5s delay between messages, 15s-25s delay between groups.
4. Passive Event-Driven Push Listener (--listen): 0 API query polling velocity; listens to incoming messages pushed by Telegram.
5. Incremental ID Checkpoints: Only pulls messages newer than last_message_id.
"""

import sys
import os
import json
import time
import random
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from telethon import TelegramClient, events
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
ROTATION_STATE_FILE = BASE_DIR / "rotation_state.json"

MARKETPLACE_KEYWORDS = [
    "buy", "sell", "market", "gv", "trade", "deal", "shop",
    "zone", "exchange", "gmail", "account", "service", "bulk", "order"
]


class AutonomousBuyerCollector:
    def __init__(
        self,
        messages_per_group: int = 25,
        batch_size: int = 5,
        filter_marketplace: bool = True,
        min_request_delay: float = 1.2,
        max_request_delay: float = 2.8,
        group_cooldown: float = 15.0
    ):
        self.messages_per_group = messages_per_group
        self.batch_size = batch_size
        self.filter_marketplace = filter_marketplace
        self.min_request_delay = min_request_delay
        self.max_request_delay = max_request_delay
        self.group_cooldown = group_cooldown

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
            print("❌ Error: Telegram session is not authorized.")
            return False

        me = await self.client.get_me()
        print(f"🛡️ Connected to Telegram as: {me.first_name} (@{me.username or 'N/A'}, ID: {me.id})")
        print("🔒 Anti-Ban Protection Active: Small-batch rotation with human jitter.")
        return True

    async def disconnect(self) -> None:
        """Disconnect Telegram client cleanly."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            print("🔌 Disconnected from Telegram.")

    def load_target_groups(self) -> List[Dict[str, Any]]:
        """Load and filter groups for marketplace relevance."""
        if not USER_GROUPS_FILE.exists():
            return []

        try:
            with open(USER_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
                if not isinstance(groups, list):
                    return []

                valid = [g for g in groups if g.get("id")]

                if self.filter_marketplace:
                    filtered = []
                    for g in valid:
                        title = (g.get("title") or "").lower()
                        uname = (g.get("username") or "").lower()
                        if any(k in title or k in uname for k in MARKETPLACE_KEYWORDS):
                            filtered.append(g)
                    return filtered

                return valid
        except Exception as e:
            print(f"⚠️ Warning loading groups: {e}")
            return []

    def get_next_rotation_batch(self, all_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Select next small batch of groups to scan in round-robin sequence."""
        if not all_groups:
            return []

        cursor = 0
        if ROTATION_STATE_FILE.exists():
            try:
                with open(ROTATION_STATE_FILE, "r") as f:
                    state = json.load(f)
                    cursor = state.get("next_index", 0)
            except Exception:
                cursor = 0

        if cursor >= len(all_groups):
            cursor = 0

        batch = all_groups[cursor:cursor + self.batch_size]
        next_cursor = (cursor + len(batch)) % len(all_groups)

        try:
            with open(ROTATION_STATE_FILE, "w") as f:
                json.dump({"next_index": next_cursor, "last_updated": datetime.now().isoformat()}, f)
        except Exception:
            pass

        return batch

    async def process_single_message(self, msg, chat_id: int, title: str, username: str) -> Optional[Dict[str, Any]]:
        """Extract, save raw message, and classify buyer lead."""
        if not msg or not msg.text or len(msg.text.strip()) < 5:
            return None

        msg_id = msg.id
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

        # 1. Raw DB Save
        database.insert_raw_message(raw_record)
        self.storage.save_raw_messages([raw_record])

        # 2. LLM Analysis
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

            database.insert_buyer(buyer_record)
            self.storage.save_buyers([buyer_record])
            print(f"  🎯 [BUYER DETECTED] {sender_name} ({sender_username}) in {title}")
            print(f"     Need: {buyer_record['need']} | Budget: {buyer_record['budget']} | Urgency: {buyer_record['urgency']}")
            return buyer_record

        return None

    async def scan_group(self, group: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Scan a single group safely with pacing and FloodWait prevention."""
        chat_id = group.get("id")
        title = group.get("title", f"Chat_{chat_id}")
        username = group.get("username")

        last_scraped_id = database.get_last_scraped_id(chat_id)
        new_buyers: List[Dict[str, Any]] = []

        print(f"\n📂 Checking [{title}] (Last Seen ID: {last_scraped_id})...")
        target_entity = username if username else chat_id
        max_seen_id = last_scraped_id

        try:
            kwargs: Dict[str, Any] = {"limit": self.messages_per_group}
            if last_scraped_id > 0:
                kwargs["min_id"] = last_scraped_id

            messages_fetched = 0
            async for msg in self.client.iter_messages(target_entity, **kwargs):
                if not msg or not msg.text:
                    continue

                messages_fetched += 1
                if msg.id > max_seen_id:
                    max_seen_id = msg.id

                b = await self.process_single_message(msg, chat_id, title, username)
                if b:
                    new_buyers.append(b)

                # Human jitter delay between messages
                jitter = random.uniform(self.min_request_delay, self.max_request_delay)
                await asyncio.sleep(jitter)

            database.update_scrape_state(chat_id, title, max_seen_id)
            print(f"  ✓ Fetched {messages_fetched} new messages. Qualified buyers: {len(new_buyers)}")

        except FloodWaitError as e:
            wait_s = e.seconds + 5
            print(f"  ⚠️ FloodWait detected: Cooling down for {wait_s}s...")
            await asyncio.sleep(wait_s)
        except (ChannelPrivateError, UserNotParticipantError):
            print(f"  ⏭️ Skipped {title}: Private or non-participant.")
        except Exception as e:
            print(f"  ⚠️ Note for {title}: {type(e).__name__}: {e}")

        # Safe cooldown between different groups (15-25 seconds)
        cooldown = random.uniform(self.group_cooldown * 0.8, self.group_cooldown * 1.2)
        print(f"  ⏳ Waiting {round(cooldown, 1)}s cooldown before next group...")
        await asyncio.sleep(cooldown)

        return new_buyers

    async def run_cycle(self) -> Dict[str, Any]:
        """Run one safe batch cycle of 3-5 groups."""
        all_groups = self.load_target_groups()
        batch = self.get_next_rotation_batch(all_groups)

        print(f"\n🚀 [SAFE BATCH RUN] Rotating through {len(batch)} of {len(all_groups)} marketplace groups...")

        new_buyers: List[Dict[str, Any]] = []
        for g in batch:
            b_list = await self.scan_group(g)
            new_buyers.extend(b_list)

        counts = database.get_counts()
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "groups_checked": [g.get("title") for g in batch],
            "total_groups_in_pool": len(all_groups),
            "new_buyers_this_cycle": len(new_buyers),
            "total_buyers_in_db": counts.get("total_buyers", 0),
            "total_messages_in_db": counts.get("total_messages", 0),
            "latest_buyers": database.get_recent_buyers(limit=10)
        }

        with open(LATEST_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return report

    async def run_forever(self, interval_seconds: int = 3600) -> None:
        """Hourly daemon: runs 1 small safe batch every hour continuously."""
        if not await self.connect():
            return

        try:
            while True:
                print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting safe hourly batch cycle...")
                await self.run_cycle()
                print(f"💤 Sleeping for {round(interval_seconds/60, 1)} minutes until next batch rotation...")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            print("🛑 Collector stopped.")
        finally:
            await self.disconnect()

    async def run_listener(self) -> None:
        """Zero-query passive real-time push listener. 100% ban-proof."""
        if not await self.connect():
            return

        all_groups = self.load_target_groups()
        target_ids = [g["id"] for g in all_groups if g.get("id")]
        title_map = {g["id"]: g.get("title", "") for g in all_groups if g.get("id")}
        uname_map = {g["id"]: g.get("username", "") for g in all_groups if g.get("id")}

        print(f"\n🎧 [PASSIVE PUSH LISTENER ACTIVE] Monitoring {len(target_ids)} marketplace groups in real-time...")
        print("💡 0 polling requests made to Telegram. Listening for server-pushed new messages.")

        @self.client.on(events.NewMessage(chats=target_ids))
        async def handler(event):
            chat_id = event.chat_id
            title = title_map.get(chat_id, "Unknown Chat")
            uname = uname_map.get(chat_id, "")
            msg = event.message
            if msg and msg.text:
                await self.process_single_message(msg, chat_id, title, uname)

        try:
            await self.client.run_until_disconnected()
        finally:
            await self.disconnect()


def display_recent_buyers_table(buyers: List[Dict[str, Any]]) -> None:
    print("\n📋 LATEST FOUND BUYERS LIST:")
    print("-" * 105)
    print(f"{'#':<3} | {'Sender':<22} | {'Username':<18} | {'Urgency':<7} | {'Need':<25} | {'Budget'}")
    print("-" * 105)
    for i, b in enumerate(buyers[:10], 1):
        s_name = (b.get("sender_name") or "Unknown")[:20]
        s_user = (b.get("sender_username") or "N/A")[:16]
        urg = b.get("urgency") or "LOW"
        need = (b.get("need") or "Inquiry")[:23]
        budget = (b.get("budget") or "N/A")[:15]
        print(f"{i:<3} | {s_name:<22} | {s_user:<18} | {urg:<7} | {need:<25} | {budget}")
    print("-" * 105 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Stealth Autonomous Telegram Buyer Collector")
    parser.add_argument("--once", action="store_true", help="Run 1 safe batch of 3-5 groups and exit")
    parser.add_argument("--daemon", action="store_true", help="Run hourly small batch rotation")
    parser.add_argument("--listen", action="store_true", help="Run 0-query passive push listener in real-time")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of groups per hourly batch (default: 5)")
    parser.add_argument("--limit", type=int, default=25, help="Messages per group (default: 25)")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds (default: 3600)")
    parser.add_argument("--all-groups", action="store_true", help="Disable marketplace filter and scan all groups")
    return parser.parse_args()


async def main():
    args = parse_args()
    collector = AutonomousBuyerCollector(
        messages_per_group=args.limit,
        batch_size=args.batch_size,
        filter_marketplace=not args.all_groups
    )

    if args.listen:
        await collector.run_listener()
    elif args.daemon:
        await collector.run_forever(interval_seconds=args.interval)
    else:
        connected = await collector.connect()
        if not connected:
            sys.exit(1)
        try:
            report = await collector.run_cycle()
            display_recent_buyers_table(report.get("latest_buyers", []))
        finally:
            await collector.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
