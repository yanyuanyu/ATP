"""Unit tests for the Docker demo's packet-header classifier."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PROBE_PATH = Path(__file__).parents[1] / "demo" / "docker" / "demo" / "packet_probe.py"
SPEC = importlib.util.spec_from_file_location("demo_packet_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
packet_probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet_probe)


def test_tlcp_gateways_are_cross_domain() -> None:
    assert packet_probe.packet_scope("172.28.0.11", "172.28.0.12") == "cross_domain"
    assert packet_probe.node_for("172.28.0.13", "172.28.0.11") == "tlcp-payment"
    assert packet_probe.node_for("172.28.1.254", "172.28.1.1") == "travel@family.test"
    assert packet_probe.packet_scope("172.28.1.2", "172.28.2.2") == "cross_domain"
    assert packet_probe.packet_scope("172.28.3.2", "172.28.1.2") == "cross_domain"


def test_local_gateway_backend_link_is_not_cross_domain() -> None:
    assert packet_probe.packet_scope("172.28.1.2", "172.28.1.1") == "edge_agent"


def test_tlcp_handshake_and_application_records_are_recognized() -> None:
    assert packet_probe.classify_secure_record(b"\x16\x01\x01\x00\x00") == (
        "tls_handshake",
        "TLCPv1.1",
        "1.1",
    )
    assert packet_probe.classify_secure_record(b"\x17\x01\x01\x00\x00") == (
        "tls_appdata",
        "TLCPv1.1",
        "1.1",
    )


def test_unknown_secure_record_version_is_rejected() -> None:
    assert packet_probe.classify_secure_record(b"\x16\x02\x00\x00\x00") is None


def test_protocol_dns_queries_accept_each_domain_bind_address() -> None:
    assert packet_probe.is_protocol_dns_query("172.28.1.1", "172.28.1.10", 53)
    assert packet_probe.is_protocol_dns_query("172.28.2.1", "172.28.2.10", 53)
    assert packet_probe.is_protocol_dns_query("172.28.3.1", "172.28.3.10", 53)


def test_protocol_dns_queries_reject_stub_and_unrelated_traffic() -> None:
    assert not packet_probe.is_protocol_dns_query("172.28.1.1", "127.0.0.11", 53)
    assert not packet_probe.is_protocol_dns_query("172.28.2.101", "172.28.2.10", 53)
    assert not packet_probe.is_protocol_dns_query("172.28.1.1", "172.28.1.10", 853)
