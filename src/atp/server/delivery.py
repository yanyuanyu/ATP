"""Delivery manager — background loop that transfers queued messages to remote servers."""

import asyncio
import logging
import random
import time

from atp.core.message import ATPMessage
from atp.core.identity import AgentID
from atp.core.signature import Signer
from atp.discovery.dns import BaseDNSResolver
from atp.security.atk import ATKRecord
from atp.security.sm2 import SM2PublicKey
from atp.security.sm4 import encrypt_payload, message_aad
from atp.security.transfer import make_transfer_proof
from atp.storage.messages import MessageStore, MessageStatus

logger = logging.getLogger("atp.server.delivery")


class DeliveryManager:
    def __init__(
        self,
        message_store: MessageStore,
        dns_resolver: BaseDNSResolver,
        transport,
        signer: Signer,
        server_domain: str,
        max_retries: int = 60,
        metrics=None,
        payload_encryption: bool = False,
        encryption_selector: str = "default",
    ):
        self._store = message_store
        self._resolver = dns_resolver
        self._transport = transport  # HTTPTransport (from client/transport.py)
        self._signer = signer
        self._domain = server_domain
        self._max_retries = max_retries
        self._metrics = metrics
        self._payload_encryption = payload_encryption
        self._encryption_selector = encryption_selector
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._store.recover_interrupted_deliveries()
        self._running = True
        self._task = asyncio.create_task(self._delivery_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _delivery_loop(self) -> None:
        """Loop: get pending messages, attempt transfer, handle results."""
        active = {}
        served = set()
        try:
            while self._running:
                for domain, task in list(active.items()):
                    if task.done():
                        del active[domain]
                        try:
                            task.result()
                        except Exception:
                            logger.exception("Delivery worker failed")
                try:
                    available = 4 - len(active)
                    pending = self._store.get_fair_pending(set(active) | served, available)
                    if available and not pending:
                        # Start another round only after other ready domains had
                        # a chance; old backlogs must not monopolize all slots.
                        served.clear()
                        pending = self._store.get_fair_pending(set(active), available)
                except Exception:
                    logger.exception("Cannot read delivery queue; retrying")
                    await asyncio.sleep(1)
                    continue
                for stored in pending:
                    domain = AgentID.parse(stored.to_id).domain
                    served.add(domain)
                    active[domain] = asyncio.create_task(self._deliver_one(stored))
                # Continuous admission, not a barrier after a batch of 20.
                await asyncio.sleep(0.1)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)

    async def _deliver_one(self, stored) -> None:
        """Deliver one message. On success mark DELIVERED. On failure mark retry or bounce."""
        message = ATPMessage.from_json(stored.message_json)
        self._store.update_status(stored.nonce, MessageStatus.DELIVERING)

        try:
            success = await asyncio.wait_for(self.transfer(message), timeout=8)
        except Exception:
            logger.warning("Delivery attempt failed for %s", stored.nonce, exc_info=True)
            success = False
        if success:
            self._store.update_status(stored.nonce, MessageStatus.DELIVERED)
            if self._metrics:
                self._metrics.record_delivery_success()
            logger.info(f"Delivered {stored.nonce} to {stored.to_id}")
        else:
            if stored.retry_count >= self._max_retries:
                self._store.update_status(stored.nonce, MessageStatus.BOUNCED, error="Max retries exceeded")
                if self._metrics:
                    self._metrics.record_bounced()
                await self._send_bounce(message, "Max retries exceeded")
                logger.warning(f"Bounced {stored.nonce}: max retries exceeded")
            else:
                if self._metrics:
                    self._metrics.record_delivery_failed()
                delay = self._next_retry_delay(stored.retry_count)
                self._store.mark_retry(stored.nonce, int(time.time()) + delay)
                logger.info(f"Retry {stored.nonce} in {delay}s (attempt {stored.retry_count + 1})")

    async def transfer(self, message: ATPMessage) -> bool:
        """Sign with domain key and transfer message to remote server.

        The server signs the message with its configured domain-level private
        key (SM2 by default) before sending. This ensures ATK is a domain-level mechanism:
        Server B verifies the signature against the sender domain's DNS
        public key, not against any individual agent's key.
        """
        try:
            # Immutable wire body makes lost-ACK retries safely identifiable.
            cached = self._store.get_wire(message.nonce)
            if cached is not None:
                message = cached
            target = AgentID.parse(message.to_id)
            server_info = await self._resolver.query_svcb(target.domain)
            if not server_info:
                logger.error(f"Cannot discover server for {target.domain}")
                return False

            # Protect the payload before ATK signing.  Intermediate ATP servers
            # can route the envelope, but only the recipient domain key can
            # unwrap the SM4 session key.
            if self._payload_encryption and not message.payload.get("_atp_encrypted"):
                key_name = f"{self._encryption_selector}.atk._atp.{target.domain}"
                txt_record = await self._resolver.query_txt(key_name)
                if not txt_record:
                    logger.error("Cannot encrypt for %s: ATK key not found", target.domain)
                    return False
                record = ATKRecord.parse(txt_record)
                if not record.is_valid():
                    logger.error("Cannot encrypt for %s: recipient ATK key is revoked or expired", target.domain)
                    return False
                if record.algorithm != "sm2":
                    logger.error(
                        "Cannot encrypt for %s: recipient key algorithm is %s",
                        target.domain,
                        record.algorithm,
                    )
                    return False
                public_key = record.get_public_key()
                if not isinstance(public_key, SM2PublicKey):
                    logger.error("Cannot encrypt for %s: recipient key is not SM2", target.domain)
                    return False
                message.payload = encrypt_payload(
                    message.payload,
                    recipient_public_key=public_key,
                    aad=message_aad(
                        from_id=message.from_id,
                        to_id=message.to_id,
                        timestamp=message.timestamp,
                        nonce=message.nonce,
                        message_type=message.type,
                    ),
                )

            # Sign the final envelope.  The ATK signature must cover the
            # ciphertext, otherwise a receiver would verify a different
            # payload after encryption.
            if cached is None:
                self._signer.sign(message)
                message = self._store.save_wire(message)

            base_url = f"https://{server_info.host}:{server_info.port}"
            result = await self._transport.post_message(
                base_url, message,
                transfer_proof=make_transfer_proof(message, self._signer),
            )
            return result.success
        except Exception as e:
            logger.error(f"Transfer failed for {message.nonce}: {e}")
            return False

    def _next_retry_delay(self, retry_count: int) -> int:
        """Bound recovery latency: 2/4/8 seconds, then 8–10 seconds with jitter.

        A retry keeps the original nonce and timestamp; replay protection and
        application receipt checks remain in force. Delivery is not unbounded.
        """
        base = (2, 4, 8)[min(max(retry_count, 0), 2)]
        return base + random.randint(0, 2)

    async def _send_bounce(self, original: ATPMessage, error: str) -> None:
        """Generate bounce notification.

        If the original sender is on this server, deliver locally (DELIVERED).
        Otherwise enqueue for remote transfer (QUEUED).
        """
        bounce = ATPMessage.create(
            from_id=f"postmaster@{self._domain}",
            to_id=original.from_id,
            payload={
                "subject": f"Delivery Failure: {original.nonce}",
                "body": f"Message to {original.to_id} could not be delivered. Error: {error}",
                "original_nonce": original.nonce,
            },
        )
        self._signer.sign(bounce)

        # Check if original sender is local — deliver directly instead of
        # re-queuing for transfer, which would fail (no credential) or loop.
        try:
            sender = AgentID.parse(original.from_id)
            if sender.domain == self._domain:
                self._store.enqueue(bounce, MessageStatus.DELIVERED)
                logger.info(f"Bounce for {original.nonce} delivered locally to {original.from_id}")
                return
        except Exception:
            pass

        self._store.enqueue(bounce, MessageStatus.QUEUED)
