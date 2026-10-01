"""
Process-Level Concurrency Lock Manager for Telegram Collector
File: lock_manager.py

Guarantees that only ONE collector worker can access the Telegram session file
at any given moment. Prevents parallel workers and Telethon session database corruption.
"""

import os
import sys
import fcntl
from pathlib import Path
from typing import Optional

DEFAULT_LOCK_FILE = Path(__file__).parent.resolve() / ".collector.lock"


class SingleWorkerLock:
    def __init__(self, lock_file: Optional[Path] = None):
        self.lock_file = Path(lock_file or DEFAULT_LOCK_FILE)
        self.fp = None
        self.locked = False

    def acquire(self) -> bool:
        """Attempt non-blocking exclusive lock. Returns True if acquired, False if already running."""
        try:
            self.fp = open(self.lock_file, "a+")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.seek(0)
            self.fp.truncate()
            self.fp.write(f"PID: {os.getpid()}\n")
            self.fp.flush()
            self.locked = True
            return True
        except (IOError, BlockingIOError):
            self.fp.seek(0)
            active_info = self.fp.read().strip()
            print(f"❌ [LOCK CONFLICT] Another worker instance is already active ({active_info}).")
            return False

    def release(self) -> None:
        """Release the exclusive lock cleanly."""
        if self.fp and self.locked:
            try:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                self.fp.close()
            except Exception:
                pass
            self.locked = False
            self.fp = None

    def __enter__(self):
        if not self.acquire():
            sys.exit(1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


if __name__ == "__main__":
    lock = SingleWorkerLock()
    if lock.acquire():
        print(f"Lock acquired successfully for PID {os.getpid()}")
        lock.release()
    else:
        print("Failed to acquire lock.")
