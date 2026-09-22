from unittest.mock import AsyncMock

from starlette.testclient import TestClient

from atp.server.app import ATPServer
from atp.server.config import RuntimeServerConfig
from atp.storage.config import ConfigStorage


def test_real_server_setup_and_lifespan(tmp_path, monkeypatch):
    monkeypatch.setattr('atp.server.app.ConfigStorage', lambda: ConfigStorage(tmp_path))
    server = ATPServer(RuntimeServerConfig(domain='test.local'))
    server._setup()
    server.delivery_manager.start = AsyncMock()
    server.delivery_manager.stop = AsyncMock()
    try:
        with TestClient(server.app) as client:
            assert client.get('/.well-known/atp/v1/health').status_code == 200
            assert server.signer.algorithm == 'sm2'
            server.delivery_manager.start.assert_awaited_once()
        server.delivery_manager.stop.assert_awaited_once()
    finally:
        # Release SQLite handles on Windows as well as Linux.
        server.queue._store._conn.close()
        server.agent_store._conn.close()
        server.replay_guard._conn.close()
