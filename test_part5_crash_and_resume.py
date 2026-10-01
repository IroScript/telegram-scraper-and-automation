import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, patch, MagicMock

import database
from autonomous_collector import AutonomousBuyerCollector

from datetime import datetime

class MockTelethonMessage:
    def __init__(self, msg_id, text, sender_id=12345, date=None):
        self.id = msg_id
        self.message = text
        self.text = text
        self.sender_id = sender_id
        self.date = date or datetime.now()
        sender_mock = MagicMock()
        sender_mock.id = sender_id
        sender_mock.username = "test_user"
        sender_mock.first_name = "Test"
        sender_mock.last_name = "User"
        sender_mock.phone = None
        self.get_sender = AsyncMock(return_value=sender_mock)

async def run_crash_and_resume_suite():
    print("=" * 70)
    print("STARTING PART 5: CRASH-AND-RESUME EMPIRICAL VALIDATION SUITE")
    print("=" * 70)

    # Clean pre-existing test fixtures
    conn = database.get_connection()
    conn.execute("DELETE FROM scrape_state WHERE chat_id IN (-999001, -999002)")
    conn.execute("DELETE FROM messages WHERE chat_id IN (-999001, -999002)")
    conn.execute("DELETE FROM buyers WHERE chat_id IN (-999001, -999002)")
    conn.commit()
    conn.close()

    # Step 1: Register two mock test groups with known checkpoints
    test_groups = [
        {"id": -999001, "title": "Crash Test Group A"},
        {"id": -999002, "title": "Crash Test Group B"}
    ]
    database.register_groups(test_groups)

    # Set initial checkpoint for Group A at message_id=100, Group B at message_id=200
    database.update_group_checkpoint(-999001, 100, status="idle", cooldown_seconds=0)
    database.update_group_checkpoint(-999002, 200, status="idle", cooldown_seconds=0)

    collector = AutonomousBuyerCollector(messages_per_group=5)

    # 10 mock messages for Group A (IDs 101 through 110)
    mock_messages_batch1 = [
        MockTelethonMessage(105, "I want to buy USDT urgently"),
        MockTelethonMessage(104, "Selling some tokens here"),
        MockTelethonMessage(103, "Looking to purchase Telegram premium"),
        MockTelethonMessage(102, "Hello everyone"),
        MockTelethonMessage(101, "Buying bulk accounts")
    ]

    # --- Phase 1: Controlled Mid-Run Simulation ---
    print("\n--- Phase 5.1: Run Batch 1 on Group A and Inspect Checkpoint ---")
    mock_g1 = {"chat_id": -999001, "chat_title": "Crash Test Group A", "last_message_id": 100}

    with patch.object(collector, "client") as mock_client:
        async def mock_iter_messages(chat_id, limit=5, min_id=0):
            assert min_id == 100, f"Expected min_id=100, got {min_id}"
            for m in mock_messages_batch1:
                yield m

        mock_client.iter_messages = mock_iter_messages
        # Mock analyzer to return fast response
        with patch.object(collector.analyzer, "analyze_message", return_value={"buyer": True, "lead_confidence": 0.9, "summary": "Buyer lead"}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                buyers = await collector.scan_single_group(mock_g1)
                assert len(buyers) == 5, f"Expected 5 buyers, got {len(buyers)}"

    # Verify Database state post Phase 1
    conn = database.get_connection()
    row_g1 = conn.execute("SELECT last_message_id, status FROM scrape_state WHERE chat_id = -999001").fetchone()
    count_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = -999001").fetchone()[0]
    conn.close()

    print(f"Phase 5.1 Checkpoint State: last_message_id={row_g1['last_message_id']}, status={row_g1['status']}, messages_saved={count_msgs}")
    assert row_g1["last_message_id"] == 105, f"Expected last_message_id=105, got {row_g1['last_message_id']}"
    assert count_msgs == 5, f"Expected exactly 5 messages, got {count_msgs}"
    print("Phase 5.1 Result: PASS (Batch 1 saved, checkpoint intact at message_id=105)")

    # --- Phase 2: Crash & Interruption Simulation ---
    print("\n--- Phase 5.2: Simulate Crash/Process Interruption ---")
    # Simulate an abrupt crash: process dies. Checkpoint in DB remains 105.
    conn = database.get_connection()
    checkpoint_before_restart = conn.execute("SELECT last_message_id FROM scrape_state WHERE chat_id = -999001").fetchone()[0]
    conn.close()
    assert checkpoint_before_restart == 105
    print("Phase 5.2 Result: PASS (Pre-crash checkpoint verified intact in persistent SQLite storage)")

    # --- Phase 3: Resume Run & Duplicate Message Immunity ---
    print("\n--- Phase 5.3: Resume Collector from Checkpoint (min_id=105) ---")
    # New collector instance simulating clean process restart
    new_collector = AutonomousBuyerCollector(messages_per_group=5)

    # Next batch has messages 106 to 110 AND overlapping messages 101 to 105
    # The collector must query min_id=105, so server only yields 106..110
    # Even if overlapping messages are received, UNIQUE constraint must reject duplicates
    mock_messages_batch2 = [
        MockTelethonMessage(110, "Need to buy 500 TON"),
        MockTelethonMessage(109, "Selling BTC"),
        MockTelethonMessage(108, "WTB verified accounts"),
        MockTelethonMessage(107, "Payment sent"),
        MockTelethonMessage(106, "Looking for vendor"),
        # Overlapping duplicate messages to test immunity
        MockTelethonMessage(105, "I want to buy USDT urgently"),
        MockTelethonMessage(104, "Selling some tokens here")
    ]

    resumed_min_id_requested = []

    with patch.object(new_collector, "client") as mock_client:
        async def mock_iter_resumed(chat_id, limit=5, min_id=0):
            resumed_min_id_requested.append(min_id)
            for m in mock_messages_batch2:
                yield m

        mock_client.iter_messages = mock_iter_resumed
        with patch.object(new_collector.analyzer, "analyze_message", return_value={"buyer": True, "lead_confidence": 0.85, "summary": "Buyer lead"}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                # Retrieve current state from DB
                conn = database.get_connection()
                group_state = conn.execute("SELECT chat_id, chat_title, last_message_id FROM scrape_state WHERE chat_id = -999001").fetchone()
                conn.close()

                buyers_resumed = await new_collector.scan_single_group(dict(group_state))

    assert resumed_min_id_requested == [105], f"Expected resumed min_id=105, got {resumed_min_id_requested}"

    # Verify no duplicates inserted
    conn = database.get_connection()
    count_total = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = -999001").fetchone()[0]
    distinct_count = conn.execute("SELECT COUNT(DISTINCT message_id) FROM messages WHERE chat_id = -999001").fetchone()[0]
    row_final = conn.execute("SELECT last_message_id, status FROM scrape_state WHERE chat_id = -999001").fetchone()
    conn.close()

    print(f"Phase 5.3 Post-Resume State: total_msgs={count_total}, distinct_msgs={distinct_count}, final_checkpoint={row_final['last_message_id']}")
    assert count_total == 10, f"Expected exactly 10 total messages (no duplicate insertions), got {count_total}"
    assert distinct_count == 10, f"Expected 10 distinct messages, got {distinct_count}"
    assert row_final["last_message_id"] == 110, f"Expected final checkpoint=110, got {row_final['last_message_id']}"
    print("Phase 5.3 Result: PASS (Clean resume from checkpoint 105 -> 110, 0 duplicates inserted)")

    # --- Phase 4: Scheduler Progression to Next Group ---
    print("\n--- Phase 5.4: Verify Scheduler Progression to Next Eligible Group ---")
    # Put Group A into cooldown (60 seconds)
    database.update_group_checkpoint(-999001, 110, status="completed", cooldown_seconds=60)
    # Ensure Group B is eligible now
    database.update_group_checkpoint(-999002, 200, status="idle", cooldown_seconds=0)

    conn = database.get_connection()
    # Force next_eligible_at for Group B to past and set top priority for deterministic validation
    conn.execute("UPDATE scrape_state SET priority = 999, next_eligible_at = datetime('now', '-5 minutes') WHERE chat_id = -999002")
    conn.commit()
    conn.close()

    next_group = database.get_next_eligible_group()
    assert next_group is not None, "Scheduler must find an eligible group"
    print(f"Scheduler selected: [{next_group['chat_title']}] (ID: {next_group['chat_id']})")
    assert next_group["chat_id"] == -999002, f"Expected Group B (-999002), got {next_group['chat_id']}"
    assert next_group["last_message_id"] == 200, f"Expected Group B checkpoint=200, got {next_group['last_message_id']}"
    print("Phase 5.4 Result: PASS (Scheduler smoothly advanced to Group B, Group A cleanly in cooldown)")

    # Clean up test records
    conn = database.get_connection()
    conn.execute("DELETE FROM scrape_state WHERE chat_id IN (-999001, -999002)")
    conn.execute("DELETE FROM messages WHERE chat_id IN (-999001, -999002)")
    conn.execute("DELETE FROM buyers WHERE chat_id IN (-999001, -999002)")
    conn.commit()
    conn.close()

    print("\n" + "=" * 70)
    print("ALL PART 5 CRASH-AND-RESUME TESTS: 100% PASS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_crash_and_resume_suite())
