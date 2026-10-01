"""
Container Mock Ingestion & Checkpoint Worker
File: test_container_mock_work.py
"""

import os
import sys
import time
from db_client import DatabaseApiClient

def run_mock_work(phase: int = 1):
    api_url = os.getenv("DATABASE_API_URL", "http://telegram_database_api:8000")
    token = os.getenv("INTERNAL_API_TOKEN", "iroscript_tg_bot_token_secure_99")
    client = DatabaseApiClient(base_url=api_url, api_token=token)

    # 1. Health check
    client.wait_for_health(timeout=20)
    health = client.get_health()
    print(f"Container connected to DB API: {health['status']} (WAL: {health['wal']})")

    chat_id = -999005

    if phase == 1:
        print("\n--- Running Container Phase 1: Ingest Batch 1 (Messages 101..105) ---")
        client.register_groups([{"id": chat_id, "title": "Docker Lifecycle Group"}])
        client.update_group_checkpoint(chat_id, last_message_id=100, cooldown_seconds=0, status="idle")

        for mid in range(101, 106):
            msg = {
                "message_id": mid,
                "chat_id": chat_id,
                "chat_title": "Docker Lifecycle Group",
                "sender_id": 554433,
                "sender_name": "Docker Test User",
                "date": "2026-10-01 14:15:00",
                "raw_text": f"Docker test message {mid}"
            }
            inserted = client.insert_raw_message(msg)
            assert inserted == True, f"Message {mid} should be inserted"

        client.update_group_checkpoint(chat_id, last_message_id=105, cooldown_seconds=0, status="idle")
        state = client.get_group_state(chat_id)
        print(f"Phase 1 Complete. Checkpoint: {state['last_message_id']}, status: {state['status']}")
        assert state["last_message_id"] == 105

    elif phase == 2:
        print("\n--- Running Container Phase 2: Resume Batch 2 (Messages 101..110 with duplicates) ---")
        pre_state = client.get_group_state(chat_id)
        print(f"Resumed checkpoint from DB: {pre_state['last_message_id']}")
        assert pre_state["last_message_id"] == 105

        # Attempt to insert overlapping messages 101..105 (must be rejected) + new 106..110
        inserted_count = 0
        duplicate_count = 0
        for mid in range(101, 111):
            msg = {
                "message_id": mid,
                "chat_id": chat_id,
                "chat_title": "Docker Lifecycle Group",
                "sender_id": 554433,
                "sender_name": "Docker Test User",
                "date": "2026-10-01 14:20:00",
                "raw_text": f"Docker test message {mid}"
            }
            if client.insert_raw_message(msg):
                inserted_count += 1
            else:
                duplicate_count += 1

        print(f"Ingestion results: new_inserted={inserted_count}, duplicates_rejected={duplicate_count}")
        assert duplicate_count == 5, f"Expected 5 duplicates rejected, got {duplicate_count}"
        assert inserted_count == 5, f"Expected 5 new messages inserted, got {inserted_count}"

        client.update_group_checkpoint(chat_id, last_message_id=110, cooldown_seconds=3600, status="idle")
        final_state = client.get_group_state(chat_id)
        assert final_state["last_message_id"] == 110
        print(f"Phase 2 Complete. Final Checkpoint: {final_state['last_message_id']}")

if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_mock_work(phase=p)
