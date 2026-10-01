#!/usr/bin/env python3
"""
Autonomous Telegram Buyer Collector & Persistent REST-Mediated Scheduler
File: autonomous_collector.py

Architectural Standards & Fault-Tolerant Exception Hierarchy:
- Architecture-First: Docker / Host Supervisor -> Single Worker Lock -> Internal REST API -> SQLite WAL
- Strict Container & Storage Isolation: Collector NEVER directly accesses SQLite database files.
- All database state, raw messages, buyer leads, and scheduler state are mediated through telegram_database_api.
- Server response handling:
  ├── Success -> checkpoint API + post-group pause
  ├── FloodWait -> server wait (e.seconds) -> API global pause -> sleep -> resume
  ├── Network error -> exponential retry (3 attempts) -> checkpoint as retryable -> advance
  ├── Auth/session error -> FATAL STOP + alert (no infinite spin on bad session)
  └── Unknown error -> checkpoint API error status + cooldown -> next eligible group
- Cooldown -> Next eligible group -> FOREVER RESUME LOOP
- Operational Pacing: All delays are operational pacing, NOT guarantees against server-side abuse detection.
"""

import sys
import os
import json
import time
import random
import signal
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

def parse_db_utc_time(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse SQLite UTC timestamp string into timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        clean_ts = ts_str.replace("T", " ")
        if "." in clean_ts:
            clean_ts = clean_ts.split(".")[0]
        dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

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
from db_client import DatabaseApiClient, default_client
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
        group_cycle_hours: float = 2.0,
        db_client: Optional[DatabaseApiClient] = None,
        mock_mode: bool = False,
        mock_cooldown: Optional[float] = None,
        max_sleep_slice: float = 30.0
    ):
        self.messages_per_group = messages_per_group
        self.filter_marketplace = filter_marketplace
        self.min_request_delay = min_request_delay
        self.max_request_delay = max_request_delay
        self.min_group_cooldown = min_group_cooldown
        self.max_group_cooldown = max_group_cooldown
        self.group_cycle_seconds = int(group_cycle_hours * 3600)
        self.mock_mode = mock_mode or (os.getenv("TEST_MOCK_SCANNER", "0") == "1")
        self.mock_cooldown = mock_cooldown
        self.max_sleep_slice = float(os.getenv("SCHEDULER_MAX_SLICE", str(max_sleep_slice)))
        self.shutdown_event = asyncio.Event()

        # Pure REST API Client - Zero direct SQLite access
        self.db = db_client or default_client
        self.storage = StorageHandler()
        self.analyzer = LLMBuyerAnalyzer()
        self.client: Optional[TelegramClient] = None

    def handle_shutdown_signal(self, sig: int):
        """Handle OS termination signals gracefully without abrupt termination or lock leaks."""
        sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
        print(f"\n🛑 [SCHEDULER] Received termination signal ({sig_name}). Graceful shutdown initiated.")
        sys.stdout.flush()
        self.shutdown_event.set()

    async def connect(self) -> bool:
        """Connect and verify Telegram user session."""
        if self.mock_mode:
            print("📡 [MOCK MODE] Mock Telegram connection active (0 live network queries).")
            sys.stdout.flush()
            return True

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
        if self.mock_mode:
            print("🔌 [MOCK MODE] Disconnected mock Telegram connection.")
            sys.stdout.flush()
            return

        if self.client and self.client.is_connected():
            await self.client.disconnect()
            print("🔌 Disconnected from Telegram.")

    def sync_target_groups_to_db(self) -> int:
        """Sync groups from user_groups.json into persistent scrape_state table via REST API."""
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

                return self.db.register_groups(valid)
        except Exception as e:
            print(f"⚠️ Warning syncing target groups: {e}")
            return 0

    async def process_single_message(self, msg, chat_id: int, title: str, username: str) -> Optional[Dict[str, Any]]:
        """Store raw message via REST API and classify via LLM fallback matrix."""
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

        # 1. Raw Message Layer (API-mediated SQLite + JSON)
        self.db.insert_raw_message(raw_record)
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

            self.db.insert_buyer(buyer_record)
            self.storage.save_buyers([buyer_record])
            print(f"  🎯 [BUYER IDENTIFIED] {sender_name} ({sender_username}) in {title}")
            print(f"     Need: {buyer_record['need']} | Budget: {buyer_record['budget']} | Urgency: {buyer_record['urgency']}")
            return buyer_record

        return None

    async def _mock_scan_single_group(self, group: Dict[str, Any]) -> List[Dict[str, Any]]:
        chat_id = group["chat_id"]
        title = group["chat_title"]
        username = group.get("chat_username")
        last_scraped_id = group.get("last_message_id", 0)

        print(f"\n📂 [MOCK SCAN] Processing Group: [{title}] (ID: {chat_id}, Last Checkpoint ID: {last_scraped_id})")
        sys.stdout.flush()

        # Injected error testing
        inject = os.getenv("INJECT_ERROR", "")
        if inject == "timeout":
            os.environ.pop("INJECT_ERROR", None)
            raise asyncio.TimeoutError("Simulated network timeout")
        elif inject == "network":
            os.environ.pop("INJECT_ERROR", None)
            raise ConnectionError("Simulated network connection reset")
        elif inject == "auth":
            os.environ.pop("INJECT_ERROR", None)
            raise FatalAuthError("Simulated Telegram session revocation")
        elif inject == "unknown":
            os.environ.pop("INJECT_ERROR", None)
            raise RuntimeError("Simulated unexpected exception in group scanner")

        # Generate 5 mock messages
        new_buyers = []
        max_seen_id = last_scraped_id
        for i in range(1, 6):
            mid = last_scraped_id + i
            max_seen_id = mid
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            raw_record = {
                "message_id": mid,
                "chat_id": chat_id,
                "chat_title": title,
                "date": date_str,
                "sender_id": 999888 + (mid % 10),
                "sender_name": f"Mock Sender {mid}",
                "sender_username": f"@mock_user_{mid}",
                "raw_text": f"Looking to buy bulk accounts order {mid}",
                "message_link": f"https://t.me/c/{abs(chat_id)}/{mid}"
            }
            self.db.insert_raw_message(raw_record)
            if mid % 2 == 0:
                buyer_record = {
                    "message_id": mid,
                    "chat_id": chat_id,
                    "chat_title": title,
                    "date": date_str,
                    "sender_id": raw_record["sender_id"],
                    "sender_name": raw_record["sender_name"],
                    "sender_username": raw_record["sender_username"],
                    "buyer": True,
                    "need": "bulk accounts",
                    "budget": "$100",
                    "quantity": "50",
                    "urgency": "HIGH",
                    "confidence": 0.95,
                    "evidence": raw_record["raw_text"],
                    "raw_text": raw_record["raw_text"],
                    "message_link": raw_record["message_link"]
                }
                self.db.insert_buyer(buyer_record)
                new_buyers.append(buyer_record)

        cycle_cooldown = self.mock_cooldown if self.mock_cooldown is not None else self.group_cycle_seconds
        self.db.update_group_checkpoint(
            chat_id=chat_id,
            last_message_id=max_seen_id,
            cooldown_seconds=cycle_cooldown,
            status="idle",
            error=None
        )
        print(f"  ✓ [MOCK] Generated 5 messages (last_id={max_seen_id}), buyers={len(new_buyers)}")
        print(f"  ⏳ Group scheduled next check in ~{cycle_cooldown} seconds.")
        sys.stdout.flush()
        return new_buyers

    async def scan_single_group(self, group: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scan one eligible group adhering strictly to the fault-tolerant exception hierarchy:
        - FloodWait -> server wait (e.seconds) -> global DB pause -> sleep -> resume
        - Network error -> exponential retry (3 attempts)
        - Auth/session error -> FATAL STOP + alert
        - Unknown error -> checkpoint DB error status + cooldown -> next group
        """
        if self.mock_mode:
            return await self._mock_scan_single_group(group)

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
                self.db.set_global_flood_wait(server_wait + 10, reason=f"Telegram FloodWait {server_wait}s")
                print(f"  💤 Sleeping for exact server wait period: {server_wait + 5}s...")
                await asyncio.sleep(server_wait + 5)
                return new_buyers

            except (ChannelPrivateError, UserNotParticipantError):
                print(f"  ⏭️ Skipped {title}: Channel private or account is not a participant.")
                self.db.update_group_checkpoint(chat_id, max_seen_id, 86400, status="unauthorized", error="Not participant")
                return new_buyers

            except ChatAdminRequiredError:
                print(f"  ⏭️ Skipped {title}: Admin rights required to read.")
                self.db.update_group_checkpoint(chat_id, max_seen_id, 86400, status="admin_required", error="Admin required")
                return new_buyers

            except (ConnectionError, asyncio.TimeoutError, RPCError) as net_err:
                if attempt < max_net_retries:
                    backoff = attempt * 4.0 + random.uniform(1.0, 3.0)
                    print(f"  🔄 [NETWORK ERROR {attempt}/{max_net_retries}] {title}: {net_err}. Exponential backoff {round(backoff, 1)}s...")
                    await asyncio.sleep(backoff)
                else:
                    print(f"  ❌ [NETWORK EXHAUSTED] {title} failed after {max_net_retries} attempts.")
                    self.db.report_group_error(chat_id, error=str(net_err), cooldown_seconds=1800, status="network_fail")
                    return new_buyers

            except Exception as e:
                print(f"  ⚠️ [UNKNOWN ERROR] {title}: {type(e).__name__}: {e}")
                self.db.report_group_error(chat_id, error=str(e), cooldown_seconds=3600, status="error")
                return new_buyers

        # Success checkpointing: post-group cooldown and scheduled next eligibility
        cycle_cooldown = self.group_cycle_seconds + random.randint(60, 600)
        self.db.update_group_checkpoint(
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
        Permanent Non-Terminating Stateful Scheduler Loop mediated through REST API.

        System Loop Architecture:
        START -> LOAD STATE -> SELECT NEXT ELIGIBLE GROUP -> 
        (IF DUE: PROCESS WORK -> SAVE CHECKPOINT -> SCHEDULE NEXT ELIGIBLE TIME) -> 
        RETURN TO SCHEDULER -> WAIT (bounded sleep slices) -> REPEAT FOREVER

        Absolute Loop Rule:
        - NEVER exits on empty scheduler, all groups cooling down, 0 messages, 0 buyers, or transient errors.
        - Only exits on explicit SIGTERM/SIGINT shutdown or FatalAuthError.
        """
        # Ensure database API is online and healthy before beginning
        self.db.wait_for_health(timeout=30)

        if not await self.connect():
            return

        self.sync_target_groups_to_db()
        cycles_completed = 0

        print("\n🚀 [STATEFUL SCHEDULER LOOP STARTED]")
        print("💡 Architecture: Forever resume loop driven by REST API SQLite state machine.")
        sys.stdout.flush()

        try:
            while not self.shutdown_event.is_set():
                if max_cycles and cycles_completed >= max_cycles:
                    print(f"Completed requested {max_cycles} cycles. Exiting.")
                    sys.stdout.flush()
                    break

                try:
                    eligible_group = self.db.get_next_eligible_group()
                except Exception as api_err:
                    print(f"⚠️ [SCHEDULER API ERROR] Error querying next eligible group: {api_err}")
                    print(f"[SCHEDULER] Scheduler remains alive.")
                    print(f"RETURNING_TO_SCHEDULER=true")
                    sys.stdout.flush()
                    await asyncio.sleep(min(self.max_sleep_slice, 5.0))
                    continue

                if not eligible_group:
                    try:
                        summary = self.db.get_schedule_summary()
                    except Exception as sum_err:
                        print(f"⚠️ [SCHEDULER SUMMARY ERROR] Error querying summary: {sum_err}")
                        summary = {}

                    next_due = summary.get("next_group_due_at")
                    due_dt = parse_db_utc_time(next_due)
                    now = datetime.now(timezone.utc)

                    if due_dt and (due_dt - now).total_seconds() > 0:
                        remaining_seconds = (due_dt - now).total_seconds()
                        while remaining_seconds > 0 and not self.shutdown_event.is_set():
                            slice_wait = min(remaining_seconds, self.max_sleep_slice)
                            print(f"[SCHEDULER] No eligible group.")
                            print(f"[SCHEDULER] Next eligible: {next_due}")
                            print(f"[SCHEDULER] Waiting: {remaining_seconds:.1f}s")
                            print(f"SCHEDULER_STATE=RUNNING")
                            print(f"NEXT_ELIGIBLE_AT={next_due}")
                            print(f"WAIT_SECONDS={remaining_seconds:.1f}")
                            print(f"WAITING_FOR_WORK=true")
                            print(f"[SCHEDULER] Scheduler remains alive.")
                            sys.stdout.flush()

                            await asyncio.sleep(slice_wait)
                            if self.shutdown_event.is_set():
                                break

                            now = datetime.now(timezone.utc)
                            # Re-check database state after each slice for dynamic eligibility or schedule updates
                            try:
                                if self.db.get_next_eligible_group():
                                    break
                                summary = self.db.get_schedule_summary()
                                next_due = summary.get("next_group_due_at")
                                due_dt = parse_db_utc_time(next_due)
                                if not due_dt:
                                    break
                                remaining_seconds = (due_dt - now).total_seconds()
                            except Exception:
                                remaining_seconds = (due_dt - now).total_seconds()

                        if not self.shutdown_event.is_set():
                            print(f"[SCHEDULER] Wake-up triggered.")
                            print(f"[SCHEDULER] Rechecking eligible groups.")
                            sys.stdout.flush()
                    else:
                        slice_wait = min(self.max_sleep_slice, 5.0)
                        print(f"[SCHEDULER] No eligible group.")
                        print(f"[SCHEDULER] Next eligible: {next_due}")
                        print(f"[SCHEDULER] Waiting: {slice_wait:.1f}s")
                        print(f"SCHEDULER_STATE=RUNNING")
                        print(f"NEXT_ELIGIBLE_AT={next_due}")
                        print(f"WAIT_SECONDS={slice_wait:.1f}")
                        print(f"WAITING_FOR_WORK=true")
                        print(f"[SCHEDULER] Scheduler remains alive.")
                        sys.stdout.flush()

                        await asyncio.sleep(slice_wait)
                        if not self.shutdown_event.is_set():
                            print(f"[SCHEDULER] Wake-up triggered.")
                            print(f"[SCHEDULER] Rechecking eligible groups.")
                            sys.stdout.flush()

                    continue

                # Process eligible group
                chat_id = eligible_group["chat_id"]
                title = eligible_group.get("chat_title") or f"Group_{chat_id}"
                print(f"[SCHEDULER] Group selected: [{title}] (ID: {chat_id})")
                print(f"WORK_STARTED={chat_id}")
                sys.stdout.flush()

                try:
                    await self.scan_single_group(eligible_group)
                except FatalAuthError as fatal:
                    print(f"\n🛑 [FATAL AUTH STOP] {fatal}")
                    print("Halting collector worker loop immediately to protect session. Operator intervention required.")
                    sys.stdout.flush()
                    break
                except Exception as loop_err:
                    print(f"⚠️ [ISOLATED WORKER ERROR] Loop caught {type(loop_err).__name__}: {loop_err}")
                    try:
                        self.db.report_group_error(
                            chat_id=chat_id,
                            error=str(loop_err),
                            cooldown_seconds=1800,
                            status="error_isolated"
                        )
                    except Exception as db_err:
                        print(f"⚠️ Failed to report error to DB API: {db_err}")
                finally:
                    print(f"WORK_COMPLETED={chat_id}")
                    print(f"RETURNING_TO_SCHEDULER=true")
                    sys.stdout.flush()

                cycles_completed += 1

        except asyncio.CancelledError:
            print("🛑 [SCHEDULER] Collector worker received cancellation signal. Cleaning up.")
            sys.stdout.flush()
        finally:
            await self.disconnect()
            print("🛑 [SCHEDULER] Worker loop stopped cleanly.")
            sys.stdout.flush()

    async def run_passive_listener(self) -> None:
        """Isolated Passive Push Listener: 0 polling queries, listens to server-pushed updates."""
        self.db.wait_for_health(timeout=30)

        if not await self.connect():
            return

        self.sync_target_groups_to_db()
        registered = self.db.get_all_groups()

        chat_ids = [r["chat_id"] for r in registered]
        title_map = {r["chat_id"]: r["chat_title"] for r in registered}
        uname_map = {r["chat_id"]: r.get("chat_username") or "" for r in registered}

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

        print("👂 Passive listener running. Press Ctrl+C to stop.")
        try:
            await self.client.run_until_disconnected()
        except asyncio.CancelledError:
            print("🛑 Passive listener received cancel signal.")
        finally:
            await self.disconnect()


def parse_args():
    parser = argparse.ArgumentParser(description="Autonomous Telegram Buyer Collector & REST Scheduler")
    parser.add_argument("mode", choices=["worker", "listener", "status", "sync"], default="worker", nargs="?",
                        help="Operation mode: 'worker' (stateful scheduler), 'listener' (passive push), 'status' (DB summary), 'sync' (register groups)")
    parser.add_argument("--limit", type=int, default=15, help="Messages per group bounded batch (default: 15)")
    parser.add_argument("--cycles", type=int, default=None, help="Max group cycles to process (default: infinite)")
    parser.add_argument("--all-groups", action="store_true", help="Include all groups without marketplace keyword filter")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode (for architecture validation without live Telegram)")
    parser.add_argument("--mock-cooldown", type=float, default=None, help="Override group cooldown in seconds for mock mode")
    parser.add_argument("--max-slice", type=float, default=30.0, help="Maximum sleep slice in seconds (default: 30.0)")
    return parser.parse_args()


async def main():
    args = parse_args()
    collector = AutonomousBuyerCollector(
        messages_per_group=args.limit,
        filter_marketplace=not args.all_groups,
        mock_mode=args.mock,
        mock_cooldown=args.mock_cooldown,
        max_sleep_slice=args.max_slice
    )

    if args.mode == "status":
        collector.db.wait_for_health(timeout=10)
        health = collector.db.get_health()
        counts = collector.db.get_stats()
        summary = collector.db.get_schedule_summary()
        print("\n📊 TELEGRAM BUYER COLLECTOR SYSTEM STATUS")
        print("=" * 60)
        print(f"REST API Health: {health.get('status')} (WAL: {health.get('wal')})")
        print(f"Total Stored Messages: {counts.get('total_messages')}")
        print(f"Total Qualified Buyers: {counts.get('total_buyers')}")
        print(f"Registered Groups in Scheduler: {summary.get('total_registered_groups')}")
        print(f"Groups Eligible for Query Now: {summary.get('eligible_now')}")
        print(f"Groups in Cooldown: {summary.get('in_cooldown')}")
        print(f"Next Group Due At: {summary.get('next_group_due_at')}")
        print("=" * 60 + "\n")
        return

    if args.mode == "sync":
        collector.db.wait_for_health(timeout=10)
        n = collector.sync_target_groups_to_db()
        print(f"Synchronized {n} groups into REST API SQLite state.")
        return

    # Attach signal handlers for graceful termination
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: collector.handle_shutdown_signal(s))
        except (NotImplementedError, AttributeError):
            signal.signal(sig, lambda s, f: collector.handle_shutdown_signal(s))

    # For active worker or listener modes, enforce SingleWorkerLock
    with SingleWorkerLock() as _:
        if args.mode == "worker":
            await collector.run_worker_loop(max_cycles=args.cycles)
        elif args.mode == "listener":
            await collector.run_passive_listener()


if __name__ == "__main__":
    asyncio.run(main())
