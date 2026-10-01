"""
REST API Endpoints Comprehensive Verification Suite
File: test_rest_api_endpoints.py
"""

import sys
import time
import subprocess
from pathlib import Path
from db_client import DatabaseApiClient
import requests

def run_suite():
    print("=" * 70)
    print("STARTING REST API ENDPOINT COMPREHENSIVE VERIFICATION SUITE")
    print("=" * 70)

    # Start db_server on a dedicated test port with auth token enabled
    test_port = 8011
    test_token = "test_secret_token_123"
    env = {
        "API_PORT": str(test_port),
        "API_HOST": "127.0.0.1",
        "DATA_DIR": "data",
        "INTERNAL_API_TOKEN": test_token,
        "PATH": "/usr/bin:/bin"
    }

    server_proc = subprocess.Popen([sys.executable, "db_server.py"], env=env)
    client = DatabaseApiClient(base_url=f"http://127.0.0.1:{test_port}", api_token=test_token)

    try:
        # Wait for server to become healthy
        print("\n--- Testing GET /health ---")
        client.wait_for_health(timeout=10)
        health = client.get_health()
        print("Health Output:", health)
        assert health["status"] == "healthy"
        assert health["wal"] == True
        assert health["integrity"] == "ok"
        print("GET /health: PASS")

        # Test Auth: Request with invalid token must return 401
        print("\n--- Testing Security Token Authentication ---")
        unauth_resp = requests.get(f"http://127.0.0.1:{test_port}/stats", headers={"X-API-Key": "wrong_token"})
        print("Unauthorized Response Code:", unauth_resp.status_code)
        assert unauth_resp.status_code == 401
        print("Token Authentication Gate: PASS (401 Unauthorized enforced)")

        # Test GET /stats
        print("\n--- Testing GET /stats ---")
        stats = client.get_stats()
        print("Stats Output:", stats)
        assert "total_messages" in stats
        assert "total_buyers" in stats
        assert "tracked_chats" in stats
        print("GET /stats: PASS")

        # Test POST /groups/register & GET /groups
        print("\n--- Testing POST /groups/register & GET /groups ---")
        test_group = {"id": -777001, "title": "API Test Group", "username": "api_test_group"}
        reg_count = client.register_groups([test_group])
        assert reg_count >= 1
        all_groups = client.get_all_groups()
        found = any(g["chat_id"] == -777001 for g in all_groups)
        assert found == True
        print(f"POST /groups/register & GET /groups: PASS (Registered and retrieved group -777001)")

        # Test GET /groups/{chat_id}/state
        print("\n--- Testing GET /groups/{chat_id}/state ---")
        state = client.get_group_state(-777001)
        print("Group State:", state)
        assert state is not None
        assert state["chat_id"] == -777001
        assert state["chat_title"] == "API Test Group"
        print("GET /groups/{chat_id}/state: PASS")

        # Test POST /groups/{chat_id}/checkpoint
        print("\n--- Testing POST /groups/{chat_id}/checkpoint ---")
        updated = client.update_group_checkpoint(
            chat_id=-777001,
            last_message_id=5001,
            cooldown_seconds=3600.0,
            status="idle"
        )
        assert updated == True
        state_after = client.get_group_state(-777001)
        assert state_after["last_message_id"] == 5001
        assert state_after["status"] == "idle"
        print("POST /groups/{chat_id}/checkpoint: PASS (Updated last_message_id to 5001)")

        # Test POST /groups/{chat_id}/error
        print("\n--- Testing POST /groups/{chat_id}/error ---")
        err_res = client.report_group_error(
            chat_id=-777001,
            error="Simulated network glitch",
            cooldown_seconds=120.0,
            status="network_fail"
        )
        assert err_res == True
        state_err = client.get_group_state(-777001)
        assert state_err["status"] == "network_fail"
        assert state_err["error_count"] >= 1
        print("POST /groups/{chat_id}/error: PASS (Recorded error and status=network_fail)")

        # Test POST /groups/{chat_id}/schedule
        print("\n--- Testing POST /groups/{chat_id}/schedule ---")
        sched_res = client.schedule_group(
            chat_id=-777001,
            cooldown_seconds=60.0,
            status="idle"
        )
        assert sched_res == True
        print("POST /groups/{chat_id}/schedule: PASS")

        # Test POST /messages & Duplicate Immunity
        print("\n--- Testing POST /messages & Deduplication ---")
        msg_id = int(time.time() * 1000) % 100000000
        msg_payload = {
            "message_id": msg_id,
            "chat_id": -777001,
            "chat_title": "API Test Group",
            "sender_id": 998877,
            "sender_name": "Test Buyer",
            "sender_username": "@testbuyer",
            "date": "2026-10-01 14:00:00",
            "raw_text": "I want to buy 1000 Toncoins urgently",
            "message_link": f"https://t.me/api_test_group/{msg_id}"
        }
        ins1 = client.insert_raw_message(msg_payload)
        assert ins1 == True, "First message insert must succeed"
        ins2 = client.insert_raw_message(msg_payload)
        assert ins2 == False, "Second identical message insert must be rejected as duplicate"
        print("POST /messages: PASS (Unique insert succeeded, duplicate rejected)")

        # Test GET /messages/{chat_id}
        print("\n--- Testing GET /messages/{chat_id} ---")
        msgs = client.get_messages_by_chat_id(-777001, limit=5)
        assert len(msgs) >= 1
        assert msgs[0]["message_id"] == msg_id
        print(f"GET /messages/{{chat_id}}: PASS (Retrieved {len(msgs)} messages)")

        # Test POST /buyers & GET /buyers
        print("\n--- Testing POST /buyers & GET /buyers ---")
        buyer_payload = {
            "message_id": msg_id,
            "chat_id": -777001,
            "chat_title": "API Test Group",
            "sender_id": 998877,
            "sender_name": "Test Buyer",
            "sender_username": "@testbuyer",
            "date": "2026-10-01 14:00:00",
            "need": "Toncoin bulk purchase",
            "budget": "$5000",
            "quantity": "1000",
            "urgency": "HIGH",
            "confidence": 0.95,
            "evidence": "want to buy 1000 Toncoins",
            "raw_text": "I want to buy 1000 Toncoins urgently",
            "message_link": f"https://t.me/api_test_group/{msg_id}"
        }
        buyer_ins = client.insert_buyer(buyer_payload)
        assert buyer_ins == True
        buyers = client.get_recent_buyers(limit=5)
        assert len(buyers) >= 1
        assert buyers[0]["message_id"] == msg_id
        print("POST /buyers & GET /buyers: PASS")

        # Test POST /flood-wait & GET /scheduler/status
        print("\n--- Testing POST /flood-wait & GET /scheduler/status ---")
        fw_res = client.set_global_flood_wait(seconds=30, reason="Test Telegram FloodWait")
        assert fw_res == True
        summary = client.get_schedule_summary()
        print("Scheduler Summary:", summary)
        assert summary["total_registered_groups"] >= 1
        print("POST /flood-wait & GET /scheduler/status: PASS")

        # Test GET /groups/next
        print("\n--- Testing GET /groups/next ---")
        # Put test group in past
        import database
        conn = database.get_connection()
        conn.execute("UPDATE scrape_state SET priority = 1000, next_eligible_at = datetime('now', '-10 seconds') WHERE chat_id = -777001")
        conn.commit()
        conn.close()
        next_grp = client.get_next_eligible_group()
        assert next_grp is not None
        assert next_grp["chat_id"] == -777001
        print(f"GET /groups/next: PASS (Selected top priority eligible group: {next_grp['chat_title']})")

        # Cleanup test fixture
        conn = database.get_connection()
        conn.execute("DELETE FROM scrape_state WHERE chat_id = -777001")
        conn.execute("DELETE FROM messages WHERE chat_id = -777001")
        conn.execute("DELETE FROM buyers WHERE chat_id = -777001")
        conn.commit()
        conn.close()

        print("\n" + "=" * 70)
        print("ALL REST API ENDPOINT TESTS: 100% PASS!")
        print("=" * 70)

    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    run_suite()
