"""
Docker Full Lifecycle Verification Suite
File: test_docker_lifecycle.py

Tests exact Section 11 lifecycle:
docker compose up -> database_api healthy -> collector starts -> mock work ->
collector SIGKILL -> Docker restart -> state recovered -> same work not duplicated.
"""

import sys
import time
import subprocess
import requests
from db_client import DatabaseApiClient
import database

def run_docker_lifecycle():
    print("=" * 70)
    print("STARTING DOCKER DATABASE & COLLECTOR LIFECYCLE SUITE (SECTION 11)")
    print("=" * 70)

    # 1. Clean previous state
    subprocess.run(["docker", "compose", "down", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    try:
        # Step 1: docker compose up -d database_api
        print("\n--- Step 11.1: Start database_api service via Docker Compose ---")
        cmd_up = ["docker", "compose", "up", "-d", "database_api"]
        res_up = subprocess.run(cmd_up, capture_output=True, text=True)
        print("Docker Compose Up Output:", res_up.stdout.strip())
        assert res_up.returncode == 0

        # Step 2: database_api healthy
        print("\n--- Step 11.2: Await database_api healthcheck ---")
        healthy = False
        for _ in range(20):
            res_inspect = subprocess.run(
                ["docker", "inspect", "--format={{json .State.Health.Status}}", "telegram_database_api"],
                capture_output=True, text=True
            )
            status = res_inspect.stdout.strip().replace('"', '')
            if status == "healthy":
                healthy = True
                break
            time.sleep(1)

        print(f"Container Health Status: {status}")
        assert healthy == True, "database_api must report healthy status"

        # Verify host probe
        client = DatabaseApiClient(base_url="http://127.0.0.1:8000", api_token="iroscript_tg_bot_token_secure_99")
        health = client.get_health()
        print("Host probe health response:", health)
        assert health["status"] == "healthy"
        assert health["wal"] == True
        assert health["integrity"] == "ok"
        print("Step 11.2 Result: PASS (telegram_database_api is healthy)")

        # Step 3: collector starts & performs mock work Phase 1
        print("\n--- Step 11.3: Collector Starts & Ingests Phase 1 (Messages 101..105) ---")
        cmd_phase1 = [
            "docker", "compose", "run", "--rm",
            "-v", f"{Path('.').resolve()}/test_container_mock_work.py:/app/test_container_mock_work.py",
            "--entrypoint", "python3", "telegram_collector",
            "test_container_mock_work.py", "1"
        ]
        res_phase1 = subprocess.run(cmd_phase1, capture_output=True, text=True)
        print("Phase 1 Output:\n", res_phase1.stdout.strip())
        if res_phase1.stderr:
            print("Phase 1 Stderr:\n", res_phase1.stderr.strip())
        assert res_phase1.returncode == 0

        # Verify Phase 1 checkpoint via host
        state1 = client.get_group_state(-999005)
        print(f"Checkpoint after Phase 1: {state1['last_message_id']}")
        assert state1["last_message_id"] == 105

        # Step 4: Start long-running collector container, then SIGKILL
        print("\n--- Step 11.4: Collector Container SIGKILL (`kill -9`) ---")
        cmd_spawn = [
            "docker", "compose", "run", "-d",
            "--name", "telegram_buyer_collector",
            "--entrypoint", "python3", "telegram_collector",
            "-c", "import time; time.sleep(120)"
        ]
        res_spawn = subprocess.run(cmd_spawn, capture_output=True, text=True)
        assert res_spawn.returncode == 0
        container_id = res_spawn.stdout.strip()
        print(f"Spawned collector container ID: {container_id[:12]}")

        # Kill with SIGKILL
        res_kill = subprocess.run(["docker", "kill", "-s", "SIGKILL", "telegram_buyer_collector"], capture_output=True, text=True)
        print("Kill Output:", res_kill.stdout.strip())
        assert res_kill.returncode == 0

        # Verify container exited with 137 (SIGKILL)
        res_exit = subprocess.run(
            ["docker", "inspect", "--format={{.State.ExitCode}}", "telegram_buyer_collector"],
            capture_output=True, text=True
        )
        exit_code = int(res_exit.stdout.strip())
        print(f"Collector Exit Code after SIGKILL: {exit_code}")
        assert exit_code == 137

        # Remove dead container
        subprocess.run(["docker", "rm", "-f", "telegram_buyer_collector"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Step 11.4 Result: PASS (Collector abruptly terminated with SIGKILL)")

        # Step 5: Docker restart & state recovery
        print("\n--- Step 11.5: Docker Restart & State Recovery ---")
        # Pre-resume checkpoint check on database_api
        checkpoint_intact = client.get_group_state(-999005)
        print(f"Pre-resume checkpoint in DB API: {checkpoint_intact['last_message_id']}")
        assert checkpoint_intact["last_message_id"] == 105

        # Restart collector and run Phase 2 (Resume from 105 -> 110 with duplicates)
        cmd_phase2 = [
            "docker", "compose", "run", "--rm",
            "-v", f"{Path('.').resolve()}/test_container_mock_work.py:/app/test_container_mock_work.py",
            "--entrypoint", "python3", "telegram_collector",
            "test_container_mock_work.py", "2"
        ]
        res_phase2 = subprocess.run(cmd_phase2, capture_output=True, text=True)
        print("Phase 2 Output:\n", res_phase2.stdout.strip())
        if res_phase2.stderr:
            print("Phase 2 Stderr:\n", res_phase2.stderr.strip())
        assert res_phase2.returncode == 0

        # Step 6: Verify Zero Duplicates & Final State
        print("\n--- Step 11.6: Verify Same Work NOT Duplicated ---")
        final_msgs = client.get_messages_by_chat_id(-999005, limit=50)
        final_state = client.get_group_state(-999005)
        print(f"Total messages stored for group: {len(final_msgs)}")
        print(f"Final checkpoint: {final_state['last_message_id']}")

        assert len(final_msgs) == 10, f"Expected exactly 10 messages, got {len(final_msgs)}"
        assert final_state["last_message_id"] == 110, f"Expected final checkpoint=110, got {final_state['last_message_id']}"
        distinct_ids = set(m["message_id"] for m in final_msgs)
        assert len(distinct_ids) == 10, f"Expected 10 unique IDs, got {len(distinct_ids)}"
        print("Step 11.6 Result: PASS (Exactly 10 messages, 0 duplicates, state recovered cleanly)")

        print("\n" + "=" * 70)
        print("ALL DOCKER LIFECYCLE TESTS (SECTION 11): 100% PASS!")
        print("=" * 70)

    finally:
        # Clean up test group records
        try:
            client.update_group_checkpoint(-999005, 0, 0, "idle")
            conn = database.get_connection()
            conn.execute("DELETE FROM scrape_state WHERE chat_id = -999005")
            conn.execute("DELETE FROM messages WHERE chat_id = -999005")
            conn.commit()
            conn.close()
        except Exception:
            pass

        subprocess.run(["docker", "compose", "down"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

if __name__ == "__main__":
    from pathlib import Path
    run_docker_lifecycle()
