"""
Scanner defence tests

Background: the SPA fallback answers every unknown path with index.html, so before ScanGuard a
probe for /.env got a 200 and the full page. The rules that fix that are pure pattern matching
against user-controlled input, which is exactly the kind of code that quietly grows a rule broad
enough to swallow a real route.
Design intent: the first test enumerates every route the app actually serves and asserts none of
them match, so widening a pattern fails the suite instead of locking users out of a page.
Key constraint: nothing here asserts on request volume or path variety. Blocking on those would
lock out the AI agent that is this service's main client, so the guard deliberately has no such
signal and the tests must not grow one.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import sys

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

from web.scan_guard import (  # noqa: E402
    BLOCK_LADDER_SECONDS,
    MAX_TRACKED_ADDRESSES,
    ScanGuard,
    ScanGuardMiddleware,
    match_malicious_path,
    normalize_path,
)

# Every path the app serves: SPA routes from web-ui/src/App.tsx, the mounts from create_app, and a
# representative spread of API endpoints. A rule that matches any of these is a rule that breaks
# the product.
REAL_PATHS = [
    "/", "/login", "/methodology", "/home", "/memories", "/new", "/edit/mem_123",
    "/view/mem_123", "/tags", "/import", "/explore", "/graph", "/profile", "/logs",
    "/usage", "/settings", "/admin", "/security", "/playground",
    "/assets/index-DzcI2Rqh.js", "/assets/index-ABC123.css",
    "/static/images/upload_1.png", "/favicon.ico", "/favicon.png",
    "/astermem-icon.png", "/apple-touch-icon.png", "/icons/icon-192.png",
    "/api/config", "/api/memories", "/api/memories/mem_123", "/api/search",
    "/api/agent/call", "/api/explore/stream", "/api/profile", "/api/usage",
    "/api/logs", "/api/export", "/api/import", "/api/vector-rebuild",
    "/api/skill/raw", "/api/methodology", "/api/auth/login", "/api/security",
]

KNOWN_PROBES = [
    "/.env", "/.env.local", "/.env.production", "/api/.env", "/laravel/.env",
    "/.git/config", "/.git/HEAD", "/.svn/entries",
    "/.aws/credentials", "/.ssh/id_rsa", "/.htaccess",
    "/index.php", "/wp-login.php", "/xmlrpc.php", "/shell.php", "/admin.aspx",
    "/wp-admin/", "/wp-content/plugins/x", "/wp-json", "/wordpress/",
    "/phpmyadmin/", "/pma/", "/adminer.php",
    "/drupal/", "/joomla/",
    "/kindeditor/php/upload_json.php", "/ueditor/net/controller.ashx",
    "/cgi-bin/luci", "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php",
    "/actuator/env", "/server-status",
    "/config.json", "/settings.yaml", "/backup.sql", "/database.zip", "/site.tar.gz",
]


@pytest.mark.parametrize("path", REAL_PATHS)
def test_real_routes_are_never_treated_as_probes(path):
    """The rules must not cost a user access to a page the app actually has."""
    assert match_malicious_path(path) == "", f"{path} would be refused to real users"


@pytest.mark.parametrize("path", KNOWN_PROBES)
def test_known_probes_are_recognised(path):
    assert match_malicious_path(path) != "", f"{path} was let through"


@pytest.mark.parametrize("path,expected", [
    ("//.env", "/.env"),
    ("/%2e%65nv", "/.env"),
    ("/foo//bar///baz", "/foo/bar/baz"),
    ("/wp-admin\\install.php", "/wp-admin/install.php"),
])
def test_normalisation_closes_the_obvious_evasions(path, expected):
    assert normalize_path(path) == expected


def test_encoded_probes_still_match():
    """Percent-encoding is the cheapest evasion, so it is normalised before matching."""
    assert match_malicious_path("/%2e%65nv") == "env_file"
    assert match_malicious_path("/..%2f.git/config") != ""


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _guard(clock=None, **kwargs):
    kwargs.setdefault("trusted_proxies", "127.0.0.0/8")
    kwargs.setdefault("owner_addresses", "")
    return ScanGuard(clock=clock or FakeClock(), **kwargs)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1", "10.0.0.5", "192.168.1.20", "172.16.3.4", "169.254.1.1",
])
def test_local_and_private_addresses_are_never_blocked(address):
    """A self-hosted box must not firewall out its own owner on the LAN."""
    guard = _guard()
    for _ in range(10):
        guard.record_strike(address)
    assert guard.is_blocked(address) is False


def test_configured_owner_address_is_exempt():
    guard = _guard(owner_addresses="45.198.224.26, 80.94.95.211")
    for _ in range(10):
        guard.record_strike("45.198.224.26")
    assert guard.is_blocked("45.198.224.26") is False


def test_a_single_probe_is_refused_but_earns_no_block():
    """One hit can be a stale bookmark or a link checker, so the address stays served."""
    guard = _guard()
    blocked, address, rule = guard.evaluate("/.env", "80.94.95.211", {})
    assert blocked is True
    assert rule == "env_file"
    assert address == "80.94.95.211"
    assert guard.is_blocked("80.94.95.211") is False


def test_a_sweep_stops_being_answered_at_all():
    """What makes the rest of a forty-request sweep cheap: the address is refused whatever it asks."""
    guard = _guard()
    for path in ("/.env", "/.git/config", "/wp-login.php"):
        guard.evaluate(path, "80.94.95.211", {})
    assert guard.is_blocked("80.94.95.211") is True

    blocked, _, rule = guard.evaluate("/api/memories", "80.94.95.211", {})
    assert blocked is True
    assert rule == "active_block"


def test_the_block_expires():
    """The ladder is bounded so a false positive does not become permanent."""
    clock = FakeClock()
    guard = _guard(clock=clock)
    for path in ("/.env", "/.git/config", "/wp-login.php"):
        guard.evaluate(path, "80.94.95.211", {})
    assert guard.is_blocked("80.94.95.211") is True

    clock.advance(BLOCK_LADDER_SECONDS[0] + 1)
    assert guard.is_blocked("80.94.95.211") is False


def test_probing_while_blocked_does_not_ratchet_the_ladder():
    """
    A blocked address is refused before any pattern matching, which is what makes the rest of its
    sweep free. The strike count therefore stays put until the block lapses.
    """
    clock = FakeClock()
    guard = _guard(clock=clock)
    for path in ("/.env", "/.git/config", "/wp-login.php"):
        guard.evaluate(path, "80.94.95.211", {})
    for _ in range(50):
        guard.evaluate("/phpmyadmin/", "80.94.95.211", {})

    clock.advance(BLOCK_LADDER_SECONDS[0] + 1)
    assert guard.is_blocked("80.94.95.211") is False


def test_a_returning_scanner_earns_a_longer_block():
    """Escalation advances once per return visit, so a persistent address is refused for longer."""
    clock = FakeClock()
    guard = _guard(clock=clock)
    for path in ("/.env", "/.git/config", "/wp-login.php"):
        guard.evaluate(path, "80.94.95.211", {})
    clock.advance(BLOCK_LADDER_SECONDS[0] + 1)

    guard.evaluate("/phpmyadmin/", "80.94.95.211", {})
    assert guard.is_blocked("80.94.95.211") is True
    clock.advance(BLOCK_LADDER_SECONDS[0] + 1)
    assert guard.is_blocked("80.94.95.211") is True, "second window should outlast the first"


def test_forwarded_header_is_read_only_from_a_trusted_proxy():
    guard = _guard()
    assert guard.client_address("127.0.0.1", {"x-forwarded-for": "80.94.95.211"}) == "80.94.95.211"
    # Direct caller, so its own claim about who it is carries no weight.
    assert guard.client_address("45.198.224.26", {"x-forwarded-for": "80.94.95.211"}) == "45.198.224.26"


def test_a_forged_forwarded_chain_cannot_get_a_third_party_blocked():
    """
    The scanner reaches nginx, which appends the real hop, so reading the chain from the right
    finds the scanner and not the address it named.
    """
    guard = _guard()
    headers = {"x-forwarded-for": "80.94.95.211, 45.198.224.26"}
    assert guard.client_address("127.0.0.1", headers) == "45.198.224.26"

    for path in ("/.env", "/.git/config", "/wp-login.php"):
        guard.evaluate(path, "127.0.0.1", headers)
    assert guard.is_blocked("45.198.224.26") is True
    assert guard.is_blocked("80.94.95.211") is False


def test_tracker_stays_bounded_under_forged_sources():
    """A sweep with random source addresses must not grow memory without limit."""
    guard = _guard()
    for index in range(MAX_TRACKED_ADDRESSES + 500):
        guard.record_strike(f"80.94.{index // 256 % 256}.{index % 256}")
    assert len(guard._offenders) <= MAX_TRACKED_ADDRESSES


@pytest.fixture
def guarded_client():
    """A catch-all app behind the middleware: anything that reaches a handler returns 200."""

    async def echo(request):
        return JSONResponse({"reached": True})

    app = Starlette(routes=[Route("/{full_path:path}", echo, methods=["GET", "POST"])])
    app.add_middleware(
        ScanGuardMiddleware,
        guard=_guard(trusted_proxies=""),
        reporter=None,
    )
    with TestClient(app, client=("80.94.95.211", 44444)) as client:
        yield client


def test_middleware_refuses_a_probe_with_an_empty_404(guarded_client):
    response = guarded_client.get("/.env")
    assert response.status_code == 404
    assert response.content == b""


def test_middleware_lets_real_requests_through(guarded_client):
    for path in ("/", "/home", "/api/memories", "/settings"):
        response = guarded_client.get(path)
        assert response.status_code == 200, path
        assert response.json() == {"reached": True}
