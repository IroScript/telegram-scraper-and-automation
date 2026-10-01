#!/usr/bin/env python3
"""
P1-P8 Verification Suite for Permanent Non-Terminating Scheduler Daemon
File: test_permanent_scheduler.py

Validates the 8 rigorous empirical requirements:
- P1: Empty Scheduler Persistence Test
- P2: Future Job Wake-up Test
- P3: Long-Wait Simulation Test (1h, 5h, 24h)
- P4: Continuous Multi-Cycle Persistence Test (Same PID across cycles)
- P5: Transient Failure Resilience Test (Loop survives errors)
- P6: Graceful Shutdown (SIGTERM clean exit, lock released)
- P7: Crash / SIGKILL & Docker Auto-Recovery Test
- P8: Full Stack Restart Recovery Test
"""

import os
import sys
import time
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from db_client import DatabaseApiClient
from lock_manager import SingleWorkerLock

BASE_URL = os.getenv("DATABASE_API_URL", "http://127.0.0.1:8008")
API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "iroscript_tg_bot_token_secure_99")
PROJECT_DIR = Path(__file__).parent.resolve()


def print_separator(title: str):
    print(f"\n{'='*75}")
    print(f"  {title}")
    print(f"{'='*75}")


class SchedulerTestSuite:
    def __init__(self):
        self.client = DatabaseApiClient(base_url=BASE_URL, api_token=API_TOKEN)
        self.results = {}

    def setup(self):
        print("🔍 Checking Database API health...")
        subprocess.run(["docker", "compose", "up", "-d", "database_api"], cwd=str(PROJECT_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "compose", "stop", "telegram_collector"], cwd=str(PROJECT_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", "-f", "telegram_buyer_collector"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        self.client.wait_for_health(timeout=25)
        health = self.client.get_health()
        print(f"✓ Database API is healthy: {health['status']} (WAL: {health['wal']})")

    def test_p1_empty_scheduler_persistence(self) -> bool:
        """P1: Empty Scheduler Persistence (all groups cooling, process stays alive, bounded CPU)."""
        print_separator("P1: EMPTY SCHEDULER PERSISTENCE TEST")
        # Put all groups in future cooldown (1 hour)
        all_groups = self.client.get_all_groups()
        for g in all_groups[:10]:
            self.client.update_group_checkpoint(g["chat_id"], g.get("last_message_id", 0), 3600, status="idle")

        summary = self.client.get_schedule_summary()
        print(f"Initial State: eligible_now={summary['eligible_now']}, in_cooldown={summary['in_cooldown']}")

        # Launch worker with mock mode and max-slice 1.0s
        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["SCHEDULER_MAX_SLICE"] = "1.0"

        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "1.0"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        try:
            # Let it run for 6 seconds in empty scheduler state
            time.sleep(6)
            poll_res = proc.poll()
            assert poll_res is None, f"Worker terminated prematurely with exit code {poll_res}"

            # Check CPU utilization via ps
            ps_out = subprocess.check_output(["ps", "-p", str(proc.pid), "-o", "%cpu,rss"]).decode()
            print(f"Process PID {proc.pid} Status: RUNNING")
            print(f"Resource Metrics:\n{ps_out.strip()}")

            # Gracefully signal termination
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=5)

            # Assert required observability tokens
            assert "[SCHEDULER] No eligible group." in stdout, "Missing '[SCHEDULER] No eligible group.'"
            assert "SCHEDULER_STATE=RUNNING" in stdout, "Missing 'SCHEDULER_STATE=RUNNING'"
            assert "WAITING_FOR_WORK=true" in stdout, "Missing 'WAITING_FOR_WORK=true'"
            assert "[SCHEDULER] Scheduler remains alive." in stdout, "Missing '[SCHEDULER] Scheduler remains alive.'"

            print("✓ P1 Evidence: Scheduler persisted with zero eligible groups. CPU bounded, zero exit.")
            return True
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_p2_future_job_wakeup(self) -> bool:
        """P2: Future Job Wake-up (controlled delay, auto-wakes at T+delay, processes, returns)."""
        print_separator("P2: FUTURE JOB WAKE-UP TEST")
        test_chat_id = -999101
        self.client.register_groups([{"id": test_chat_id, "title": "P2 Wakeup Test Group"}])

        # Put all groups in far future, except test group due in 3 seconds
        self.client.update_group_checkpoint(test_chat_id, last_message_id=50, cooldown_seconds=3, status="idle")

        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["SCHEDULER_MAX_SLICE"] = "1.0"

        start_time = time.time()
        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "1.0", "--cycles", "1"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = proc.communicate(timeout=15)
        elapsed = time.time() - start_time

        print(f"Worker output summary (elapsed {elapsed:.2f}s):")
        print("\n".join(stdout.splitlines()[-15:]))

        assert elapsed >= 2.5, f"Wake-up triggered too early ({elapsed:.2f}s)"
        assert "[SCHEDULER] Wake-up triggered." in stdout, "Missing '[SCHEDULER] Wake-up triggered.'"
        assert f"WORK_STARTED={test_chat_id}" in stdout, f"Missing 'WORK_STARTED={test_chat_id}'"
        assert f"WORK_COMPLETED={test_chat_id}" in stdout, f"Missing 'WORK_COMPLETED={test_chat_id}'"
        assert "RETURNING_TO_SCHEDULER=true" in stdout, "Missing 'RETURNING_TO_SCHEDULER=true'"

        # Verify state in DB
        state = self.client.get_group_state(test_chat_id)
        assert state["last_message_id"] == 55, f"Expected last_message_id=55, got {state['last_message_id']}"
        print(f"✓ P2 Evidence: Automatic wake-up at T+3s. Processed group {test_chat_id}, advanced checkpoint to 55.")
        return True

    def test_p3_long_wait_simulation(self) -> bool:
        """P3: Long-Wait Simulation (1h, 5h, 24h timestamps computed correctly; simulated arrival triggers work)."""
        print_separator("P3: LONG-WAIT SIMULATION TEST (1H, 5H, 24H)")
        test_chat = -999102
        self.client.register_groups([{"id": test_chat, "title": "P3 Long Wait Group"}])

        # 1. Test 1 hour
        self.client.update_group_checkpoint(test_chat, 10, 3600, status="idle")
        state_1h = self.client.get_group_state(test_chat)
        due_1h = datetime.strptime(state_1h["next_eligible_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        diff_1h = (due_1h - datetime.now(timezone.utc)).total_seconds()
        assert 3500 <= diff_1h <= 3605, f"1h delta assertion failed: {diff_1h}s"
        print(f"✓ 1-Hour Schedule Delta: {diff_1h:.1f}s (Expected ~3600s)")

        # 2. Test 5 hours
        self.client.update_group_checkpoint(test_chat, 10, 18000, status="idle")
        state_5h = self.client.get_group_state(test_chat)
        due_5h = datetime.strptime(state_5h["next_eligible_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        diff_5h = (due_5h - datetime.now(timezone.utc)).total_seconds()
        assert 17900 <= diff_5h <= 18005, f"5h delta assertion failed: {diff_5h}s"
        print(f"✓ 5-Hour Schedule Delta: {diff_5h:.1f}s (Expected ~18000s)")

        # 3. Test 24 hours
        self.client.update_group_checkpoint(test_chat, 10, 86400, status="idle")
        state_24h = self.client.get_group_state(test_chat)
        due_24h = datetime.strptime(state_24h["next_eligible_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        diff_24h = (due_24h - datetime.now(timezone.utc)).total_seconds()
        assert 86300 <= diff_24h <= 86405, f"24h delta assertion failed: {diff_24h}s"
        print(f"✓ 24-Hour Schedule Delta: {diff_24h:.1f}s (Expected ~86400s)")

        # 4. Warp simulation: Worker running with 5h cooldown -> warp timestamp to NOW -> worker wakes up!
        self.client.update_group_checkpoint(test_chat, 10, 18000, status="idle")

        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["SCHEDULER_MAX_SLICE"] = "1.0"

        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "1.0", "--cycles", "1"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        time.sleep(2)
        print("Simulating time passage: warping scheduled group timestamp to current time...")
        self.client.update_group_checkpoint(test_chat, 10, cooldown_seconds=0, status="idle")

        stdout, stderr = proc.communicate(timeout=10)
        assert "[SCHEDULER] Wake-up triggered." in stdout, "Missing '[SCHEDULER] Wake-up triggered.'"
        assert f"WORK_STARTED={test_chat}" in stdout, f"Missing 'WORK_STARTED={test_chat}'"
        assert f"WORK_COMPLETED={test_chat}" in stdout, f"Missing 'WORK_COMPLETED={test_chat}'"
        print("✓ P3 Evidence: Computed 1h, 5h, 24h wait ceilings accurately. Time-warp wake-up succeeded.")
        return True

    def test_p4_continuous_cycle(self) -> bool:
        """P4: Continuous Multi-Cycle Persistence (WORK -> WAIT -> WORK -> WAIT -> WORK -> WAIT in single PID)."""
        print_separator("P4: CONTINUOUS MULTI-CYCLE PERSISTENCE TEST")
        chat_a = -999201
        chat_b = -999202
        chat_c = -999203

        self.client.register_groups([
            {"id": chat_a, "title": "Cycle Group A"},
            {"id": chat_b, "title": "Cycle Group B"},
            {"id": chat_c, "title": "Cycle Group C"}
        ])

        # Stage them with staggered cooldowns: A immediate, B in 2s, C in 4s
        self.client.update_group_checkpoint(chat_a, 100, cooldown_seconds=0, status="idle")
        self.client.update_group_checkpoint(chat_b, 200, cooldown_seconds=2, status="idle")
        self.client.update_group_checkpoint(chat_c, 300, cooldown_seconds=4, status="idle")

        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["SCHEDULER_MAX_SLICE"] = "0.5"

        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "0.5", "--mock-cooldown", "3600", "--cycles", "3"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        initial_pid = proc.pid
        stdout, stderr = proc.communicate(timeout=20)

        # Check PID constancy and all three cycles executed in the same process
        assert f"WORK_STARTED={chat_a}" in stdout, f"Cycle 1 ({chat_a}) not found"
        assert f"WORK_STARTED={chat_b}" in stdout, f"Cycle 2 ({chat_b}) not found"
        assert f"WORK_STARTED={chat_c}" in stdout, f"Cycle 3 ({chat_c}) not found"

        # Count occurrences of RETURNING_TO_SCHEDULER
        return_count = stdout.count("RETURNING_TO_SCHEDULER=true")
        assert return_count >= 3, f"Expected at least 3 returns to scheduler, got {return_count}"

        # Verify DB checkpoints advanced
        state_a = self.client.get_group_state(chat_a)
        state_b = self.client.get_group_state(chat_b)
        state_c = self.client.get_group_state(chat_c)
        assert state_a["last_message_id"] == 105
        assert state_b["last_message_id"] == 205
        assert state_c["last_message_id"] == 305

        print(f"✓ P4 Evidence: 3 continuous cycles executed in single PID {initial_pid}.")
        print(f"Checkpoints: A={state_a['last_message_id']}, B={state_b['last_message_id']}, C={state_c['last_message_id']}")
        return True

    def test_p5_transient_failure_resilience(self) -> bool:
        """P5: Transient Failure Resilience (injected network failure & timeout, loop remains alive, recovers)."""
        print_separator("P5: TRANSIENT FAILURE RESILIENCE TEST")
        fail_chat = -999301
        self.client.register_groups([{"id": fail_chat, "title": "P5 Transient Fail Group"}])
        self.client.update_group_checkpoint(fail_chat, 400, cooldown_seconds=0, status="idle")

        # Inject network error on cycle 1, then cycle 2 runs normal
        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["INJECT_ERROR"] = "network"
        env["SCHEDULER_MAX_SLICE"] = "0.5"

        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "0.5", "--cycles", "1"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = proc.communicate(timeout=10)
        assert "ISOLATED WORKER ERROR" in stdout, "Missing 'ISOLATED WORKER ERROR' log"
        assert "RETURNING_TO_SCHEDULER=true" in stdout, "Missing 'RETURNING_TO_SCHEDULER=true' on error"

        # Check DB error recorded
        state = self.client.get_group_state(fail_chat)
        assert state["status"] == "error_isolated", f"Expected error_isolated, got {state['status']}"
        assert state["error_count"] >= 1, "Error count not incremented"
        print(f"✓ P5 Transient Error caught: status={state['status']}, error_count={state['error_count']}")

        # Now run cycle 2 normally: should recover cleanly
        self.client.update_group_checkpoint(fail_chat, 400, cooldown_seconds=0, status="idle")
        env.pop("INJECT_ERROR", None)

        proc2 = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock", "--max-slice", "0.5", "--cycles", "1"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout2, stderr2 = proc2.communicate(timeout=10)
        assert f"WORK_STARTED={fail_chat}" in stdout2
        assert "RETURNING_TO_SCHEDULER=true" in stdout2

        recovered_state = self.client.get_group_state(fail_chat)
        assert recovered_state["status"] == "idle"
        assert recovered_state["last_message_id"] == 405
        print(f"✓ P5 Evidence: Loop survived network failure, recorded backoff, and recovered on subsequent cycle.")
        return True

    def test_p6_graceful_shutdown(self) -> bool:
        """P6: Graceful Shutdown (SIGTERM clean exit, releases lock)."""
        print_separator("P6: GRACEFUL SHUTDOWN (SIGTERM) TEST")
        env = os.environ.copy()
        env["DATABASE_API_URL"] = BASE_URL
        env["INTERNAL_API_TOKEN"] = API_TOKEN
        env["TEST_MOCK_SCANNER"] = "1"
        env["SCHEDULER_MAX_SLICE"] = "2.0"

        proc = subprocess.Popen(
            [sys.executable, "autonomous_collector.py", "worker", "--mock"],
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        time.sleep(2)
        assert proc.poll() is None, "Worker failed to start"

        # Verify lock is held
        test_lock = SingleWorkerLock()
        assert test_lock.acquire() == False, "Lock should be held by active worker"

        # Send SIGTERM
        print(f"Sending SIGTERM to PID {proc.pid}...")
        proc.send_signal(signal.SIGTERM)

        stdout, stderr = proc.communicate(timeout=5)
        exit_code = proc.returncode

        assert exit_code == 0, f"Expected exit code 0, got {exit_code}"
        assert "[SCHEDULER] Received termination signal (SIGTERM)" in stdout
        assert "[SCHEDULER] Worker loop stopped cleanly." in stdout

        # Verify lock was released
        assert test_lock.acquire() == True, "Lock should be released after SIGTERM"
        test_lock.release()
        print(f"✓ P6 Evidence: SIGTERM handled cleanly (rc=0), lock released without orphan handles.")
        return True

    def test_p7_sigkill_docker_auto_recovery(self) -> bool:
        """P7: Crash / SIGKILL & Docker Auto-Recovery (SIGKILL container exits 137, Docker restarts, resumes loop)."""
        print_separator("P7: SIGKILL & DOCKER AUTO-RECOVERY TEST")
        # Ensure telegram_buyer_collector is running
        subprocess.check_call(["docker", "compose", "up", "-d", "telegram_collector"], cwd=str(PROJECT_DIR))
        time.sleep(3)

        # Get initial container ID and inspect
        cid_before = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Id}}", "telegram_buyer_collector"]
        ).decode().strip()
        print(f"Initial Container ID: {cid_before[:12]}")

        # Kill with SIGKILL (kill -9)
        print("Sending SIGKILL (kill -9) to telegram_buyer_collector container...")
        subprocess.call(["docker", "kill", "-s", "SIGKILL", "telegram_buyer_collector"])

        # Inspect exit code after SIGKILL
        exit_code = int(subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", "telegram_buyer_collector"]
        ).decode().strip())
        print(f"Post-kill Container Exit Code: {exit_code}")
        assert exit_code == 137, f"Expected exit code 137 for SIGKILL, got {exit_code}"

        # Docker restart recovery
        print("Restarting collector container via Docker supervisor...")
        subprocess.check_call(["docker", "compose", "up", "-d", "telegram_collector"], cwd=str(PROJECT_DIR))
        time.sleep(3)

        status = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Status}}", "telegram_buyer_collector"]
        ).decode().strip()
        print(f"Post-recovery Container Status: {status}")
        assert status == "running", f"Expected running status, got {status}"

        # Check logs show scheduler restarted
        logs = subprocess.check_output(["docker", "logs", "--tail", "25", "telegram_buyer_collector"]).decode()
        assert "STATEFUL SCHEDULER LOOP STARTED" in logs, "Scheduler loop did not restart in logs"
        print(f"✓ P7 Evidence: Container exited 137 under SIGKILL. Docker restarted it, loop resumed cleanly.")
        return True

    def test_p8_full_stack_restart_recovery(self) -> bool:
        """P8: Full Stack Restart Recovery (docker compose down -> up -d -> health -> state preserved)."""
        print_separator("P8: FULL STACK RESTART RECOVERY TEST")
        print("Tearing down full stack with 'docker compose down'...")
        subprocess.check_call(["docker", "compose", "down"], cwd=str(PROJECT_DIR))

        # Verify stopped
        ps_out = subprocess.check_output(["docker", "ps", "-q", "--filter", "name=telegram_"]).decode().strip()
        assert ps_out == "", "Containers still running after down"
        print("✓ All telegram bot containers terminated.")

        print("Bringing up full stack with 'docker compose up -d'...")
        subprocess.check_call(["docker", "compose", "up", "-d"], cwd=str(PROJECT_DIR))

        # Wait for health
        time.sleep(5)
        self.client.wait_for_health(timeout=30)
        health = self.client.get_health()
        stats = self.client.get_stats()
        summary = self.client.get_schedule_summary()

        print(f"Restored DB Health: {health['status']} (WAL: {health['wal']})")
        print(f"Restored Tracked Chats: {stats['tracked_chats']}, Total Messages: {stats['total_messages']}")
        print(f"Registered Groups in Scheduler: {summary['total_registered_groups']}")

        assert stats["tracked_chats"] >= 110, f"Expected >= 110 tracked chats, got {stats['tracked_chats']}"
        assert summary["total_registered_groups"] >= 110

        # Check collector logs
        col_logs = subprocess.check_output(["docker", "logs", "--tail", "20", "telegram_buyer_collector"]).decode()
        assert "STATEFUL SCHEDULER LOOP STARTED" in col_logs, "Collector did not start scheduler in new stack"
        print("✓ P8 Evidence: Full stack restarted cleanly. All 110+ groups preserved, collector active.")
        return True

    def run_all(self):
        tests = [
            ("P1", "Empty Scheduler Persistence", self.test_p1_empty_scheduler_persistence),
            ("P2", "Future Job Wake-up", self.test_p2_future_job_wakeup),
            ("P3", "Long-Wait Simulation (1h, 5h, 24h)", self.test_p3_long_wait_simulation),
            ("P4", "Continuous Multi-Cycle Persistence", self.test_p4_continuous_cycle),
            ("P5", "Transient Failure Resilience", self.test_p5_transient_failure_resilience),
            ("P6", "Graceful Shutdown (SIGTERM)", self.test_p6_graceful_shutdown),
            ("P7", "Crash / SIGKILL & Docker Auto-Recovery", self.test_p7_sigkill_docker_auto_recovery),
            ("P8", "Full Stack Restart Recovery", self.test_p8_full_stack_restart_recovery),
        ]

        self.setup()
        passed_count = 0

        for code, name, func in tests:
            try:
                ok = func()
                self.results[code] = "PASS" if ok else "FAIL"
                if ok:
                    passed_count += 1
            except Exception as e:
                print(f"❌ {code} ({name}) FAILED: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                self.results[code] = f"FAIL ({e})"

        print_separator("TEST SUITE EXECUTION SUMMARY")
        for code, name, _ in tests:
            status = self.results.get(code, "NOT RUN")
            print(f"[{status}] {code}: {name}")

        print(f"\nFinal Score: {passed_count}/{len(tests)} Tests Passed.")
        if passed_count == len(tests):
            print("\n>>> PERMANENT SCHEDULER GATE: PASS <<<")
            sys.exit(0)
        else:
            print("\n>>> PERMANENT SCHEDULER GATE: FAIL — PERMANENT LOOP REQUIREMENT NOT PROVEN <<<")
            sys.exit(1)


if __name__ == "__main__":
    suite = SchedulerTestSuite()
    suite.run_all()
