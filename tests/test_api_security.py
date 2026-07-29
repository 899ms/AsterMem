"""
Scanner defence reporting tests

Background: the guard refuses a probe before it reaches a handler, and once an address is blocked it
stops logging entirely so a sweep costs nothing. The side effect was that eighty-one refused
requests left eight log lines, readable only over SSH, and an owner could not tell a working guard
from one that had been switched off.
Design intent: the snapshot has to answer that question, which means the two populations it reports
must stay distinct — an address that is blocked, and an address that has merely been seen probing.
Key constraints:
  - The endpoint is owner-only. It names addresses and reveals the block threshold, both of which
    help a scanner time its next sweep.
  - The counter for refusals during a block is the whole point of the page: it is the traffic that
    leaves no other trace. A regression that folds it into the pattern counts would hide it again.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

from web.scan_guard import ScanGuard  # noqa: E402

SCANNER = "80.94.95.211"
OTHER_SCANNER = "45.198.224.26"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _guard(clock=None):
    return ScanGuard(trusted_proxies="127.0.0.0/8", owner_addresses="",
                     clock=clock or FakeClock())


def _probe(guard, address, path="/.env"):
    return guard.evaluate(path, address, {})


def test_endpoint_is_owner_only(anon_client):
    """Address lists and the block threshold are reconnaissance; they stay behind the session."""
    resp = anon_client.get("/api/security")
    assert resp.status_code == 401, resp.text


def test_reports_the_guard_as_installed(client):
    resp = client.get("/api/security")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    for field in ("blocked", "watching", "refused_total", "rule_hits", "block_after_strikes"):
        assert field in body, field


def test_a_clean_instance_reports_nothing_blocked(client):
    """The test client is exempt, so nothing it does can populate these lists."""
    body = client.get("/api/security").json()
    assert body["blocked"] == []
    assert isinstance(body["watching"], list)


def test_an_address_short_of_the_threshold_is_watched_not_blocked():
    """
    Reporting it as blocked would send the owner looking for a block that is not there — the
    address is still being served normally.
    """
    guard = _guard()
    _probe(guard, SCANNER)

    snapshot = guard.snapshot()
    assert snapshot["blocked"] == []
    assert snapshot["watching"] == [{"address": SCANNER, "strikes": 1}]


def test_a_blocked_address_reports_its_remaining_time():
    guard = _guard()
    for _ in range(3):
        _probe(guard, SCANNER)

    snapshot = guard.snapshot()
    assert snapshot["watching"] == []
    assert len(snapshot["blocked"]) == 1
    entry = snapshot["blocked"][0]
    assert entry["address"] == SCANNER
    assert entry["strikes"] == 3
    assert 0 < entry["blocked_for_seconds"] <= 900


def test_an_expired_block_moves_back_to_watching():
    """A lapsed block is not a block; the page must not keep showing it as one."""
    clock = FakeClock()
    guard = _guard(clock)
    for _ in range(3):
        _probe(guard, SCANNER)
    assert len(guard.snapshot()["blocked"]) == 1

    clock.advance(901)
    snapshot = guard.snapshot()
    assert snapshot["blocked"] == []
    assert snapshot["watching"] == [{"address": SCANNER, "strikes": 3}]


def test_silent_refusals_are_counted_separately_from_pattern_hits():
    """
    The requests refused during a block are the ones that leave no log line, so the count is the
    only evidence the guard is absorbing a sweep. It must not be mixed into the pattern totals.
    """
    guard = _guard()
    for _ in range(3):
        _probe(guard, SCANNER)
    pattern_hits_before = guard.snapshot()["rule_hits"]["env_file"]

    # Whatever it asks for now is refused without matching a rule.
    for path in ("/wp-admin/", "/index.php", "/api/memories"):
        blocked, _address, rule = _probe(guard, SCANNER, path)
        assert blocked is True
        assert rule == "active_block"

    snapshot = guard.snapshot()
    assert snapshot["rule_hits"]["active_block"] == 3
    assert snapshot["rule_hits"]["env_file"] == pattern_hits_before
    assert snapshot["refused_total"] == 6


def test_exempt_traffic_never_appears_in_the_report():
    """Loopback and private ranges are never blocked, so they must not be listed as suspects."""
    guard = _guard()
    for address in ("127.0.0.1", "192.168.1.20", "10.0.0.5"):
        _probe(guard, address)

    snapshot = guard.snapshot()
    assert snapshot["blocked"] == []
    assert snapshot["watching"] == []
    assert snapshot["tracked_addresses"] == 0


def test_clean_requests_are_not_counted_as_refusals():
    guard = _guard()
    blocked, _address, rule = _probe(guard, SCANNER, "/api/memories")
    assert blocked is False
    assert rule == ""

    snapshot = guard.snapshot()
    assert snapshot["refused_total"] == 0
    assert snapshot["rule_hits"] == {}


def test_offenders_are_reported_independently():
    guard = _guard()
    for _ in range(3):
        _probe(guard, SCANNER)
    _probe(guard, OTHER_SCANNER)

    snapshot = guard.snapshot()
    assert [row["address"] for row in snapshot["blocked"]] == [SCANNER]
    assert [row["address"] for row in snapshot["watching"]] == [OTHER_SCANNER]
    assert snapshot["tracked_addresses"] == 2


def test_disabled_guard_reports_off_rather_than_zeroes():
    """
    An instance that turned the guard off must not be shown counters: zeroes on a page titled
    "protection" read as calm rather than as absence.
    """
    import asyncio

    from web import api_security

    original_guard, original_enabled = api_security._guard, api_security._enabled
    try:
        api_security.init_security_api(None, False)
        body = asyncio.run(api_security.get_security_status(admin_id=1))
        assert body == {"enabled": False}
    finally:
        api_security.init_security_api(original_guard, original_enabled)
