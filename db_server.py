"""
Internal SQLite REST API Service for Telegram Buyer Lead Collector
File: db_server.py

Architecture Standards:
- Exposes SQLite database via high-performance async REST endpoints
- Enforces strict isolation: Collector never accesses buyers.db directly
- Handles SQLite concurrency, WAL journal mode, atomic transactions, and deduplication
- Internal Docker network bound (non-public), protected by internal token
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from aiohttp import web
from datetime import datetime, timezone

import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DB_API] %(message)s"
)
logger = logging.getLogger("db_server")

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

# --- Security Middleware ---
@web.middleware
async def auth_middleware(request: web.Request, handler):
    # Whitelist /health and / for basic status
    if request.path in ["/health", "/"]:
        return await handler(request)

    if INTERNAL_API_TOKEN:
        token = request.headers.get("X-API-Key")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif auth_header:
                token = auth_header.strip()

        if token != INTERNAL_API_TOKEN:
            logger.warning(f"Unauthorized access attempt to {request.path} from {request.remote}")
            return web.json_response(
                {"error": "Unauthorized", "message": "Invalid or missing internal API token"},
                status=401
            )

    return await handler(request)


# --- Endpoint Handlers ---

async def handle_root(request: web.Request) -> web.Response:
    return web.json_response({
        "service": "telegram_database_api",
        "version": "1.0.0",
        "status": "online"
    })


async def handle_health(request: web.Request) -> web.Response:
    """GET /health: Verify database connectivity, WAL mode, and integrity."""
    try:
        integrity = database.run_integrity_check()
        conn = database.get_connection()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        is_healthy = (integrity == "ok") and (journal.lower() == "wal")
        status_code = 200 if is_healthy else 500

        return web.json_response({
            "status": "healthy" if is_healthy else "degraded",
            "database": "connected",
            "wal": journal.lower() == "wal",
            "integrity": integrity,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }, status=status_code)
    except Exception as e:
        logger.error(f"Healthcheck error: {e}", exc_info=True)
        return web.json_response({
            "status": "unhealthy",
            "database": "error",
            "error": str(e)
        }, status=500)


async def handle_stats(request: web.Request) -> web.Response:
    """GET /stats: Total counts and storage stats."""
    try:
        counts = database.get_counts()
        conn = database.get_connection()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        return web.json_response({
            **counts,
            "journal_mode": journal
        })
    except Exception as e:
        logger.error(f"Stats error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_all_groups(request: web.Request) -> web.Response:
    """GET /groups: Retrieve all registered groups metadata."""
    try:
        conn = database.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT chat_id, chat_title, chat_username, last_message_id, status FROM scrape_state")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return web.json_response({"groups": rows})
    except Exception as e:
        logger.error(f"Error fetching all groups: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_next_group(request: web.Request) -> web.Response:
    """GET /groups/next: Retrieve next eligible group whose cooldown has expired."""
    try:
        group = database.get_next_eligible_group()
        return web.json_response({
            "group": group
        })
    except Exception as e:
        logger.error(f"Error fetching next eligible group: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_group_state(request: web.Request) -> web.Response:
    """GET /groups/{chat_id}/state: Retrieve current state record for a single group."""
    try:
        chat_id = int(request.match_info["chat_id"])
        state = database.get_group_state(chat_id)
        if state is None:
            return web.json_response(
                {"error": "Not Found", "message": f"Group {chat_id} not registered in state engine"},
                status=404
            )
        return web.json_response(state)
    except ValueError:
        return web.json_response({"error": "Invalid chat_id parameter, integer expected"}, status=400)
    except Exception as e:
        logger.error(f"Error fetching group state: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_checkpoint(request: web.Request) -> web.Response:
    """POST /groups/{chat_id}/checkpoint: Atomically record last processed message ID and cooldown."""
    try:
        chat_id = int(request.match_info["chat_id"])
        data = await request.json()

        last_message_id = int(data.get("last_message_id", 0))
        cooldown_seconds = float(data.get("cooldown_seconds", 3600.0))
        status = data.get("status", "idle")
        error = data.get("error")

        database.update_group_checkpoint(
            chat_id=chat_id,
            last_message_id=last_message_id,
            cooldown_seconds=cooldown_seconds,
            status=status,
            error=error
        )

        return web.json_response({
            "status": "ok",
            "updated": True,
            "chat_id": chat_id,
            "last_message_id": last_message_id
        })
    except (ValueError, json.JSONDecodeError) as e:
        return web.json_response({"error": f"Invalid payload or chat_id: {e}"}, status=400)
    except Exception as e:
        logger.error(f"Error updating checkpoint: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_error(request: web.Request) -> web.Response:
    """POST /groups/{chat_id}/error: Record error and schedule retry cooldown."""
    try:
        chat_id = int(request.match_info["chat_id"])
        data = await request.json()

        error = data.get("error", "Unknown error")
        cooldown_seconds = float(data.get("cooldown_seconds", 300.0))
        status = data.get("status", "error")

        # Keep existing last_message_id, increment error count
        database.update_group_checkpoint(
            chat_id=chat_id,
            last_message_id=0,
            cooldown_seconds=cooldown_seconds,
            status=status,
            error=error
        )

        return web.json_response({
            "status": "ok",
            "error_recorded": True,
            "chat_id": chat_id
        })
    except (ValueError, json.JSONDecodeError) as e:
        return web.json_response({"error": f"Invalid payload or chat_id: {e}"}, status=400)
    except Exception as e:
        logger.error(f"Error recording group error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_schedule(request: web.Request) -> web.Response:
    """POST /groups/{chat_id}/schedule: Update next_eligible_at."""
    try:
        chat_id = int(request.match_info["chat_id"])
        data = await request.json()

        cooldown_seconds = float(data.get("cooldown_seconds", 7200.0))
        status = data.get("status", "idle")

        database.update_group_checkpoint(
            chat_id=chat_id,
            last_message_id=0,
            cooldown_seconds=cooldown_seconds,
            status=status,
            error=None
        )

        return web.json_response({
            "status": "ok",
            "scheduled": True,
            "chat_id": chat_id,
            "cooldown_seconds": cooldown_seconds
        })
    except (ValueError, json.JSONDecodeError) as e:
        return web.json_response({"error": f"Invalid payload or chat_id: {e}"}, status=400)
    except Exception as e:
        logger.error(f"Error scheduling group: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_messages(request: web.Request) -> web.Response:
    """POST /messages: Store raw message. Enforces UNIQUE(chat_id, message_id) duplicate immunity."""
    try:
        msg = await request.json()
        if not msg.get("message_id") or not msg.get("chat_id"):
            return web.json_response({"error": "message_id and chat_id are required fields"}, status=400)

        inserted = database.insert_raw_message(msg)
        return web.json_response({
            "inserted": inserted,
            "duplicate": not inserted,
            "message_id": msg["message_id"],
            "chat_id": msg["chat_id"]
        }, status=201 if inserted else 200)
    except json.JSONDecodeError:
        return web.json_response({"error": "Malformed JSON payload"}, status=400)
    except Exception as e:
        logger.error(f"Error inserting message: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_buyers(request: web.Request) -> web.Response:
    """POST /buyers: Store identified buyer lead record."""
    try:
        buyer = await request.json()
        if not buyer.get("message_id") or not buyer.get("chat_id"):
            return web.json_response({"error": "message_id and chat_id are required fields"}, status=400)

        inserted = database.insert_buyer(buyer)
        return web.json_response({
            "inserted": inserted,
            "message_id": buyer["message_id"],
            "chat_id": buyer["chat_id"]
        }, status=201 if inserted else 200)
    except json.JSONDecodeError:
        return web.json_response({"error": "Malformed JSON payload"}, status=400)
    except Exception as e:
        logger.error(f"Error inserting buyer: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_messages_by_chat(request: web.Request) -> web.Response:
    """GET /messages/{chat_id}: Retrieve recent messages for a group."""
    try:
        chat_id = int(request.match_info["chat_id"])
        limit = int(request.query.get("limit", 20))
        messages = database.get_messages_by_chat_id(chat_id, limit=limit)
        return web.json_response({
            "chat_id": chat_id,
            "count": len(messages),
            "messages": messages
        })
    except ValueError:
        return web.json_response({"error": "Invalid chat_id or limit"}, status=400)
    except Exception as e:
        logger.error(f"Error retrieving messages: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_buyers(request: web.Request) -> web.Response:
    """GET /buyers: Retrieve recent buyer leads."""
    try:
        limit = int(request.query.get("limit", 20))
        buyers = database.get_recent_buyers(limit=limit)
        return web.json_response({
            "count": len(buyers),
            "buyers": buyers
        })
    except ValueError:
        return web.json_response({"error": "Invalid limit parameter"}, status=400)
    except Exception as e:
        logger.error(f"Error retrieving buyers: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_flood_wait(request: web.Request) -> web.Response:
    """POST /flood-wait: Set global pause cooldown across all registered groups."""
    try:
        data = await request.json()
        seconds = int(data.get("seconds", 60))
        reason = data.get("reason", "FloodWait")

        database.set_global_flood_wait(seconds)
        logger.warning(f"Global FloodWait pause triggered: {seconds}s ({reason})")

        return web.json_response({
            "status": "ok",
            "action": "global_flood_wait_applied",
            "paused_seconds": seconds,
            "reason": reason
        })
    except (ValueError, json.JSONDecodeError) as e:
        return web.json_response({"error": f"Invalid payload: {e}"}, status=400)
    except Exception as e:
        logger.error(f"Error applying flood wait: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_scheduler_status(request: web.Request) -> web.Response:
    """GET /scheduler/status: Global scheduler queue state and due times."""
    try:
        summary = database.get_schedule_summary()
        return web.json_response(summary)
    except Exception as e:
        logger.error(f"Error retrieving scheduler status: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def handle_post_register_groups(request: web.Request) -> web.Response:
    """POST /groups/register: Batch register/sync group metadata."""
    try:
        data = await request.json()
        groups = data.get("groups", [])
        if not isinstance(groups, list):
            return web.json_response({"error": "groups must be an array"}, status=400)

        count = database.register_groups(groups)
        return web.json_response({
            "status": "ok",
            "registered": count
        })
    except json.JSONDecodeError:
        return web.json_response({"error": "Malformed JSON payload"}, status=400)
    except Exception as e:
        logger.error(f"Error registering groups: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def create_app() -> web.Application:
    # Initialize DB schema if not already present
    database.init_db()

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/groups", handle_get_all_groups)
    app.router.add_get("/groups/next", handle_get_next_group)
    app.router.add_get("/groups/{chat_id}/state", handle_get_group_state)
    app.router.add_post("/groups/{chat_id}/checkpoint", handle_post_checkpoint)
    app.router.add_post("/groups/{chat_id}/error", handle_post_error)
    app.router.add_post("/groups/{chat_id}/schedule", handle_post_schedule)
    app.router.add_post("/groups/register", handle_post_register_groups)
    app.router.add_post("/messages", handle_post_messages)
    app.router.add_post("/buyers", handle_post_buyers)
    app.router.add_get("/messages/{chat_id}", handle_get_messages_by_chat)
    app.router.add_get("/buyers", handle_get_buyers)
    app.router.add_post("/flood-wait", handle_post_flood_wait)
    app.router.add_get("/scheduler/status", handle_get_scheduler_status)
    return app


if __name__ == "__main__":
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", 8000))
    logger.info(f"Starting Telegram Database REST API on http://{host}:{port}")
    app = create_app()
    web.run_app(app, host=host, port=port)
