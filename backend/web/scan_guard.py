"""
Scanner defence for instances that answer public traffic.

Background: the SPA fallback serves index.html for every unknown GET, so probes for /.env,
/.git/config and /wp-admin were all answered with 200 and the full page. Scanners read a 200 as
a live target and escalate to heavier sweeps, and the instance advertises files it does not have.
Design intent: match only paths this service can never legitimately serve, answer them with an
empty 404, and stop answering an address that keeps probing so a forty-request sweep costs two
responses instead of forty page renders.
Key constraints:
  - Request volume and path variety are never used as signals. The normal workload here is an AI
    agent calling the API in bursts across many memory ids, which is indistinguishable from a
    scan by those measures; blocking on them would lock out the one client that matters.
  - Loopback, private ranges and the reverse proxy are never blocked. A self-hosted box that
    firewalls its own owner out of the settings page is worse than the scanning it prevents.
  - Forwarded headers are honoured only from trusted proxy addresses, otherwise a scanner sets
    X-Forwarded-For and gets an arbitrary third party blocked.
"""

import ipaddress
import os
import re
import time
from collections import Counter, OrderedDict
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Paths no AsterMem deployment serves. Kept deliberately narrow: every entry is a probe for
# software this project does not use, so a match is a scanner rather than a mistyped URL.
# Real routes (/admin, /login, /explore, /profile, /settings ...) and /api/*, /assets/*,
# /static/images/* must never match, so no rule keys off a bare generic word.
MALICIOUS_PATH_RULES = (
    ("env_file", re.compile(r"(^|/)\.env(?:[./-]|$)", re.I)),
    ("vcs_metadata", re.compile(r"(^|/)\.(?:git|svn|hg|bzr)(?:/|$)", re.I)),
    ("credential_store", re.compile(r"(^|/)\.(?:aws|ssh|npmrc|netrc)(?:/|$)", re.I)),
    ("htaccess_probe", re.compile(r"(^|/)\.ht(?:access|passwd)(?:[./]|$)", re.I)),
    ("script_file_probe", re.compile(r"(^|/)[^/]*\.(?:php\d*|phtml|phar|asp|aspx|jsp|jspx|cgi|shtml)(?:/|$)", re.I)),
    ("wordpress_probe", re.compile(r"(^|/)(?:wp-admin|wp-includes|wp-content|wp-json|wordpress)(?:/|$)", re.I)),
    ("db_admin_probe", re.compile(r"(^|/)(?:phpmyadmin|phpMyAdmin|myadmin|pma|adminer|dbadmin)(?:/|$)", re.I)),
    ("cms_probe", re.compile(r"(^|/)(?:drupal|joomla|magento|typo3)(?:/|$)", re.I)),
    ("editor_probe", re.compile(r"(^|/)(?:kindeditor|ueditor|ckfinder|kcfinder|fckeditor|webuploader)(?:/|$)", re.I)),
    ("cgi_probe", re.compile(r"(^|/)cgi-bin(?:/|$)", re.I)),
    ("dependency_probe", re.compile(r"(^|/)vendor/(?:phpunit|composer)(?:/|$)", re.I)),
    ("actuator_probe", re.compile(r"(^|/)actuator(?:/(?:env|heapdump|configprops|beans))?(?:/|$)", re.I)),
    ("server_info_probe", re.compile(r"(^|/)(?:server-status|server-info)(?:/|$)", re.I)),
    ("shell_probe", re.compile(r"(^|/)(?:shell|backdoor|webshell|eval-stdin)(?:[./]|$)", re.I)),
    ("config_dump_probe", re.compile(r"(^|/)(?:config|configuration|settings)\.(?:json|ya?ml|ini|bak|old|txt)(?:$)", re.I)),
    ("archive_probe", re.compile(r"(^|/)[^/]*\.(?:sql|sql\.gz|tar|tar\.gz|tgz|zip|rar|bak|swp)(?:$)", re.I)),
)

# A single probe can be an old bookmark or a link checker, so one match only counts. Blocking
# starts once an address has proven a pattern, and lengthens while it keeps going.
BLOCK_AFTER_STRIKES = 3
BLOCK_LADDER_SECONDS = (900, 3600, 21600, 86400)

# Bounded so a sweep with forged source addresses cannot grow the tracker without limit.
MAX_TRACKED_ADDRESSES = 4096

_DEFAULT_TRUSTED_PROXIES = "127.0.0.0/8,::1/128"


def _parse_networks(raw: str) -> tuple:
    networks = []
    for value in (raw or "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _parse_address(value: str):
    value = (value or "").strip()
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1 and "." in value:
        value = value.split(":")[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def normalize_path(path: str) -> str:
    """Decode and collapse a path so %2e%2e and doubled slashes cannot slip a probe past the rules."""
    normalized = path or "/"
    for _ in range(2):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    normalized = normalized.replace("\\", "/")
    normalized = re.sub(r"/{2,}", "/", normalized)
    return normalized[:2048]


def match_malicious_path(path: str) -> str:
    """Return the name of the first rule the path trips, or an empty string when it is clean."""
    normalized = normalize_path(path)
    for name, pattern in MALICIOUS_PATH_RULES:
        if pattern.search(normalized):
            return name
    return ""


class ScanGuard:
    """
    Tracks which addresses probe for software this service does not run.

    Kept as an object rather than module state so tests can drive one instance with an injected
    clock, and so a restart starts from a clean slate: bans are a load-shedding measure, not a
    security boundary, and a scanner that returns after a restart simply earns them again.
    """

    def __init__(self, trusted_proxies: str = None, owner_addresses: str = None,
                 block_after_strikes: int = BLOCK_AFTER_STRIKES, clock=time.monotonic):
        self._trusted = _parse_networks(
            trusted_proxies if trusted_proxies is not None
            else os.environ.get("ASTERMEM_TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
        )
        self._owners = frozenset(
            item.strip() for item in
            (owner_addresses if owner_addresses is not None
             else os.environ.get("ASTERMEM_ALLOWED_IPS", "")).split(",")
            if item.strip()
        )
        self._block_after_strikes = max(1, block_after_strikes)
        self._clock = clock
        # address -> [strike count, blocked-until timestamp]
        self._offenders = OrderedDict()
        # Counted for the security page only. Refusals during a block are deliberately not logged,
        # so without a tally the owner sees three log lines for a forty-request sweep and cannot
        # tell whether the guard is working.
        self._rule_hits = Counter()
        self._refused_total = 0

    def client_address(self, peer: str, headers) -> str:
        """
        Resolve the caller, trusting forwarded headers only when the direct peer is a known proxy.

        Walks the forwarded chain from the right so proxies we control are stripped and the first
        address outside them is used; a scanner can prepend anything it likes to that header but
        cannot make its own hop disappear.
        """
        parsed_peer = _parse_address(peer)
        if parsed_peer is None:
            return ""
        if not self._is_trusted_proxy(parsed_peer):
            return str(parsed_peer)

        forwarded = headers.get("x-forwarded-for", "") if headers else ""
        chain = [item for item in (_parse_address(part) for part in forwarded.split(",")) if item]
        if chain:
            for candidate in reversed(chain):
                if not self._is_trusted_proxy(candidate):
                    return str(candidate)
            return str(chain[0])

        real_ip = _parse_address(headers.get("x-real-ip", "") if headers else "")
        return str(real_ip or parsed_peer)

    def _is_trusted_proxy(self, address) -> bool:
        return any(address in network for network in self._trusted)

    def is_exempt(self, address: str) -> bool:
        """Loopback, link-local, private ranges, trusted proxies and configured owners are never blocked."""
        if not address or address in self._owners:
            return True
        parsed = _parse_address(address)
        if parsed is None:
            return True
        return bool(
            parsed.is_loopback or parsed.is_link_local or parsed.is_private
            or self._is_trusted_proxy(parsed)
        )

    def is_blocked(self, address: str) -> bool:
        if self.is_exempt(address):
            return False
        record = self._offenders.get(address)
        if record is None:
            return False
        if record[1] <= self._clock():
            return False
        self._offenders.move_to_end(address)
        return True

    def record_strike(self, address: str) -> int:
        """
        Count a probe and extend the block window. Returns the running strike count.

        The window grows along a fixed ladder rather than unboundedly: a permanent ban buys nothing
        against addresses that rotate anyway, and it makes a false positive permanent too.
        """
        if self.is_exempt(address):
            return 0
        record = self._offenders.get(address)
        strikes = (record[0] if record else 0) + 1
        blocked_until = 0.0
        if strikes >= self._block_after_strikes:
            step = min(strikes - self._block_after_strikes, len(BLOCK_LADDER_SECONDS) - 1)
            blocked_until = self._clock() + BLOCK_LADDER_SECONDS[step]
        self._offenders[address] = [strikes, blocked_until]
        self._offenders.move_to_end(address)
        while len(self._offenders) > MAX_TRACKED_ADDRESSES:
            self._offenders.popitem(last=False)
        return strikes

    def release(self, address: str) -> bool:
        """
        Drop an address from the tracker, ending its block and resetting its strike count.

        For the case the ladder cannot tell apart: a client that belongs here tripped a rule and is
        now locked out, and the only other way back is restarting the service.

        Releasing is not an exemption. The address is judged from scratch, so one that goes on
        probing earns the block again; ASTERMEM_ALLOWED_IPS is the way to say "never block this".
        """
        return self._offenders.pop(address, None) is not None

    def evaluate(self, path: str, peer: str, headers) -> tuple:
        """
        Decide whether to answer a request.

        Returns (should_block, address, rule). A blocked address is refused whatever it asks for,
        which is what makes the rest of a sweep cheap; a clean address is only refused on the
        request that trips a rule.

        Refusing before matching also means probes sent during a block do not count, so the ladder
        advances once per return visit rather than once per request. That is the intent: a single
        sweep should not be able to talk itself into a day-long block.
        """
        address = self.client_address(peer, headers)
        if self.is_blocked(address):
            self._refused_total += 1
            self._rule_hits["active_block"] += 1
            return True, address, "active_block"
        rule = match_malicious_path(path)
        if not rule:
            return False, address, ""
        self.record_strike(address)
        self._refused_total += 1
        self._rule_hits[rule] += 1
        return True, address, rule

    def snapshot(self) -> dict:
        """
        Read-only view for the security page.

        Reports blocked and merely-suspected addresses separately: an address one strike short of
        the threshold is still being served normally, and presenting it as blocked would send the
        owner chasing a block that does not exist.

        Counts are per process. Blocks live in memory by design (see the class docstring), so there
        is no history to report and the page has to say as much rather than imply a lifetime total.
        """
        now = self._clock()
        blocked = []
        watching = []
        for address, (strikes, blocked_until) in self._offenders.items():
            remaining = int(blocked_until - now)
            if remaining > 0:
                blocked.append({
                    "address": address,
                    "strikes": strikes,
                    "blocked_for_seconds": remaining,
                })
            else:
                watching.append({"address": address, "strikes": strikes})
        blocked.sort(key=lambda item: item["blocked_for_seconds"], reverse=True)
        watching.sort(key=lambda item: item["strikes"], reverse=True)
        return {
            "blocked": blocked,
            "watching": watching,
            "tracked_addresses": len(self._offenders),
            "refused_total": self._refused_total,
            # "active_block" is kept in here on purpose: the gap between it and the named rules is
            # what the guard actually saves, since those requests never reach a handler or a log.
            "rule_hits": dict(sorted(self._rule_hits.items(), key=lambda kv: kv[1], reverse=True)),
            "block_after_strikes": self._block_after_strikes,
            "block_ladder_seconds": list(BLOCK_LADDER_SECONDS),
            "max_tracked_addresses": MAX_TRACKED_ADDRESSES,
            "trusted_proxies": [str(network) for network in self._trusted],
            "owner_addresses": sorted(self._owners),
        }


class ScanGuardMiddleware(BaseHTTPMiddleware):
    """
    Refuses probes for software this service does not run.

    Design intent: sits outermost so a refusal costs nothing further down the stack, and answers
    with a bare 404 because that is the response that makes a scanner lose interest.
    Key constraint: only the request that trips a rule is reported. Once an address is blocked its
    refusals are silent, otherwise the sweep this exists to absorb would still write one log line
    per probe.
    """

    def __init__(self, app, guard=None, reporter=print):
        super().__init__(app)
        self._guard = guard if guard is not None else ScanGuard()
        self._reporter = reporter

    async def dispatch(self, request, call_next):
        peer = request.client.host if request.client else ""
        blocked, address, rule = self._guard.evaluate(request.url.path, peer, request.headers)
        if blocked:
            if rule != "active_block" and self._reporter is not None:
                self._reporter(f"Blocked probe from {address}: {rule} {request.url.path[:120]}")
            return Response(status_code=404)
        return await call_next(request)
