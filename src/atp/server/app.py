"""ATP server application — wires together all components and runs via uvicorn."""

import logging

import uvicorn
from starlette.applications import Starlette

from atp.server.config import RuntimeServerConfig
from atp.server.routes import get_routes
from atp.server.queue import MessageQueue
from atp.server.delivery import DeliveryManager
from atp.server.metrics import ServerMetrics
from atp.security.ats import ATSVerifier
from atp.security.atk import ATKVerifier
from atp.security.replay import ReplayGuard
from atp.security.tls import TLSConfig
from atp.discovery.dns import DNSResolver
from atp.discovery.local import LocalResolver, CompositeResolver
from atp.storage.agents import AgentStore
from atp.storage.config import ConfigStorage
from atp.storage.keys import KeyStorage
from atp.storage.messages import MessageStore
from atp.core.signature import Signer

logger = logging.getLogger("atp.server")


class ATPServer:
    def __init__(self, config: RuntimeServerConfig):
        self.config = config
        self.app: Starlette | None = None
        self.queue: MessageQueue | None = None
        self.ats_verifier: ATSVerifier | None = None
        self.atk_verifier: ATKVerifier | None = None
        self.replay_guard: ReplayGuard | None = None
        self.delivery_manager: DeliveryManager | None = None
        self.metrics: ServerMetrics | None = None
        self.signer: Signer | None = None
        self.agent_store: AgentStore | None = None
        self._config_storage: ConfigStorage | None = None

    def _setup(self) -> None:
        """Initialize all components."""
        logging.basicConfig(level=getattr(logging, self.config.log_level.upper(), logging.INFO))

        self._config_storage = ConfigStorage()
        self._config_storage.ensure_dirs()
        config_dir = self._config_storage.config_dir

        # Message store
        db_path = config_dir / "data" / "messages.db"
        message_store = MessageStore(db_path)
        message_store.init_db()
        self.queue = MessageQueue(message_store)

        # Agent credentials store
        self.agent_store = AgentStore(db_path)
        self.agent_store.init_db()

        # DNS resolver
        dns_resolver = DNSResolver()
        if self.config.local_mode:
            local = LocalResolver(
                peers_path=self.config.peers_file,
                dns_override_path=self.config.dns_override_file,
            )
            resolver = CompositeResolver(local, dns_resolver)
        else:
            resolver = dns_resolver

        # Security
        self.ats_verifier = ATSVerifier(resolver)
        self.atk_verifier = ATKVerifier(resolver)
        replay_db = config_dir / "data" / "nonces.db"
        self.replay_guard = ReplayGuard(
            max_age_seconds=self.config.replay_max_age,
            db_path=replay_db,
        )

        # Signer
        key_storage = KeyStorage(config_dir / "keys")
        if not key_storage.has_any_key_file(
            self.config.key_selector, self.config.key_algorithm
        ):
            logger.info(
                "No %s key found for selector '%s', generating...",
                self.config.key_algorithm,
                self.config.key_selector,
            )
            key_storage.generate(
                self.config.key_selector, self.config.key_algorithm
            )
        # A malformed key must fail startup instead of being silently replaced:
        # replacing it would leave DNS publishing the old public key.
        private_key, _public_key = key_storage.load_key_pair(
            self.config.key_selector, self.config.key_algorithm
        )
        self.signer = Signer(private_key, self.config.key_selector, self.config.domain)

        # Metrics
        self.metrics = ServerMetrics()

        # Transport (lazy import to avoid circular deps)
        from atp.client.transport import HTTPTransport

        transport = HTTPTransport(no_verify=self.config.local_mode)

        # Delivery manager
        self.delivery_manager = DeliveryManager(
            message_store=message_store,
            dns_resolver=resolver,
            transport=transport,
            signer=self.signer,
            server_domain=self.config.domain,
            max_retries=self.config.retry_max_attempts,
            metrics=self.metrics,
        )

        # Starlette app
        self.app = Starlette(
            routes=get_routes(),
            on_startup=[self._on_startup],
            on_shutdown=[self._on_shutdown],
        )
        self.app.state.server = self

    async def _on_startup(self) -> None:
        await self.delivery_manager.start()
        logger.info(f"ATP Server started: {self.config.domain} on port {self.config.port}")

    async def _on_shutdown(self) -> None:
        await self.delivery_manager.stop()
        logger.info("ATP Server stopped")

    def run(self) -> None:
        """Blocking entry point."""
        self._setup()

        kwargs: dict = {
            "host": self.config.host,
            "port": self.config.port,
            "log_level": self.config.log_level.lower(),
        }

        if self.config.tls_cert_path and self.config.tls_key_path:
            kwargs["ssl_certfile"] = self.config.tls_cert_path
            kwargs["ssl_keyfile"] = self.config.tls_key_path

        uvicorn.run(self.app, **kwargs)
