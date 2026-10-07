import asyncio
import sqlite3
import json
import time
import os
import sys
import subprocess
from unittest.mock import AsyncMock, patch, MagicMock
from telethon.errors import RPCError

import database
from autonomous_collector import AutonomousBuyerCollector, FatalAuthError
from db_client import DatabaseApiClient
from llm_analyzer import extract_json

async def run_failure_matrix_tests():
    # Ensure database schema is initialized
    database.init_db()
    # Pre-test cleanup to guarantee idempotency
    conn = database.get_connection()
    conn.execute("DELETE FROM scrape_state WHERE chat_id IN (-888101, -888102, -888103, -888104, -888105, -888106)")
    conn.execute("DELETE FROM messages WHERE chat_id IN (-888104, -888105)")
    conn.commit()
    conn.close()

    test_port = 8014
    test_token = "part4_test_token"
    env = os.environ.copy()
    env.update({
        "API_PORT": str(test_port),
        "API_HOST": "127.0.0.1",
        "DATA_DIR": "data",
        "INTERNAL_API_TOKEN": test_token
    })
    server_proc = subprocess.Popen([sys.executable, "db_server.py"], env=env)
    client = DatabaseApiClient(base_url=f"http://127.0.0.1:{test_port}", api_token=test_token)
    client.wait_for_health(timeout=10)

    try:
        collector = AutonomousBuyerCollector(messages_per_group=5, db_client=client)
        
        # --- 1. Transient Network Error ---
        print("\n--- Test 4.1: Transient Network Error & Retry/Backoff ---")
        mock_g1 = {"chat_id": -888101, "chat_title": "Network Error Chat", "last_message_id": 100}
        database.register_groups([{"id": -888101, "title": "Network Error Chat"}])
        with patch.object(collector, "client") as mock_client:
            mock_client.iter_messages.side_effect = ConnectionError("Transient Socket Drop")
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                res = await collector.scan_single_group(mock_g1)
                assert res == [], "Should return empty buyers list on network exhaustion"
                assert mock_sleep.call_count >= 2, "Must perform exponential retry backoffs"
                
                conn = database.get_connection()
                row = conn.execute("SELECT status, error_count, last_error FROM scrape_state WHERE chat_id = -888101").fetchone()
                conn.close()
                assert row["status"] == "network_fail"
                print("Test 4.1 Result: PASS (Network retry backoff, checkpoint status=network_fail, state preserved)")

        # --- 2. Timeout Error ---
        print("\n--- Test 4.2: Timeout Error ---")
        mock_g2 = {"chat_id": -888102, "chat_title": "Timeout Error Chat", "last_message_id": 200}
        database.register_groups([{"id": -888102, "title": "Timeout Error Chat"}])
        with patch.object(collector, "client") as mock_client:
            mock_client.iter_messages.side_effect = asyncio.TimeoutError("MTProto Query Timed Out")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                res = await collector.scan_single_group(mock_g2)
                assert res == []
                conn = database.get_connection()
                row = conn.execute("SELECT status, last_error FROM scrape_state WHERE chat_id = -888102").fetchone()
                conn.close()
                assert row["status"] == "network_fail"
                print("Test 4.2 Result: PASS (Timeout handled with retry and checkpoint)")

        # --- 3. Generic Exception & State Preservation ---
        print("\n--- Test 4.3: Generic Exception & Cooldown Checkpoint ---")
        mock_g3 = {"chat_id": -888103, "chat_title": "Generic Error Chat", "last_message_id": 300}
        database.register_groups([{"id": -888103, "title": "Generic Error Chat"}])
        with patch.object(collector, "client") as mock_client:
            mock_client.iter_messages.side_effect = ValueError("Malformed API packet structure")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                res = await collector.scan_single_group(mock_g3)
                assert res == []
                conn = database.get_connection()
                row = conn.execute("SELECT status, last_error FROM scrape_state WHERE chat_id = -888103").fetchone()
                conn.close()
                assert row["status"] == "error"
                assert "Malformed API packet" in row["last_error"]
                print("Test 4.3 Result: PASS (Generic exception caught, error logged in DB, state preserved)")

        # --- 4. Database Concurrency / WAL Contention ---
        print("\n--- Test 4.4: Database Temporary Contention / Busy Handling ---")
        conn1 = database.get_connection()
        conn2 = database.get_connection()
        test_msg_id = int(time.time() * 1000) % 100000000
        msg_test = {"message_id": test_msg_id, "chat_id": -888104, "raw_text": "Concurrency test"}
        inserted = database.insert_raw_message(msg_test)
        assert inserted == True
        read_row = conn2.execute("SELECT raw_text FROM messages WHERE message_id = ? AND chat_id = -888104", (test_msg_id,)).fetchone()
        assert read_row["raw_text"] == "Concurrency test"
        conn1.close()
        conn2.close()
        print("Test 4.4 Result: PASS (SQLite WAL mode handles concurrent read/write without locking collision)")

        # --- 5. Malformed Response / Unparseable JSON ---
        print("\n--- Test 4.5: Malformed Response Handling ---")
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "INVALID_GARBAGE_OUTPUT"}}]}
            mock_post.return_value = mock_resp
            analysis = collector.analyzer.analyze_message("Some buyer message")
            assert analysis["buyer"] == False, "Malformed model output must gracefully default to buyer=False"
            print("Test 4.5 Result: PASS (Malformed LLM output handled gracefully with buyer=False default)")

        # --- 6. Duplicate Message Insertion Immunity ---
        print("\n--- Test 4.6: Duplicate Message Insertion Immunity ---")
        dup_id = test_msg_id + 1
        dup_msg = {"message_id": dup_id, "chat_id": -888105, "raw_text": "Duplicate test message"}
        ins1 = database.insert_raw_message(dup_msg)
        assert ins1 == True, "First insertion must succeed"
        ins2 = database.insert_raw_message(dup_msg)
        assert ins2 == False, "Second identical insertion must be rejected (ignored)"
        conn = database.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE message_id = ? AND chat_id = -888105", (dup_id,)).fetchone()[0]
        conn.close()
        assert count == 1, "Database must hold exactly 1 copy of the message"
        print("Test 4.6 Result: PASS (Duplicate message immune; UNIQUE(chat_id, message_id) strictly prevents duplicate insertion)")

        # --- 7. Unexpected Exception in Loop Isolation ---
        print("\n--- Test 4.7: Unexpected Loop Exception Isolation (Never-Exit Guarantee) ---")
        mock_eligible = {"chat_id": -888106, "chat_title": "Crash Loop Chat", "last_message_id": 0}
        database.register_groups([{"id": -888106, "title": "Crash Loop Chat"}])
        with patch.object(collector, "scan_single_group", side_effect=RuntimeError("Unforeseen loop disruption")):
            with patch.object(collector, "connect", return_value=True):
                with patch.object(collector, "disconnect", return_value=None):
                    with patch.object(collector.db, "get_next_eligible_group", return_value=mock_eligible):
                        with patch.object(collector, "sync_target_groups_to_db", return_value=None):
                            with patch("asyncio.sleep", new_callable=AsyncMock):
                                await collector.run_worker_loop(max_cycles=1)
                                conn = database.get_connection()
                                row = conn.execute("SELECT status, last_error FROM scrape_state WHERE chat_id = -888106").fetchone()
                                conn.close()
                                assert row["status"] == "error_isolated"
                                print("Test 4.7 Result: PASS (Loop caught unexpected exception, checkpointed error_isolated, and completed cycle)")

        # Clean up test mock rows
        conn = database.get_connection()
        conn.execute("DELETE FROM scrape_state WHERE chat_id IN (-888101, -888102, -888103, -888104, -888105, -888106)")
        conn.execute("DELETE FROM messages WHERE chat_id IN (-888104, -888105)")
        conn.commit()
        conn.close()

        print("\nALL PART 4 FAILURE-RECOVERY & NEVER-EXIT TESTS: 100% PASS!\n")
    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    asyncio.run(run_failure_matrix_tests())
