#!/usr/bin/env python3
"""
Autonomous Telegram Buyer Collector & Persistent SQLite Scheduler
File: autonomous_collector.py

Architectural Standards & Fault-Tolerant Exception Hierarchy:
- Architecture-First: Docker / Host Supervisor -> Single Worker Lock -> SQLite WAL -> Persistent 110-Group State
- Server response handling:
  ├── Success -> checkpoint DB + post-group pause
  ├── FloodWait -> server wait (e.seconds) -> DB global pause -> sleep -> resume
  ├── Network error -> exponential retry (3 attempts) -> checkpoint as retryable -> advance
  ├── Auth/session error -> FATAL STOP + alert (no infinite spin on bad session)
  └── Unknown error -> checkpoint DB error status + cooldown -> next eligible group
- Cooldown -> Next eligible group -> FOREVER RESUME LOOP
- Operational Pacing: All delays are operational pacing, NOT guarantees against server-side abuse detection.
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
    SessionPasswordNeededError,
    AuthKeyInvalidError,
    AuthKeyDuplicatedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    RPCError
)

from config import API_ID, API_HASH, BASE_DIR
import database
from storage_handler import StorageHandler
from llm_analyzer import LLMBuyerAnalyzer
from lock_manager import SingleWorkerLock

SESSION_NAME = str(BASE_DIR / "tg_buyer_session")
USER_GROUPS_FILE = BASE_DIR / "user_groups.json"
LATEST_REPORT_FILE = BASE_DIR / "latest_buyer_report.json"

MARKETPLACE_KEYWORDS = [
    "buy", "sell", "market", "gv", "trade", "deal", "shop",
    "zone", "exchange", "gmail", "account", "service", "bulk", "order"
]


class FatalAuthError(Exception):
    """Raised when Telegram session credentials or authorization are revoked."""
    pass


class AutonomousBuyerCollector:
    def __init__(
        self,
        messages_per_group: int = 15,
        filter_marketplace: bool = True,
        min_request_delay: float = 1.0,
        max_request_delay: float = 4.5,
        min_group_cooldown: float = 20.0,
        max_group_cooldown: float = 120.0,
        group_cycle_hours: float = 2.0
    ):
        self.messages_per_group = messages_per_group
        self.filter_marketplace = filter_marketplace
        self.min_request_delay = min_request_delay
        self.max_request_delay = max_request_delay
        self.min_group_cooldown = min_group_cooldown
        self.max_group_cooldown = max_group_cooldown
        self.group_cycle_seconds = int(group_cycle_hours * 3600)

        database.init_db()
        self.storage = StorageHandler()
        self.analyzer = LLMBuyerAnalyzer()
        self.client: Optional[TelegramClient] = None

    async def connect(self) -> bool:
        """Connect and verify Telegram user session."""
        if not API_ID or not API_HASH:
            print("❌ Error: API_ID or API_HASH missing in configuration.")
            return False

        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise FatalAuthError("Telegram session is not authorized. Re-authentication required.")

        me = await self.client.get_me()
        print(f"📡 Connected to Telegram as: {me.first_name} (@{me.username or 'N/A'}, ID: {me.id})")
        return True

    async def disconnect(self) -> None:
        """Disconnect Telegram client cleanly."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            print("🔌 Disconnected from Telegram.")

    def sync_target_groups_to_db(self) -> int:
        """Sync groups from user_groups.json into persistent SQLite scrape_state table."""
        if not USER_GROUPS_FILE.exists():
            return 0

        try:
            with open(USER_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
                if not isinstance(groups, list):
                    return 0

                valid = [g for g in groups if g.get("id")]
                if self.filter_marketplace:
                    filtered = []
                    for g in valid:
                        title = (g.get("title") or "").lower()
                        uname = (g.get("username") or "").lower()
                        if any(k in title or k in uname for k in MARKETPLACE_KEYWORDS):
                            filtered.append(g)
                    valid = filtered

                return database.register_groups(valid)
        except Exception as e:
            print(f"⚠️ Warning syncing target groups: {e}")
            return 0

    async def process_single_message(self, msg, chat_id: int, title: str, username: str) -> Optional[Dict[str, Any]]:
        """Store raw message in SQLite and classify via LLM fallback matrix."""
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

        # 1. Raw Message Layer (SQLite + JSON)
        database.insert_raw_message(raw_record)
        self.storage.save_raw_messages([raw_record])

        # 2. Semantic Analysis
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
            print(f"  🎯 [BUYER IDENTIFIED] {sender_name} ({sender_username}) in {title}")
            print(f"     Need: {buyer_record['need']} | Budget: {buyer_record['budget']} | Urgency: {buyer_record['urgency']}")
            return buyer_record

        return None

    async def scan_single_group(self, group: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scan one eligible group adhering strictly to the fault-tolerant exception hierarchy:
        - FloodWait -> server wait (e.seconds) -> global DB pause -> sleep -> resume
        - Network error -> exponential retry (3 attempts)
        - Auth/session error -> FATAL STOP + alert
        - Unknown error -> checkpoint DB error status + cooldown -> next group
        """
        chat_id = group["chat_id"]
        title = group["chat_title"]
        username = group.get("chat_username")
        last_scraped_id = group.get("last_message_id", 0)

        print(f"\n📂 Processing Group: [{title}] (ID: {chat_id}, Last Checkpoint ID: {last_scraped_id})")
        target_entity = username if username else chat_id
        max_seen_id = last_scraped_id
        new_buyers = []

        # 1. Network Retry Loop (Up to 3 attempts with exponential backoff)
        max_net_retries = 3
        messages_fetched = 0

        for attempt in range(1, max_net_retries + 1):
            try:
                kwargs: Dict[str, Any] = {"limit": self.messages_per_group}
                if last_scraped_id > 0:
                    kwargs["min_id"] = last_scraped_id

                async for msg in self.client.iter_messages(target_entity, **kwargs):
                    if not msg or not msg.text:
                        continue

                    messages_fetched += 1
                    if msg.id > max_seen_id:
                        max_seen_id = msg.id

                    b = await self.process_single_message(msg, chat_id, title, username or "")
                    if b:
                        new_buyers.append(b)

                    # Per-request pacing: 1.0s to 4.5s randomized delay
                    pacing_delay = random.uniform(self.min_request_delay, self.max_request_delay)
                    await asyncio.sleep(pacing_delay)

                # Fetch succeeded
                break

            except (AuthKeyInvalidError, AuthKeyDuplicatedError, UserDeactivatedError, UserDeactivatedBanError, SessionPasswordNeededError) as auth_err:
                # Fatal session error: Halt immediately to prevent session corruption
                raise FatalAuthError(f"Fatal Telegram authentication/session error: {auth_err}")

            except FloodWaitError as e:
                server_wait = getattr(e, "seconds", 60)
                print(f"  🚨 [SERVER FLOODWAIT] Telegram mandates waiting {server_wait} seconds.")
                database.set_global_flood_wait(server_wait + 10)
                print(f"  💤 Sleeping for exact server wait period: {server_wait + 5}s...")
                await asyncio.sleep(server_wait + 5)
                return new_buyers

            except (ChannelPrivateError, UserNotParticipantError):
                print(f"  ⏭️ Skipped {title}: Channel private or account is not a participant.")
                database.update_group_checkpoint(chat_id, max_seen_id, 86400, status="unauthorized", error="Not participant")
                return new_buyers

            except ChatAdminRequiredError:
                print(f"  ⏭️ Skipped {title}: Admin rights required to read.")
                database.update_group_checkpoint(chat_id, max_seen_id, 86400, status="admin_required", error="Admin required")
                return new_buyers

            except (ConnectionError, asyncio.TimeoutError, RPCError) as net_err:
                if attempt < max_net_retries:
                    backoff = attempt * 4.0 + random.uniform(1.0, 3.0)
                    print(f"  🔄 [NETWORK ERROR {attempt}/{max_net_retries}] {title}: {net_err}. Exponential backoff {round(backoff, 1)}s...")
                    await asyncio.sleep(backoff)
                else:
                    print(f"  ❌ [NETWORK EXHAUSTED] {title} failed after {max_net_retries} attempts.")
                    database.update_group_checkpoint(chat_id, max_seen_id, 1800, status="network_fail", error=str(net_err))
                    return new_buyers

            except Exception as e:
                print(f"  ⚠️ [UNKNOWN ERROR] {title}: {type(e).__name__}: {e}")
                database.update_group_checkpoint(chat_id, max_seen_id, 3600, status="error", error=str(e))
                return new_buyers

        # Success checkpointing: post-group cooldown and scheduled next eligibility
        cycle_cooldown = self.group_cycle_seconds + random.randint(60, 600)
        database.update_group_checkpoint(
            chat_id=chat_id,
            last_message_id=max_seen_id,
            cooldown_seconds=cycle_cooldown,
            status="idle",
            error=None
        )
        print(f"  ✓ Fetched {messages_fetched} messages. Qualified buyers: {len(new_buyers)}")
        print(f"  ⏳ Group scheduled next check in ~{round(cycle_cooldown/3600, 1)} hours.")

        # Inter-group randomized operational pause (15s to 120s)
        group_pause = random.uniform(self.min_group_cooldown, self.max_group_cooldown)
        print(f"  ⏸️ Inter-group pause: {round(group_pause, 1)}s before evaluating scheduler...")
        await asyncio.sleep(group_pause)

        return new_buyers

    async def run_worker_loop(self, max_cycles: Optional[int] = None) -> None:
        """
        Continuous stateful scheduler loop.
        Never exits on non-fatal group errors; logs, checkpoints cooldown, and advances to next group.
        Only halts on FatalAuthError to alert operator.
        """
        if not await self.connect():
            return

        self.sync_target_groups_to_db()
        cycles_completed = 0

        print("\n🚀 [STATEFUL SCHEDULER LOOP STARTED]")
        print("💡 Architecture: Forever resume loop driven by persistent SQLite state machine.")

        try:
            while True:
                if max_cycles and cycles_completed >= max_cycles:
                    print(f"Completed requested {max_cycles} cycles. Exiting.")
                    break

                eligible_group = database.get_next_eligible_group()

                if not eligible_group:
                    summary = database.get_schedule_summary()
                    next_due = summary.get("next_group_due_at")
                    print(f"💤 All groups currently cooling down. Next group eligible at: {next_due}")
                    print("Sleeping 60 seconds before re-checking scheduler queue...")
                    await asyncio.sleep(60)
                    continue

                try:
                    await self.scan_single_group(eligible_group)
                    cycles_completed += 1

                except FatalAuthError as fatal:
                    print(f"\n🛑 [FATAL AUTH STOP] {fatal}")
                    print("Halting collector worker loop immediately to protect session. Operator intervention required.")
                    break

                except Exception as loop_err:
                    print(f"⚠️ [ISOLATED WORKER ERROR] Loop caught {type(loop_err).__name__}: {loop_err}")
                    database.update_group_checkpoint(
                        chat_id=eligible_group["chat_id"],
                        last_message_id=eligible_group.get("last_message_id", 0),
                        cooldown_seconds=1800,
                        status="error_isolated",
                        error=str(loop_err)
                    )
                    cycles_completed += 1

        except asyncio.CancelledError:
            print("🛑 Collector worker received cancellation signal. Cleaning up.")
        finally:
            await self.disconnect()

    async def run_passive_listener(self) -> None:
        """Isolated Passive Push Listener: 0 polling queries, listens to server-pushed updates."""
        if not await self.connect():
            return

        self.sync_target_groups_to_db()
        conn = database.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT chat_id, chat_title, chat_username FROM scrape_state")
        registered = cur.fetchall()
        conn.close()

        chat_ids = [r["chat_id"] for r in registered]
        title_map = {r["chat_id"]: r["chat_title"] for r in registered}
        uname_map = {r["chat_id"]: r["chat_username"] or "" for r in registered}

        print(f"\n🎧 [PASSIVE PUSH LISTENER INITIALIZED] Listening to {len(chat_ids)} registered groups...")
        print("💡 Passive Architecture: 0 outbound polling requests. Real-time updates pushed by Telegram.")

        @self.client.on(events.NewMessage(chats=chat_ids))
        async def event_handler(event):
            chat_id = event.chat_id
            title = title_map.get(chat_id, "Unknown Group")
            uname = uname_map.get(chat_id, "")
            msg = event.message
            if msg and msg.text:
                await self.process_single_message(msg, chat_id, title, uname)

        try:
            await self.client.run_until_disconnected()
        finally:
            await self.disconnect()


def parse_args():
    parser = argparse.ArgumentParser(description="Autonomous Telegram Buyer Collector & Persistent SQLite Scheduler")
    parser.add_argument("mode", choices=["worker", "listener", "status", "sync"], nargs="?", default="status",
                        help="Operation mode: 'worker' (stateful scheduler), 'listener' (passive push), 'status' (DB summary), 'sync' (register groups)")
    parser.add_argument("--limit", type=int, default=15, help="Messages per group bounded batch (default: 15)")
    parser.add_argument("--cycles", type=int, default=None, help="Max group cycles to process (default: infinite)")
    parser.add_argument("--all-groups", action="store_true", help="Include all groups without marketplace keyword filter")
    return parser.parse_args()


async def main():
    args = parse_args()
    collector = AutonomousBuyerCollector(
        messages_per_group=args.limit,
        filter_marketplace=not args.all_groups
    )

    if args.mode == "status":
        database.init_db()
        status = database.run_integrity_check()
        counts = database.get_counts()
        summary = database.get_schedule_summary()
        print("\n📊 TELEGRAM BUYER COLLECTOR SYSTEM STATUS")
        print("=" * 60)
        print(f"SQLite DB Integrity: {status}")
        print(f"Total Stored Messages: {counts.get('total_messages')}")
        print(f"Total Qualified Buyers: {counts.get('total_buyers')}")
        print(f"Registered Groups in Scheduler: {summary.get('total_registered_groups')}")
        print(f"Groups Eligible for Query Now: {summary.get('eligible_now')}")
        print(f"Groups in Cooldown: {summary.get('in_cooldown')}")
        print(f"Next Group Due At: {summary.get('next_group_due_at')}")
        print("=" * 60 + "\n")
        return

    if args.mode == "sync":
        database.init_db()
        n = collector.sync_target_groups_to_db()
        print(f"Synchronized {n} groups into SQLite scrape_state.")
        return

    # For active worker or listener modes, enforce SingleWorkerLock
    with SingleWorkerLock() as _:
        if args.mode == "worker":
            await collector.run_worker_loop(max_cycles=args.cycles)
        elif args.mode == "listener":
            await collector.run_passive_listener()


if __name__ == "__main__":
    asyncio.run(main())
