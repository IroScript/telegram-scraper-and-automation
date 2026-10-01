import os
import sys
import time
import subprocess
import signal
from pathlib import Path
from lock_manager import SingleWorkerLock

def run_lock_tests():
    lock_path = Path(".test_lock_lifecycle.lock")
    if lock_path.exists():
        lock_path.unlink()

    # --- Subtest 3.1: Reject Second Worker ---
    print("\n--- Subtest 3.1: Reject Second Worker ---")
    p1 = subprocess.Popen([sys.executable, "-c", f"""
import time
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
if l.acquire():
    print("WORKER_1_LOCKED", flush=True)
    time.sleep(30)
"""], stdout=subprocess.PIPE, text=True)

    out1 = p1.stdout.readline().strip()
    print("Worker 1:", out1)
    assert out1 == "WORKER_1_LOCKED"

    p2 = subprocess.Popen([sys.executable, "-c", f"""
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
acquired = l.acquire()
print(f"WORKER_2_ACQUIRED_{{acquired}}", flush=True)
"""], stdout=subprocess.PIPE, text=True)

    out2 = p2.stdout.read().strip()
    print("Worker 2 Output:\n", out2)
    assert "WORKER_2_ACQUIRED_False" in out2
    p2.wait()
    print("Subtest 3.1 Result: PASS (Second worker rejected with LOCK CONFLICT)")

    # --- Subtest 3.2: Normal Exit Releases Lock ---
    print("\n--- Subtest 3.2: Normal Exit Releases Lock ---")
    os.kill(p1.pid, signal.SIGINT) # clean interrupt
    p1.wait()
    time.sleep(0.5)

    p3 = subprocess.Popen([sys.executable, "-c", f"""
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
print(f"WORKER_3_ACQUIRED_{{l.acquire()}}", flush=True)
l.release()
"""], stdout=subprocess.PIPE, text=True)
    out3 = p3.stdout.read().strip()
    print("Worker 3:", out3)
    assert "WORKER_3_ACQUIRED_True" in out3
    p3.wait()
    print("Subtest 3.2 Result: PASS (Normal exit released lock)")

    # --- Subtest 3.3: SIGTERM Releases Lock ---
    print("\n--- Subtest 3.3: SIGTERM Releases Lock ---")
    p4 = subprocess.Popen([sys.executable, "-c", f"""
import time
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
if l.acquire():
    print("WORKER_4_LOCKED", flush=True)
    time.sleep(30)
"""], stdout=subprocess.PIPE, text=True)
    out4 = p4.stdout.readline().strip()
    print("Worker 4:", out4)
    assert out4 == "WORKER_4_LOCKED"

    os.kill(p4.pid, signal.SIGTERM)
    p4.wait()
    time.sleep(0.5)

    p5 = subprocess.Popen([sys.executable, "-c", f"""
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
print(f"WORKER_5_ACQUIRED_{{l.acquire()}}", flush=True)
l.release()
"""], stdout=subprocess.PIPE, text=True)
    out5 = p5.stdout.read().strip()
    print("Worker 5:", out5)
    assert "WORKER_5_ACQUIRED_True" in out5
    p5.wait()
    print("Subtest 3.3 Result: PASS (SIGTERM cleanly released lock)")

    # --- Subtest 3.4: SIGKILL Safe Recovery ---
    print("\n--- Subtest 3.4: SIGKILL Safe Recovery ---")
    p6 = subprocess.Popen([sys.executable, "-c", f"""
import time
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
if l.acquire():
    print("WORKER_6_LOCKED", flush=True)
    time.sleep(30)
"""], stdout=subprocess.PIPE, text=True)
    out6 = p6.stdout.readline().strip()
    print("Worker 6:", out6)
    assert out6 == "WORKER_6_LOCKED"

    os.kill(p6.pid, signal.SIGKILL) # Sudden kernel death
    p6.wait()
    time.sleep(0.5)

    p7 = subprocess.Popen([sys.executable, "-c", f"""
from pathlib import Path
from lock_manager import SingleWorkerLock
l = SingleWorkerLock(Path("{lock_path}"))
print(f"WORKER_7_ACQUIRED_{{l.acquire()}}", flush=True)
l.release()
"""], stdout=subprocess.PIPE, text=True)
    out7 = p7.stdout.read().strip()
    print("Worker 7:", out7)
    assert "WORKER_7_ACQUIRED_True" in out7
    p7.wait()
    print("Subtest 3.4 Result: PASS (SIGKILL immediately recovered by next worker)")

    # --- Subtest 3.5: Stale Lock with dead PID in file ---
    print("\n--- Subtest 3.5: Stale Lock Recovery ---")
    with open(lock_path, "w") as f:
        f.write("PID: 99999999\n") # Non-existent dead PID text in file

    l_stale = SingleWorkerLock(lock_path)
    acq_stale = l_stale.acquire()
    print("Stale file lock acquisition:", acq_stale)
    assert acq_stale == True, "New worker must acquire lock even if file contains dead PID"
    l_stale.release()
    print("Subtest 3.5 Result: PASS (Stale lock file does not block new worker)")

    # Clean up
    if lock_path.exists():
        lock_path.unlink()
    print("\nALL PART 3 SINGLE-WORKER LOCK RECOVERY TESTS: 100% PASS!\n")

if __name__ == "__main__":
    run_lock_tests()
