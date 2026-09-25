"""Tests for ATP server modules: config, queue, routes, delivery."""

import base64
import json
import sqlite3
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient
from starlette.applications import Starlette

from atp.server.config import RuntimeServerConfig
from atp.server.routes import get_routes
from atp.server.queue import MessageQueue
from atp.server.delivery import DeliveryManager
from atp.server.metrics import ServerMetrics
from atp.core.message import ATPMessage
from atp.core.signature import Signer, VerifyResult
from atp.security.ats import ATSResult
from atp.security.replay import ReplayGuard
from atp.storage.agents import AgentStore
from atp.storage.config import ATPConfig, ServerConfig
from atp.storage.messages import MessageStore, MessageStatus
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _make_thread_safe_store(db_path) -> MessageStore:
    """Create a MessageStore with check_same_thread=False for TestClient compatibility.

    Starlette's TestClient runs the ASGI app in a separate thread, so the
    default SQLite same-thread check would fail.
    """
    store = MessageStore.__new__(MessageStore)
    store._db_path = db_path
    store._conn = sqlite3.connect(str(db_path), check_same_thread=False)
    store._conn.row_factory = sqlite3.Row
    store.init_db()
    return store


def _make_thread_safe_agent_store(db_path) -> AgentStore:
    """Create an AgentStore with check_same_thread=False for TestClient compatibility."""
    agent_store = AgentStore.__new__(AgentStore)
    agent_store._db_path = db_path
    agent_store._conn = sqlite3.connect(str(db_path), check_same_thread=False)
    agent_store.init_db()
    return agent_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def private_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def signer(private_key):
    return Signer(private_key, "default", "sender.com")


def _make_signed_message(signer, from_id="agent@sender.com", to_id="bot@test.local", payload=None):
    """Helper: create and sign a message."""
    if payload is None:
        payload = {"body": "hello"}
    msg = ATPMessage.create(from_id, to_id, payload)
    signer.sign(msg)
    return msg


@pytest.fixture
def mock_server(tmp_path):
    """Create a mock server object with all dependencies mocked."""
    server = MagicMock()
    server.config = RuntimeServerConfig(domain="test.local", port=7443, max_message_size=1_048_576, admin_token="test-admin-token")

    # ATS: always PASS
    server.ats_verifier = AsyncMock()
    server.ats_verifier.verify = AsyncMock(return_value=ATSResult(status="PASS"))

    # ATK: always PASS
    server.atk_verifier = AsyncMock()
    server.atk_verifier.verify = AsyncMock(return_value=VerifyResult(passed=True))

    # Replay: always fresh
    server.replay_guard = ReplayGuard()

    # Queue with real MessageStore (thread-safe for TestClient)
    db_path = tmp_path / "test.db"
    store = _make_thread_safe_store(db_path)
    server.queue = MessageQueue(store)

    # Agent credentials store (thread-safe)
    agent_store = _make_thread_safe_agent_store(db_path)
    agent_store.register("agent@test.local", "testpass")
    server.agent_store = agent_store

    # Metrics
    server.metrics = ServerMetrics()

    return server


@pytest.fixture
def client(mock_server):
    app = Starlette(routes=get_routes())
    app.state.server = mock_server
    return TestClient(app)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestRuntimeServerConfig:
    def test_defaults(self):
        cfg = RuntimeServerConfig(domain="example.com")
        assert cfg.domain == "example.com"
        assert cfg.port == 7443
        assert cfg.host == "0.0.0.0"
        assert cfg.local_mode is False
        assert cfg.max_message_size == 1_048_576
        assert cfg.key_algorithm == "sm2"
        assert cfg.transport_mode == "tls"
        assert cfg.tlcp_gateway_url == ""

    def test_from_cli_and_config_defaults(self):
        atp_config = ATPConfig(
            server=ServerConfig(
                domain="file.local",
                port=8443,
                admin_token="config-token",
                max_message_size=12345,
            ),
            local_mode=True,
            peers_file="peers.toml",
        )
        cfg = RuntimeServerConfig.from_cli_and_config({}, atp_config)
        assert cfg.domain == "file.local"
        assert cfg.port == 8443
        assert cfg.local_mode is True
        assert cfg.peers_file == "peers.toml"
        assert cfg.key_algorithm == "sm2"
        assert cfg.max_message_size == 12345
        assert cfg.admin_token == "config-token"

    def test_cli_overrides_config(self):
        atp_config = ATPConfig(
            server=ServerConfig(domain="file.local", port=8443),
        )
        cli_args = {
            "domain": "cli.local",
            "port": 9999,
            "local": False,
            "log_level": "DEBUG",
            "key_selector": "competition",
            "key_algorithm": "ed25519",
        }
        cfg = RuntimeServerConfig.from_cli_and_config(cli_args, atp_config)
        assert cfg.domain == "cli.local"
        assert cfg.port == 9999
        assert cfg.local_mode is False
        assert cfg.log_level == "DEBUG"
        assert cfg.key_selector == "competition"
        assert cfg.key_algorithm == "ed25519"

    def test_cli_none_values_do_not_override(self):
        atp_config = ATPConfig(
            server=ServerConfig(domain="file.local", port=8443),
        )
        cli_args = {"domain": None, "port": None}
        cfg = RuntimeServerConfig.from_cli_and_config(cli_args, atp_config)
        assert cfg.domain == "file.local"
        assert cfg.port == 8443


# ---------------------------------------------------------------------------
# Queue tests
# ---------------------------------------------------------------------------

class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_enqueue_and_get_for_agent(self, tmp_path):
        store = MessageStore(tmp_path / "q.db")
        store.init_db()
        queue = MessageQueue(store)

        msg = ATPMessage.create("a@sender.com", "b@recv.com", {"body": "test"})
        msg_id = await queue.enqueue(msg, MessageStatus.DELIVERED)
        assert msg_id > 0

        results = await queue.get_for_agent("b@recv.com")
        assert len(results) == 1
        assert results[0].nonce == msg.nonce

    @pytest.mark.asyncio
    async def test_get_pending(self, tmp_path):
        store = MessageStore(tmp_path / "q2.db")
        store.init_db()
        queue = MessageQueue(store)

        msg = ATPMessage.create("a@s.com", "b@r.com", {"x": 1})
        await queue.enqueue(msg, MessageStatus.QUEUED)

        pending = await queue.get_pending()
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestHandleMessage:
    def test_valid_message_accepted(self, client, signer):
        msg = _make_signed_message(signer)
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "nonce" in data

    def test_invalid_json_returns_400(self, client):
        resp = client.post("/.well-known/atp/v1/message", content="not json")
        assert resp.status_code == 400

    def test_missing_fields_returns_400(self, client):
        resp = client.post("/.well-known/atp/v1/message", content='{"from": "a@b.com"}')
        assert resp.status_code == 400

    def test_ats_fail_returns_403(self, client, mock_server, signer):
        mock_server.ats_verifier.verify = AsyncMock(
            return_value=ATSResult(status="FAIL", error_code="550 5.7.26")
        )
        msg = _make_signed_message(signer)
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 403
        assert "ATS" in resp.json()["error"]

    def test_atk_fail_returns_403(self, client, mock_server, signer):
        mock_server.atk_verifier.verify = AsyncMock(
            return_value=VerifyResult(passed=False, error_code="550 5.7.28", error_message="bad sig")
        )
        msg = _make_signed_message(signer)
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 403
        assert "ATK" in resp.json()["error"]

    def test_replay_returns_400(self, client, signer):
        msg = _make_signed_message(signer)
        body = msg.to_json()
        resp1 = client.post("/.well-known/atp/v1/message", content=body)
        assert resp1.status_code == 202
        # Same nonce again → replay
        resp2 = client.post("/.well-known/atp/v1/message", content=body)
        assert resp2.status_code == 400
        assert "Replay" in resp2.json()["error"]

    def test_local_delivery_marked_delivered(self, client, mock_server, signer):
        # to_id domain matches server domain "test.local"
        msg = _make_signed_message(signer, to_id="bot@test.local")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202

        # Verify it was stored as DELIVERED
        stored = mock_server.queue._store.get_by_nonce(msg.nonce)
        assert stored is not None
        assert stored.status == MessageStatus.DELIVERED

    def test_remote_delivery_marked_queued(self, client, mock_server, signer):
        # to_id domain does NOT match server domain
        msg = _make_signed_message(signer, to_id="bot@remote.com")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202

        stored = mock_server.queue._store.get_by_nonce(msg.nonce)
        assert stored is not None
        assert stored.status == MessageStatus.QUEUED

    def test_message_too_large_returns_400(self, client, mock_server, signer):
        mock_server.config.max_message_size = 10  # very small
        msg = _make_signed_message(signer)
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 400
        assert "too large" in resp.json()["error"].lower()

    def test_local_submission_without_credential_returns_401(self, client, mock_server, signer):
        """A local agent (from_id domain == server domain) must provide credentials."""
        msg = _make_signed_message(signer, from_id="agent@test.local", to_id="bot@test.local")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 401
        assert "Credential required" in resp.json()["error"]

    def test_local_submission_with_wrong_credential_returns_401(self, client, mock_server, signer):
        """A local agent with wrong password gets 401."""
        msg = _make_signed_message(signer, from_id="agent@test.local", to_id="bot@test.local")
        creds = base64.b64encode(b"agent@test.local:wrongpass").decode()
        resp = client.post(
            "/.well-known/atp/v1/message",
            content=msg.to_json(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 401
        assert "Credential verification failed" in resp.json()["error"]

    def test_local_submission_with_valid_credential_accepted(self, client, mock_server, signer):
        """A local agent with correct credentials is accepted."""
        msg = _make_signed_message(signer, from_id="agent@test.local", to_id="bot@test.local")
        creds = base64.b64encode(b"agent@test.local:testpass").decode()
        resp = client.post(
            "/.well-known/atp/v1/message",
            content=msg.to_json(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_remote_transfer_without_credential_still_works(self, client, mock_server, signer):
        """Messages from remote servers (different domain) don't need credentials."""
        msg = _make_signed_message(signer, from_id="agent@sender.com", to_id="bot@test.local")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"


class TestHandleRecv:
    def _auth_header(self, agent_id="agent@test.local", password="testpass"):
        creds = base64.b64encode(f"{agent_id}:{password}".encode()).decode()
        return {"Authorization": f"Basic {creds}"}

    def test_get_messages_with_credential(self, client, mock_server, signer):
        # Enqueue a delivered message for agent@test.local
        msg = _make_signed_message(signer, to_id="agent@test.local")
        mock_server.queue._store.enqueue(msg, MessageStatus.DELIVERED)

        resp = client.get(
            "/.well-known/atp/v1/messages",
            headers=self._auth_header(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["messages"][0]["nonce"] == msg.nonce

    def test_recv_without_credential_returns_401(self, client):
        resp = client.get("/.well-known/atp/v1/messages")
        assert resp.status_code == 401

    def test_recv_wrong_credential_returns_401(self, client):
        resp = client.get(
            "/.well-known/atp/v1/messages",
            headers=self._auth_header(password="wrongpass"),
        )
        assert resp.status_code == 401

    def test_after_id_parameter(self, client, mock_server, signer):
        msg1 = _make_signed_message(signer, to_id="agent@test.local")
        msg2 = _make_signed_message(signer, to_id="agent@test.local")
        id1 = mock_server.queue._store.enqueue(msg1, MessageStatus.DELIVERED)
        mock_server.queue._store.enqueue(msg2, MessageStatus.DELIVERED)

        resp = client.get(
            f"/.well-known/atp/v1/messages?after_id={id1}",
            headers=self._auth_header(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["messages"][0]["nonce"] == msg2.nonce


class TestCapabilities:
    def test_capabilities_response(self, client):
        resp = client.get("/.well-known/atp/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "1.0"
        assert "message" in data["capabilities"]
        assert "atp/1" in data["protocols"]
        assert "max_payload_size" in data


class TestHealth:
    def test_health_response(self, client):
        resp = client.get("/.well-known/atp/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["domain"] == "test.local"
        assert "version" in data


class TestRegister:
    def test_register_new_agent(self, client):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "newbot@test.local", "password": "newpass"},
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "registered"

    def test_register_duplicate_returns_409(self, client):
        # agent@test.local is already registered in the fixture
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "agent@test.local", "password": "anypass"},
        )
        assert resp.status_code == 409

    def test_register_missing_fields_returns_400(self, client):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "bot@test.local"},
        )
        assert resp.status_code == 400

    def test_register_invalid_json_returns_400(self, client):
        resp = client.post(
            "/.well-known/atp/v1/register",
            content="not json",
        )
        assert resp.status_code == 400


class TestAgentsList:
    def test_list_agents(self, client):
        resp = client.get("/.well-known/atp/v1/agents", headers={"Authorization": "Bearer test-admin-token"})
        assert resp.status_code == 200
        assert "agent@test.local" in resp.json()["agents"]

    def test_list_agents_no_auth(self, client):
        resp = client.get("/.well-known/atp/v1/agents")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Delivery manager tests
# ---------------------------------------------------------------------------

class TestDeliveryManager:
    def test_next_retry_delay(self):
        dm = DeliveryManager(
            message_store=MagicMock(),
            dns_resolver=MagicMock(),
            transport=MagicMock(),
            signer=MagicMock(),
            server_domain="test.local",
        )
        assert dm._next_retry_delay(0) == 60
        assert dm._next_retry_delay(1) == 300
        assert dm._next_retry_delay(2) == 1800
        assert dm._next_retry_delay(3) == 7200
        assert dm._next_retry_delay(4) == 28800
        assert dm._next_retry_delay(5) == 86400
        assert dm._next_retry_delay(99) == 86400  # clamp

    @pytest.mark.asyncio
    async def test_transfer_success(self):
        mock_resolver = AsyncMock()
        mock_resolver.query_svcb = AsyncMock(return_value=MagicMock(host="remote.com", port=7443))

        mock_transport = AsyncMock()
        mock_transport.post_message = AsyncMock(return_value=MagicMock(success=True))

        dm = DeliveryManager(
            message_store=MagicMock(),
            dns_resolver=mock_resolver,
            transport=mock_transport,
            signer=MagicMock(),
            server_domain="test.local",
        )

        msg = ATPMessage.create("a@test.local", "b@remote.com", {"x": 1})
        result = await dm.transfer(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_transfer_no_server_info(self):
        mock_resolver = AsyncMock()
        mock_resolver.query_svcb = AsyncMock(return_value=None)

        dm = DeliveryManager(
            message_store=MagicMock(),
            dns_resolver=mock_resolver,
            transport=MagicMock(),
            signer=MagicMock(),
            server_domain="test.local",
        )

        msg = ATPMessage.create("a@test.local", "b@unknown.com", {"x": 1})
        result = await dm.transfer(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_deliver_one_success(self, tmp_path):
        store = MessageStore(tmp_path / "del.db")
        store.init_db()

        msg = ATPMessage.create("a@test.local", "b@remote.com", {"x": 1})
        store.enqueue(msg, MessageStatus.QUEUED)
        stored = store.get_by_nonce(msg.nonce)

        mock_resolver = AsyncMock()
        mock_resolver.query_svcb = AsyncMock(return_value=MagicMock())
        mock_transport = AsyncMock()
        mock_transport.post_message = AsyncMock(return_value=MagicMock(success=True))

        dm = DeliveryManager(
            message_store=store,
            dns_resolver=mock_resolver,
            transport=mock_transport,
            signer=MagicMock(),
            server_domain="test.local",
        )

        await dm._deliver_one(stored)
        updated = store.get_by_nonce(msg.nonce)
        assert updated.status == MessageStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_deliver_one_failure_retries(self, tmp_path):
        store = MessageStore(tmp_path / "del2.db")
        store.init_db()

        msg = ATPMessage.create("a@test.local", "b@remote.com", {"x": 1})
        store.enqueue(msg, MessageStatus.QUEUED)
        stored = store.get_by_nonce(msg.nonce)

        mock_resolver = AsyncMock()
        mock_resolver.query_svcb = AsyncMock(return_value=None)

        dm = DeliveryManager(
            message_store=store,
            dns_resolver=mock_resolver,
            transport=MagicMock(),
            signer=MagicMock(),
            server_domain="test.local",
            max_retries=6,
        )

        await dm._deliver_one(stored)
        updated = store.get_by_nonce(msg.nonce)
        assert updated.status == MessageStatus.FAILED  # marked for retry
        assert updated.retry_count == 1

    @pytest.mark.asyncio
    async def test_deliver_one_max_retries_bounces(self, tmp_path, private_key):
        store = MessageStore(tmp_path / "del3.db")
        store.init_db()

        msg = ATPMessage.create("a@sender.com", "b@remote.com", {"x": 1})
        store.enqueue(msg, MessageStatus.QUEUED)

        # Simulate max retries reached by updating retry_count
        for _ in range(6):
            store.mark_retry(msg.nonce, int(time.time()) - 1)

        stored = store.get_by_nonce(msg.nonce)
        assert stored.retry_count >= 6

        mock_resolver = AsyncMock()
        mock_resolver.query_svcb = AsyncMock(return_value=None)

        real_signer = Signer(private_key, "default", "test.local")

        dm = DeliveryManager(
            message_store=store,
            dns_resolver=mock_resolver,
            transport=MagicMock(),
            signer=real_signer,
            server_domain="test.local",
            max_retries=6,
        )

        await dm._deliver_one(stored)
        updated = store.get_by_nonce(msg.nonce)
        assert updated.status == MessageStatus.BOUNCED


# ---------------------------------------------------------------------------
# Stats / Inspect route tests
# ---------------------------------------------------------------------------

class TestHandleStats:
    def test_stats_endpoint(self, client, mock_server, signer):
        # Post a valid message first
        msg = _make_signed_message(signer, to_id="bot@test.local")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202

        # Now get stats (with admin token)
        resp = client.get("/.well-known/atp/v1/stats", headers={"Authorization": "Bearer test-admin-token"})
        assert resp.status_code == 200
        data = resp.json()

        # Verify all expected top-level keys
        assert "messages" in data
        assert "security" in data
        assert "queue" in data
        assert "domain" in data
        assert "agents" in data
        assert "uptime" in data

        # Domain should match server config
        assert data["domain"] == "test.local"

        # Messages should show at least 1 received
        assert data["messages"]["received"] >= 1

        # Security should have counts
        assert data["security"]["ats_pass"] >= 1
        assert data["security"]["atk_pass"] >= 1

        # Queue should have delivered count
        assert data["queue"]["delivered"] >= 1

        # Agents should include the recipient
        assert "bot@test.local" in data["agents"]


class TestHandleInspect:
    def test_inspect_found(self, client, mock_server, signer):
        # Post a message first
        msg = _make_signed_message(signer, to_id="bot@test.local")
        resp = client.post("/.well-known/atp/v1/message", content=msg.to_json())
        assert resp.status_code == 202

        # Inspect by nonce (with admin token)
        resp = client.get(f"/.well-known/atp/v1/inspect?nonce={msg.nonce}", headers={"Authorization": "Bearer test-admin-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["nonce"] == msg.nonce
        assert data["from"] == msg.from_id
        assert data["to"] == msg.to_id
        assert data["status"] == "delivered"

    def test_inspect_not_found(self, client):
        resp = client.get("/.well-known/atp/v1/inspect?nonce=nonexistent", headers={"Authorization": "Bearer test-admin-token"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    def test_inspect_missing_param(self, client):
        resp = client.get("/.well-known/atp/v1/inspect", headers={"Authorization": "Bearer test-admin-token"})
        assert resp.status_code == 400
        assert "nonce" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# Security regression tests
# ---------------------------------------------------------------------------

class TestIdentityBinding:
    """P0 fix: local agent cannot impersonate another local agent."""

    def test_sender_mismatch_rejected(self, client, mock_server, signer):
        """Authenticated as agent@test.local but from=other@test.local → 403."""
        # Register "agent" (already done by fixture) and "other"
        mock_server.agent_store.register("other@test.local", "otherpass")

        msg = _make_signed_message(signer, from_id="other@test.local", to_id="bot@test.local")
        # Authenticate as agent@test.local (not other@test.local)
        import base64
        creds = base64.b64encode(b"agent@test.local:testpass").decode()
        resp = client.post(
            "/.well-known/atp/v1/message",
            content=msg.to_json(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 403
        assert "mismatch" in resp.json()["error"].lower()

    def test_case_insensitive_match(self, client, mock_server, signer):
        """Agent@Test.Local and agent@test.local should be treated as same identity."""
        msg = _make_signed_message(signer, from_id="agent@test.local", to_id="bot@test.local")
        # Auth with mixed case — should still pass since AgentID normalizes
        import base64
        creds = base64.b64encode(b"agent@test.local:testpass").decode()
        resp = client.post(
            "/.well-known/atp/v1/message",
            content=msg.to_json(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 202


class TestRegistrationValidation:
    """P1 fix: short-form registration validates local_part format."""

    def test_short_form_valid(self, client, mock_server):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "validbot", "password": "pass123"},
        )
        assert resp.status_code == 201
        assert resp.json()["agent_id"] == "validbot@test.local"

    def test_short_form_invalid_chars(self, client, mock_server):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "bad name", "password": "pass123"},
        )
        assert resp.status_code == 400

    def test_short_form_slash(self, client, mock_server):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "foo/bar", "password": "pass123"},
        )
        assert resp.status_code == 400

    def test_reserved_name_blocked(self, client, mock_server):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "admin", "password": "pass123"},
        )
        assert resp.status_code == 403
        assert "reserved" in resp.json()["error"].lower()

    def test_wrong_domain_blocked(self, client, mock_server):
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "evil@other.com", "password": "pass123"},
        )
        assert resp.status_code == 403

    def test_full_form_case_normalized(self, client, mock_server):
        """Full-form agent_id with mixed case should be normalized before storage."""
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "CaseBot@Test.Local", "password": "pass123"},
        )
        assert resp.status_code == 201
        # Should be stored as normalized lowercase
        assert resp.json()["agent_id"] == "casebot@test.local"

    def test_case_variant_duplicate_rejected(self, client, mock_server):
        """Registering a case variant of an existing agent should fail (duplicate)."""
        # "agent@test.local" is already registered by the fixture
        resp = client.post(
            "/.well-known/atp/v1/register",
            json={"agent_id": "Agent@Test.Local", "password": "different"},
        )
        assert resp.status_code == 409


class TestReplayPruning:
    """P2 fix: nonce DB is pruned periodically, not just on startup."""

    def test_prune_removes_expired(self, tmp_path):
        db = tmp_path / "nonces.db"
        guard = ReplayGuard(max_age_seconds=10, max_cache_size=100_000, db_path=db, _prune_interval=5)
        import sqlite3

        now = int(time.time())

        # Insert 6 nonces (triggers prune at 5th insert)
        for i in range(6):
            guard.check(f"n{i}", now)

        # Manually insert an expired nonce directly into DB
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO nonces (nonce, timestamp) VALUES (?, ?)", ("old", now - 100))
        conn.commit()

        # Verify expired nonce exists
        count = conn.execute("SELECT COUNT(*) FROM nonces WHERE nonce='old'").fetchone()[0]
        assert count == 1

        # Trigger another batch to cause prune
        for i in range(6, 12):
            guard.check(f"n{i}", now)

        # After prune, the expired nonce should be gone
        count = conn.execute("SELECT COUNT(*) FROM nonces WHERE nonce='old'").fetchone()[0]
        assert count == 0
        conn.close()
