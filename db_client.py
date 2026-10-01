"""
Database API Client for Telegram Buyer Lead Collector
File: db_client.py

Architecture Standards:
- Pure HTTP REST Client communicating with telegram_database_api container
- Collector NEVER accesses SQLite buyers.db directly
- Built-in connection retry, exponential backoff, and auth header injection
- Ensures clean abstraction identical to legacy database module
"""

import os
import time
import json
import logging
from typing import Dict, Any, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("db_client")


class DatabaseApiClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 10.0
    ):
        self.base_url = (base_url or os.getenv("DATABASE_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.api_token = api_token or os.getenv("INTERNAL_API_TOKEN", "")
        self.timeout = timeout

        self.session = requests.Session()
        # Retry on 500, 502, 503, 504
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["X-API-Key"] = self.api_token
        return headers

    def wait_for_health(self, timeout: float = 30.0, poll_interval: float = 1.0) -> bool:
        """Poll /health endpoint until database API reports healthy status."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                res = self.session.get(f"{self.base_url}/health", timeout=3.0)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") == "healthy":
                        logger.info("Database API is healthy and reachable.")
                        return True
            except Exception as e:
                logger.debug(f"Waiting for Database API: {e}")
            time.sleep(poll_interval)

        raise ConnectionError(f"Database API at {self.base_url} failed to report healthy within {timeout}s")

    def get_health(self) -> Dict[str, Any]:
        res = self.session.get(f"{self.base_url}/health", timeout=self.timeout)
        res.raise_for_status()
        return res.json()

    def get_stats(self) -> Dict[str, Any]:
        res = self.session.get(f"{self.base_url}/stats", headers=self._headers(), timeout=self.timeout)
        res.raise_for_status()
        return res.json()

    def get_next_eligible_group(self) -> Optional[Dict[str, Any]]:
        res = self.session.get(f"{self.base_url}/groups/next", headers=self._headers(), timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        return data.get("group")

    def get_all_groups(self) -> List[Dict[str, Any]]:
        res = self.session.get(f"{self.base_url}/groups", headers=self._headers(), timeout=self.timeout)
        res.raise_for_status()
        return res.json().get("groups", [])

    def get_group_state(self, chat_id: int) -> Optional[Dict[str, Any]]:
        res = self.session.get(f"{self.base_url}/groups/{chat_id}/state", headers=self._headers(), timeout=self.timeout)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return res.json()

    def update_group_checkpoint(
        self,
        chat_id: int,
        last_message_id: int,
        cooldown_seconds: float,
        status: str = "idle",
        error: Optional[str] = None
    ) -> bool:
        payload = {
            "last_message_id": last_message_id,
            "cooldown_seconds": cooldown_seconds,
            "status": status,
            "error": error
        }
        res = self.session.post(
            f"{self.base_url}/groups/{chat_id}/checkpoint",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("updated", False)

    def report_group_error(
        self,
        chat_id: int,
        error: str,
        cooldown_seconds: float = 300.0,
        status: str = "error"
    ) -> bool:
        payload = {
            "error": error,
            "cooldown_seconds": cooldown_seconds,
            "status": status
        }
        res = self.session.post(
            f"{self.base_url}/groups/{chat_id}/error",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("error_recorded", False)

    def schedule_group(
        self,
        chat_id: int,
        cooldown_seconds: float,
        status: str = "idle"
    ) -> bool:
        payload = {
            "cooldown_seconds": cooldown_seconds,
            "status": status
        }
        res = self.session.post(
            f"{self.base_url}/groups/{chat_id}/schedule",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("scheduled", False)

    def insert_raw_message(self, msg: Dict[str, Any]) -> bool:
        """Insert raw message via API. Returns True if new, False if duplicate."""
        res = self.session.post(
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=msg,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("inserted", False)

    def insert_buyer(self, buyer: Dict[str, Any]) -> bool:
        """Insert buyer record via API. Returns True if new record inserted."""
        res = self.session.post(
            f"{self.base_url}/buyers",
            headers=self._headers(),
            json=buyer,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("inserted", False)

    def get_messages_by_chat_id(self, chat_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        res = self.session.get(
            f"{self.base_url}/messages/{chat_id}",
            params={"limit": limit},
            headers=self._headers(),
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("messages", [])

    def get_recent_buyers(self, limit: int = 20) -> List[Dict[str, Any]]:
        res = self.session.get(
            f"{self.base_url}/buyers",
            params={"limit": limit},
            headers=self._headers(),
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("buyers", [])

    def set_global_flood_wait(self, seconds: int, reason: str = "FloodWait") -> bool:
        payload = {"seconds": seconds, "reason": reason}
        res = self.session.post(
            f"{self.base_url}/flood-wait",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("status") == "ok"

    def get_schedule_summary(self) -> Dict[str, Any]:
        res = self.session.get(f"{self.base_url}/scheduler/status", headers=self._headers(), timeout=self.timeout)
        res.raise_for_status()
        return res.json()

    def register_groups(self, groups: List[Dict[str, Any]]) -> int:
        payload = {"groups": groups}
        res = self.session.post(
            f"{self.base_url}/groups/register",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout
        )
        res.raise_for_status()
        return res.json().get("registered", 0)


# Module-level default singleton instance
default_client = DatabaseApiClient()

# Convenience functional wrappers matching database module interface
def get_next_eligible_group() -> Optional[Dict[str, Any]]:
    return default_client.get_next_eligible_group()

def update_group_checkpoint(
    chat_id: int,
    last_message_id: int,
    cooldown_seconds: float,
    status: str = "idle",
    error: Optional[str] = None
) -> bool:
    return default_client.update_group_checkpoint(chat_id, last_message_id, cooldown_seconds, status, error)

def insert_raw_message(msg: Dict[str, Any]) -> bool:
    return default_client.insert_raw_message(msg)

def insert_buyer(buyer: Dict[str, Any]) -> bool:
    return default_client.insert_buyer(buyer)

def register_groups(groups: List[Dict[str, Any]]) -> int:
    return default_client.register_groups(groups)

def set_global_flood_wait(seconds: int, reason: str = "FloodWait") -> bool:
    return default_client.set_global_flood_wait(seconds, reason)

def get_schedule_summary() -> Dict[str, Any]:
    return default_client.get_schedule_summary()
