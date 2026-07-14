"""HTTP route handlers for the ATP server."""

import base64
import json
import sqlite3
import time
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from atp.core.message import ATPMessage
from atp.core.identity import AgentID
from atp.core.errors import MessageFormatError
from atp.storage.messages import MessageStatus

logger = logging.getLogger("atp.server")

# Reserved agent IDs that cannot be registered by users
RESERVED_AGENT_IDS = frozenset({
    "admin", "postmaster", "root", "system", "server",
    "abuse", "security", "noreply", "no-reply", "mailer-daemon",
})


def _check_admin_auth(request: Request) -> JSONResponse | None:
    """Verify admin token from Authorization: Bearer <token>.

    Returns None if authorized, or a 401/403 JSONResponse if not.
    The admin token is set via server config (admin_token).
    """
    server = request.app.state.server
    admin_token = getattr(server.config, "admin_token", None)
    if not admin_token:
        return JSONResponse(
            {"error": "Admin access not configured (set admin_token in server config)"},
            status_code=403,
        )
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Admin authentication required"}, status_code=401)
    if auth_header[7:] != admin_token:
        return JSONResponse({"error": "Invalid admin token"}, status_code=403)
    return None


async def handle_message(request: Request) -> JSONResponse:
    """POST /.well-known/atp/v1/message

    Validates, verifies, and enqueues an incoming ATP message.
    """
    server = request.app.state.server

    try:
        # 1. Read body, check size
        body = await request.body()
        if len(body) > server.config.max_message_size:
            return JSONResponse(
                status_code=400,
                content={"error": "Message too large", "max_size": server.config.max_message_size},
            )

        # 2. Parse ATPMessage
        try:
            body_str = body.decode("utf-8")
            message = ATPMessage.from_json(body_str)
        except (MessageFormatError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid message format", "details": str(exc)},
            )

        # 3. Source IP
        source_ip = request.client.host if request.client else "0.0.0.0"

        # 4. Sender domain — normalize from_id for consistent storage
        try:
            sender = AgentID.parse(message.from_id)
            sender_domain = sender.domain
            message.from_id = str(sender)
        except MessageFormatError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid sender ID", "details": str(exc)},
            )

        # 4b. Determine: local agent submission vs remote server transfer
        is_local_submission = (sender_domain == server.config.domain)

        if is_local_submission:
            # ── Local Agent → Server A: Credential Verify only ──
            # Server A is the agent's own server. Verify identity via
            # username+password (Basic Auth over TLS). No ATS+ATK needed.
            if hasattr(server, 'agent_store') and server.agent_store is not None:
                auth_header = request.headers.get("authorization", "")
                if not auth_header.startswith("Basic "):
                    return JSONResponse(
                        {"error": "Credential required for local agent"},
                        status_code=401,
                    )
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode()
                    auth_agent_id, auth_password = decoded.split(":", 1)
                except Exception:
                    return JSONResponse(
                        {"error": "Invalid Authorization header"},
                        status_code=401,
                    )

                if not server.agent_store.verify(auth_agent_id, auth_password):
                    logger.warning(f"Credential verification failed for {auth_agent_id}")
                    if server.metrics:
                        server.metrics.record_credential_failed()
                    return JSONResponse(
                        {"error": "Credential verification failed"},
                        status_code=401,
                    )

                # P0 fix: Bind credential identity to message sender.
                # Authenticated agent must match message "from" field,
                # otherwise any local user could impersonate another.
                # Compare normalized AgentIDs (case-insensitive).
                try:
                    auth_parsed = AgentID.parse(auth_agent_id)
                    from_parsed = AgentID.parse(message.from_id)
                except MessageFormatError:
                    return JSONResponse(
                        {"error": "Invalid agent ID in credential or message"},
                        status_code=400,
                    )
                if auth_parsed != from_parsed:
                    logger.warning(
                        f"Identity mismatch: authenticated as {auth_agent_id}, "
                        f"but message from={message.from_id}"
                    )
                    return JSONResponse(
                        {"error": "Sender identity mismatch: authenticated agent does not match message 'from'"},
                        status_code=403,
                    )

                if server.metrics:
                    server.metrics.record_credential_passed()

                logger.info(f"Credential verified for {auth_agent_id}")

        else:
            # ── Remote Server → Server B: ATS + ATK Verify ──
            # Message transferred from another ATP Server. Verify sender
            # authorization (ATS) and message integrity (ATK) via DNS.

            # ATS verify
            ats_result = await server.ats_verifier.verify(sender_domain, source_ip)
            server.metrics.record_ats(ats_result.status)
            if ats_result.status == "TEMPERROR":
                return JSONResponse(
                    status_code=451,
                    content={
                        "error": "ATS temporary DNS error, retry later",
                        "error_code": ats_result.error_code,
                    },
                )
            if ats_result.status == "FAIL":
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "ATS validation failed",
                        "error_code": ats_result.error_code,
                        "matched_directive": ats_result.matched_directive,
                    },
                )

            # ATK verify
            atk_result = await server.atk_verifier.verify(message)
            server.metrics.record_atk(atk_result.passed)
            if not atk_result.passed:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "ATK verification failed",
                        "error_code": atk_result.error_code,
                        "error_message": atk_result.error_message,
                    },
                )

        # 5. Replay check (both local and remote)
        if not server.replay_guard.check(
            message.nonce, message.timestamp, sender=message.from_id
        ):
            server.metrics.record_replay_blocked()
            return JSONResponse(
                status_code=400,
                content={"error": "Replay detected"},
            )

        # 8. Route decision — normalize to_id for case-insensitive mailbox lookup
        try:
            to_parsed = AgentID.parse(message.to_id)
            to_domain = to_parsed.domain
            # Normalize to_id so DB storage matches case-insensitive recv queries
            message.to_id = str(to_parsed)
        except MessageFormatError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid recipient ID", "details": str(exc)},
            )

        server.metrics.record_message_received()

        if to_domain == server.config.domain:
            # Local delivery
            await server.queue.enqueue(message, MessageStatus.DELIVERED)
            server.metrics.record_local_delivery()
        else:
            # Remote: queue for delivery manager
            await server.queue.enqueue(message, MessageStatus.QUEUED)
            server.metrics.record_forwarded()

        # 9. Accepted
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "nonce": message.nonce, "timestamp": int(time.time())},
        )

    except Exception as exc:
        logger.error(f"Unexpected error handling message: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


async def handle_recv(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/messages

    Retrieve delivered messages for an agent. Requires Credential authentication.
    The authenticated agent can only read their own messages.
    """
    server = request.app.state.server

    # Credential authentication required
    if hasattr(server, 'agent_store') and server.agent_store is not None:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Basic "):
            return JSONResponse(
                {"error": "Credential required"},
                status_code=401,
            )
        try:
            decoded = base64.b64decode(auth_header[6:]).decode()
            auth_agent_id, auth_password = decoded.split(":", 1)
        except Exception:
            return JSONResponse(
                {"error": "Invalid Authorization header"},
                status_code=401,
            )

        if not server.agent_store.verify(auth_agent_id, auth_password):
            return JSONResponse(
                {"error": "Credential verification failed"},
                status_code=401,
            )

        # Use authenticated agent_id (ignore query param to prevent reading others' messages)
        agent_id = auth_agent_id
    else:
        # No agent store configured, fall back to query param
        agent_id = request.query_params.get("agent_id")
        if not agent_id:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing required parameter: agent_id"},
            )

    limit_str = request.query_params.get("limit", "50")
    try:
        limit = int(limit_str)
    except ValueError:
        limit = 50

    after_id_str = request.query_params.get("after_id")
    after_id = None
    if after_id_str is not None:
        try:
            after_id = int(after_id_str)
        except ValueError:
            pass

    stored_messages = await server.queue.get_for_agent(agent_id, limit, after_id)

    messages = []
    last_id = None
    for sm in stored_messages:
        try:
            msg = ATPMessage.from_json(sm.message_json)
            messages.append(msg.to_dict())
            last_id = sm.id  # Track highest DB id for cursor pagination
        except Exception:
            pass

    result = {"messages": messages, "count": len(messages)}
    if last_id is not None:
        result["last_id"] = last_id
    return JSONResponse(content=result)


async def handle_capabilities(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/capabilities"""
    server = request.app.state.server
    return JSONResponse({
        "version": "1.0",
        "capabilities": ["message"],
        "protocols": ["atp/1"],
        "max_payload_size": server.config.max_message_size,
    })


async def handle_health(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/health"""
    server = request.app.state.server
    return JSONResponse({
        "status": "healthy",
        "domain": server.config.domain,
        "version": "1.0.0a1",
    })


async def handle_stats(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/stats
    Returns server metrics + queue status from DB. Requires admin auth.
    """
    denied = _check_admin_auth(request)
    if denied:
        return denied
    server = request.app.state.server
    metrics = server.metrics.to_dict()

    # Add queue counts from database
    store = server.queue._store
    # Query counts by status
    conn = sqlite3.connect(str(store._db_path))
    cursor = conn.execute(
        "SELECT status, COUNT(*) FROM messages GROUP BY status"
    )
    queue_counts = dict(cursor.fetchall())
    conn.close()

    metrics["queue"] = {
        "queued": queue_counts.get("queued", 0),
        "delivering": queue_counts.get("delivering", 0),
        "delivered": queue_counts.get("delivered", 0),
        "failed": queue_counts.get("failed", 0),
        "bounced": queue_counts.get("bounced", 0),
    }

    # Add agent list
    conn = sqlite3.connect(str(store._db_path))
    cursor = conn.execute(
        "SELECT DISTINCT to_id FROM messages WHERE status = 'delivered'"
    )
    metrics["agents"] = [row[0] for row in cursor.fetchall()]
    conn.close()

    metrics["domain"] = server.config.domain

    return JSONResponse(metrics)


async def handle_inspect(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/inspect?nonce=<nonce>
    Returns detailed status of a specific message. Requires admin auth.
    """
    denied = _check_admin_auth(request)
    if denied:
        return denied
    server = request.app.state.server
    nonce = request.query_params.get("nonce")
    if not nonce:
        return JSONResponse({"error": "Missing 'nonce' parameter"}, status_code=400)

    stored = server.queue._store.get_by_nonce(nonce)
    if not stored:
        return JSONResponse({"error": f"Message {nonce} not found"}, status_code=404)

    result = {
        "nonce": stored.nonce,
        "from": stored.from_id,
        "to": stored.to_id,
        "status": stored.status.value,
        "created_at": stored.created_at,
        "updated_at": stored.updated_at,
        "retry_count": stored.retry_count,
        "next_retry_at": stored.next_retry_at,
        "error": stored.error,
    }
    return JSONResponse(result)


async def handle_register(request: Request) -> JSONResponse:
    """POST /.well-known/atp/v1/register

    Register a new agent on this server.
    Body: {"agent_id": "bot@example.com", "password": "secret"}
    """
    server = request.app.state.server

    try:
        body = await request.body()
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    agent_id = data.get("agent_id")
    password = data.get("password")

    if not agent_id or not password:
        return JSONResponse({"error": "Missing agent_id or password"}, status_code=400)

    if not hasattr(server, "agent_store") or server.agent_store is None:
        return JSONResponse({"error": "Agent registration not available"}, status_code=503)

    # Validate agent_id format: if it contains '@', verify domain matches server
    # If no '@', auto-complete with server domain
    if "@" in agent_id:
        try:
            parsed = AgentID.parse(agent_id)
        except MessageFormatError as exc:
            return JSONResponse({"error": f"Invalid agent_id format: {exc}"}, status_code=400)
        if parsed.domain != server.config.domain:
            return JSONResponse(
                {"error": f"Cannot register agent for domain '{parsed.domain}' on server '{server.config.domain}'"},
                status_code=403,
            )
        local_part = parsed.local_part
        agent_id = str(parsed)  # Normalize to lowercase canonical form
    else:
        # Short-form: validate by constructing full ID and parsing it
        full_id = f"{agent_id}@{server.config.domain}"
        try:
            parsed = AgentID.parse(full_id)
        except MessageFormatError as exc:
            return JSONResponse({"error": f"Invalid agent_id format: {exc}"}, status_code=400)
        local_part = parsed.local_part
        agent_id = str(parsed)

    # Block reserved agent IDs
    if local_part in RESERVED_AGENT_IDS:
        return JSONResponse(
            {"error": f"Agent ID '{local_part}' is reserved and cannot be registered"},
            status_code=403,
        )

    try:
        record = server.agent_store.register(agent_id, password)
        logger.info(f"Agent registered: {agent_id}")
        return JSONResponse(
            {"status": "registered", "agent_id": agent_id},
            status_code=201,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=409)


async def handle_agents(request: Request) -> JSONResponse:
    """GET /.well-known/atp/v1/agents

    List registered agents. Requires admin auth.
    """
    denied = _check_admin_auth(request)
    if denied:
        return denied
    server = request.app.state.server

    if not hasattr(server, "agent_store") or server.agent_store is None:
        return JSONResponse({"agents": []})

    agents = server.agent_store.list_agents()
    return JSONResponse({"agents": [a.agent_id for a in agents]})


def get_routes() -> list[Route]:
    return [
        Route("/.well-known/atp/v1/message", handle_message, methods=["POST"]),
        Route("/.well-known/atp/v1/register", handle_register, methods=["POST"]),
        Route("/.well-known/atp/v1/agents", handle_agents, methods=["GET"]),
        Route("/.well-known/atp/v1/messages", handle_recv, methods=["GET"]),
        Route("/.well-known/atp/v1/capabilities", handle_capabilities, methods=["GET"]),
        Route("/.well-known/atp/v1/health", handle_health, methods=["GET"]),
        Route("/.well-known/atp/v1/stats", handle_stats, methods=["GET"]),
        Route("/.well-known/atp/v1/inspect", handle_inspect, methods=["GET"]),
    ]
