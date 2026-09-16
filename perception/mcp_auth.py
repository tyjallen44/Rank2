"""Who is calling the MCP endpoint — token verification, nothing else.

Two kinds of bearer token are accepted, and the caller ends up as a
``PulseIdentity`` either way:

1. **An RLDatix OAuth token** (RS256 JWT) minted by RLDatix's authorization
   server for one named person, verified here against that server's public
   JWKS. Its ``email`` claim is looked up in Pulse's own ``users`` table, which
   must hold an active row — so an RLDatix token alone is not access to Pulse,
   it is only proof of who is asking. Its ``role`` claim is mapped onto a Pulse
   role group (see ``_DEFAULT_ROLE_MAP``).
2. **A Pulse session token** minted by ``server._create_token`` — the token a
   browser already holds — but only the identity-bearing ones. Tokens minted
   from the five shared team passwords carry no email (``server.py``'s
   ``/api/auth/login``) and are refused: a caller with no identity cannot be
   scoped to its own runs and cannot be metered by the daily cap, which are the
   two things the MCP is built around. This path checks the ``users`` row too:
   a 30-day session token outlives a deactivation by up to a month, and the
   docstring above would otherwise be true of only one of the two paths.

TWO ROLES, AND WHY. ``PulseIdentity`` carries both:

* ``role`` is the MAPPED group, and it is the ACCESS GATE only — it answers
  "may an RLDatix caller with this role reach Pulse at all", and nothing else.
* ``account_role`` is the role on the caller's own Pulse ``users`` row — what
  their own browser login is minted with at ``server.py``'s Google and native
  sign-in. Everything that GRANTS is stamped with ``account_role``: the run's
  ``analysis_runs.user_role`` and the short-lived download token the report
  tools mint. Those two must agree (``download_pdf`` looks a run up through
  ``query_history(role)``, which filters on ``user_role``), and neither may
  exceed what the person already holds in Pulse. Using the mapped group for
  either would hand a Google-approved account whose Pulse role is ``user`` a
  working ``salesteam`` session credential.

THE RULES THAT MATTER IN ``JwtVerifier``, and why each is written this way:

1. **RS256 only, allowlisted twice.** The classic JWT break is re-signing a
   token with ``alg: none`` (no signature) or with ``alg: HS256`` using the RSA
   PUBLIC key — which is public — as the HMAC secret. A verifier that reads the
   algorithm out of the token's own header and trusts it accepts both. So the
   algorithm is checked against ``ALGORITHMS`` BEFORE any key is looked up, and
   passed to ``jwt.decode`` as an explicit allowlist as well: two independent
   refusals, because one of them being deleted in a refactor must not be enough.
2. **Registered claims are required.** Without ``options={"require": [...]}`` a
   token carrying no ``exp`` at all validates forever.
3. **The JWKS cache cannot become a fetch amplifier.** An unknown ``kid`` is
   what legitimate key rotation looks like, so it has to trigger a refetch. It
   is also free for an attacker to generate. Hence ``REFETCH_COOLDOWN_SECONDS``:
   rotation heals within five minutes, a forged-kid flood costs one fetch.
4. **A failed fetch empties the cache** rather than keeping the previous keys —
   serving a key set we can no longer confirm is the same as serving a revoked
   key.

``verify()`` never raises on a bad token; it returns ``None``. Configuration
errors raise at construction, where they belong. No network at import time —
the JWKS is fetched on the first ``verify()``.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

import jwt
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWTError

# ── Environment ───────────────────────────────────────────────────────────────
ISSUER_ENV = "PULSE_MCP_OAUTH_ISSUER"
AUDIENCE_ENV = "PULSE_MCP_OAUTH_AUDIENCE"
JWKS_URL_ENV = "PULSE_MCP_OAUTH_JWKS_URL"
ROLE_MAP_ENV = "PULSE_MCP_ROLE_MAP"

# RLDatix role → Pulse role group. Two lines are load-bearing:
#   * RLDatix "admin" maps to "rldatix", NEVER to Pulse "admin". Pulse admin can
#     delete runs, read every user's history and manage users; no rep's
#     connector should ever hold that.
#   * Anything absent is REFUSED, not defaulted. "marketing" and "support" are
#     absent on purpose — a role nobody has thought about must not land silently
#     in a group. Add one at deploy time through PULSE_MCP_ROLE_MAP.
_DEFAULT_ROLE_MAP: dict[str, str] = {
    "ae": "salesteam", "bdr": "salesteam", "bdr_lead": "salesteam", "sales_lead": "salesteam",
    "cs": "customersuccess", "cs_lead": "customersuccess",
    "cro": "rldatix", "executive": "rldatix", "admin": "rldatix",
}

# Pulse roles an environment override may never name as a TARGET. The rule above
# is stated twice in prose and was enforced only by the default table, so
# PULSE_MCP_ROLE_MAP='{"marketing":"admin"}' — one line on the least-reviewed
# surface in this change, the deploy command — defeated it silently. These two
# are the elevated roles in server.py (`require_admin`, `require_integration_admin`).
_FORBIDDEN_ROLE_TARGETS = frozenset({"admin", "integrations_admin"})

# The same refusal for "this token did not verify" on BOTH paths, so a caller
# cannot use the message to learn which of the two their guess came closest to.
_INVALID_TOKEN = ("That token is not valid for Pulse. It may be expired, issued by a "
                  "different authorization server, or addressed to a different service.")


class PulseAuthError(Exception):
    """A refusal to authenticate. ``message`` is plain English and safe to
    return to the caller — it says what happened and what to do next.

    ``status`` separates the two kinds of refusal. 401 means "this token did not
    verify", and a connector answers it by running the OAuth dance again. 403
    means "the token verified and the answer is still no" — no Pulse account,
    or a deactivated one — which re-authenticating cannot fix. Returning 401
    there is what puts a rep with a valid RLDatix token and no Pulse account
    into an endless sign-in loop instead of showing them the message this module
    went to the trouble of writing."""

    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.status = status

    @property
    def message(self) -> str:
        return str(self)


@dataclass(frozen=True)
class PulseIdentity:
    """The verified caller behind one MCP request.

    ``email`` is the scoping key for every read and for the daily cap, so it is
    lowercased once here and never re-derived.

    ``role`` and ``account_role`` are NOT interchangeable; see the module
    docstring. ``role`` is the mapped group and gates access. ``account_role``
    is the caller's own Pulse ``users.role`` and is what a run is stamped with
    (``analysis_runs.user_role``) and what a minted download token carries —
    both deliberately bounded by what that person's Pulse login already grants.
    It is a required field: an identity with no answer to "what does this person
    hold in Pulse" must not be constructible."""

    email: str
    role: str
    account_role: str
    name: str
    brand: str
    source: str          # "oauth" | "pulse-session"


class JwtVerifier:
    """Verifies RS256 tokens from one issuer for one audience. Thread-safe.

    One instance per process, built once and reused: the JWKS cache lives on the
    instance, so a per-request verifier would refetch the key set every call."""

    #: The ONLY accepted signature algorithm. See the module docstring, rule 1.
    ALGORITHMS: tuple[str, ...] = ("RS256",)
    #: Clock-skew tolerance on exp/iat/nbf. Cloud Run and the authorization
    #: server are not NTP-locked to each other; a minute is small enough that an
    #: expired token stays expired in any useful sense.
    LEEWAY_SECONDS: float = 60.0
    #: Minimum gap between unknown-kid-triggered refetches (rule 3).
    REFETCH_COOLDOWN_SECONDS: float = 300.0
    #: The JWKS fetch must not be able to hang a request thread.
    HTTP_TIMEOUT_SECONDS: float = 5.0
    #: A JWKS is a handful of public keys; anything larger is a misconfigured
    #: URL or a hostile endpoint, and either way must not be read into memory.
    MAX_JWKS_BYTES: int = 256 * 1024
    #: Modulus floor. RSAAlgorithm.from_jwk will happily build a 56-bit "key"
    #: from a JWK whose `n` is malformed — a key that small is breakable, and an
    #: entry that produced one is corrupt or hostile either way.
    MIN_RSA_KEY_BITS: int = 2048

    def __init__(self, issuer: str, audience: str, jwks_url: str, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """`clock` is injectable so the refetch cooldown can be tested without
        sleeping five minutes; production never passes it."""
        if not issuer.strip():
            raise ValueError("issuer must not be empty")
        if not audience.strip():
            raise ValueError("audience must not be empty")
        scheme = urlparse(jwks_url).scheme.lower()
        if scheme not in ("http", "https"):
            # urllib will cheerfully open file:// and ftp://. This raises at
            # CONSTRUCTION, and mount_mcp constructs the verifier while
            # server.py is still importing (see the verifier() call there), so a
            # typo'd PULSE_MCP_OAUTH_JWKS_URL is a revision that does not start
            # rather than one that 401s everybody with the cause buried in the
            # container log. The two halves are one mechanism: without the eager
            # call this check would first run on somebody's first request.
            raise ValueError(f"jwks_url must be http(s), got {jwks_url!r} (scheme {scheme!r})")
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self._clock = clock
        self._lock = threading.Lock()
        # None until the first fetch; {} means "fetched, and it gave us nothing
        # usable" — a distinction the refetch logic depends on.
        self._keys: Optional[dict[str, Any]] = None
        self._refetched_at: Optional[float] = None

    def verify(self, token: str) -> Optional[dict]:
        """Verify `token`; return its payload, or None if it is not acceptable.

        None covers every rejection — bad signature, expired, wrong audience,
        wrong issuer, forbidden algorithm, unknown key. The caller's job is the
        same in all of those cases, and distinguishing them in the response
        would tell an attacker which of their guesses was closest."""
        if not token.strip():
            return None

        try:
            header: dict[str, Any] = jwt.get_unverified_header(token)
        except PyJWTError as exc:
            print(f"[pulse-mcp-auth] rejecting token: unreadable header ({exc})")
            return None

        algorithm = header.get("alg")
        if algorithm not in self.ALGORITHMS:
            # `none` and `HS256` die HERE, before a key is ever looked up.
            print(f"[pulse-mcp-auth] rejecting token: alg={algorithm!r} is not in {self.ALGORITHMS!r}")
            return None

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            print("[pulse-mcp-auth] rejecting token: no kid in the header")
            return None

        key = self._key_for(kid)
        if key is None:
            print(f"[pulse-mcp-auth] rejecting token: no JWKS key for kid={kid!r}")
            return None

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=list(self.ALGORITHMS),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.LEEWAY_SECONDS,
                # Absent is as bad as wrong: without `require`, a token with no
                # `exp` at all validates forever.
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except PyJWTError as exc:
            print(f"[pulse-mcp-auth] rejecting token: {type(exc).__name__}: {exc}")
            return None

        return payload

    def _key_for(self, kid: str) -> Optional[Any]:
        """The public key for `kid`, fetching or refetching as the rules allow.

        The lock is held ACROSS the network fetch on purpose: it serializes a
        burst of concurrent first requests into one outbound call instead of
        eight, which is the same instinct as the cooldown."""
        with self._lock:
            if self._keys is None:
                self._fetch_locked()

            key = (self._keys or {}).get(kid)
            if key is not None:
                return key

            now = self._clock()
            if (self._refetched_at is not None
                    and now - self._refetched_at < self.REFETCH_COOLDOWN_SECONDS):
                return None
            self._refetched_at = now
            self._fetch_locked()
            return (self._keys or {}).get(kid)

    def _fetch_locked(self) -> None:
        """Replace the key cache from `jwks_url`. Call with `_lock` held.

        A failed fetch leaves an EMPTY cache rather than the previous one. That
        is the conservative direction: serving a key set we can no longer
        confirm is the same as serving a revoked key. Everything 401s until the
        authorization server answers again, which is loud and recoverable."""
        try:
            document = self._get_jwks()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"[pulse-mcp-auth] JWKS fetch from {self.jwks_url} failed: {exc}")
            self._keys = {}
            return

        entries = document.get("keys")
        if not isinstance(entries, list):
            print(f"[pulse-mcp-auth] JWKS at {self.jwks_url} has no 'keys' array")
            self._keys = {}
            return

        keys: dict[str, Any] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kty") != "RSA":
                continue
            # `use` and `alg` are optional in a JWK; when present they must not
            # contradict what we are about to do with the key.
            if entry.get("use") not in (None, "sig"):
                continue
            if entry.get("alg") not in (None, *self.ALGORITHMS):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid:
                # Without a kid there is nothing to look this key up BY, and
                # trying every key in turn is how you burn CPU on garbage.
                continue
            try:
                key = RSAAlgorithm.from_jwk(json.dumps(entry))
            except (PyJWTError, ValueError, TypeError, KeyError) as exc:
                print(f"[pulse-mcp-auth] JWKS entry kid={kid!r} is unusable: {exc}")
                continue
            bits = getattr(key, "key_size", 0)
            if bits < self.MIN_RSA_KEY_BITS:
                print(f"[pulse-mcp-auth] JWKS entry kid={kid!r} is a {bits}-bit key — "
                      f"below the {self.MIN_RSA_KEY_BITS}-bit floor; refusing to use it")
                continue
            keys[kid] = key

        print(f"[pulse-mcp-auth] JWKS from {self.jwks_url}: {len(keys)} usable RSA key(s)")
        self._keys = keys

    def _get_jwks(self) -> dict[str, Any]:
        """One HTTP GET of the JWKS document. stdlib urllib, no new dependency."""
        request = urllib.request.Request(
            self.jwks_url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=self.HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(self.MAX_JWKS_BYTES + 1)
        if len(body) > self.MAX_JWKS_BYTES:
            raise ValueError(f"JWKS at {self.jwks_url} exceeds {self.MAX_JWKS_BYTES} bytes")
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"JWKS at {self.jwks_url} is not a JSON object")
        return parsed


# ── Process-wide verifier ─────────────────────────────────────────────────────
_verifier: Optional[JwtVerifier] = None
_verifier_lock = threading.Lock()


def oauth_config(env: Optional[Mapping[str, str]] = None) -> Optional[tuple[str, str, str]]:
    """(issuer, audience, jwks_url) when all three are set, else None.

    All three or nothing: a half-configured OAuth path would either verify
    against the wrong audience or fail every request, and both are worse than
    the documented "his own session tokens only" mode."""
    src = env if env is not None else os.environ
    issuer = (src.get(ISSUER_ENV) or "").strip()
    audience = (src.get(AUDIENCE_ENV) or "").strip()
    jwks_url = (src.get(JWKS_URL_ENV) or "").strip()
    if issuer and audience and jwks_url:
        return issuer, audience, jwks_url
    return None


def verifier() -> Optional[JwtVerifier]:
    """The process-wide JwtVerifier, built on first use, or None when OAuth is
    not configured. The JWKS cache lives on it, so it must not be rebuilt.

    ``mount_mcp`` calls this once at import time so the issuer/audience/scheme
    checks in ``__init__`` run at boot. That costs no network — the JWKS fetch
    stays lazy inside ``verify()``."""
    global _verifier
    if _verifier is None:
        config = oauth_config()
        if config is None:
            return None
        with _verifier_lock:
            if _verifier is None:
                _verifier = JwtVerifier(*config)
    return _verifier


def reset_verifier_cache() -> None:
    """Drop the cached verifier. Test seam only — production builds it once."""
    global _verifier
    with _verifier_lock:
        _verifier = None


# ── Role mapping ──────────────────────────────────────────────────────────────

def role_map(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The RLDatix-role → Pulse-role table, with PULSE_MCP_ROLE_MAP merged over
    the defaults (overrides win). Adding a role is a deploy-time change, not a
    code change. Malformed JSON is ignored whole rather than applied partially —
    a half-read map would grant a group nobody chose.

    An override may not name an elevated Pulse role as its target: the "never
    Pulse admin" rule is the reason this table exists, and a rule enforced only
    by the default values is a rule one typo in a deploy line defeats."""
    src = env if env is not None else os.environ
    mapping = dict(_DEFAULT_ROLE_MAP)
    raw = (src.get(ROLE_MAP_ENV) or "").strip()
    if not raw:
        return mapping
    try:
        extra = json.loads(raw)
    except ValueError as exc:
        print(f"[pulse-mcp-auth] {ROLE_MAP_ENV} is not valid JSON, ignoring it: {exc}")
        return mapping
    if not isinstance(extra, dict):
        print(f"[pulse-mcp-auth] {ROLE_MAP_ENV} is not a JSON object, ignoring it")
        return mapping
    for key, value in extra.items():
        if not (isinstance(key, str) and isinstance(value, str)
                and key.strip() and value.strip()):
            continue
        target = value.strip().lower()
        if target in _FORBIDDEN_ROLE_TARGETS:
            print(f"[pulse-mcp-auth] {ROLE_MAP_ENV} maps {key.strip().lower()!r} to "
                  f"{target!r}, which is an elevated Pulse role; refusing that entry")
            continue
        mapping[key.strip().lower()] = target
    return mapping


def map_role(rldatix_role: str) -> str:
    """The Pulse role group for an RLDatix role. Raises for anything unmapped —
    failing closed, because a role that lands in a group by default is a role
    nobody chose the permissions for."""
    role = (rldatix_role or "").strip().lower()
    mapped = role_map().get(role)
    if not mapped:
        raise PulseAuthError(f"Pulse access is not configured for the role '{role}'. "
                             f"Ask Taylor to add it to {ROLE_MAP_ENV}.")
    return mapped


# ── The decision ──────────────────────────────────────────────────────────────

def authenticate(token: str, srv: Any) -> PulseIdentity:
    """Turn a bearer token into a PulseIdentity, or raise PulseAuthError.

    Never returns None: every path either produces an identity or refuses with a
    message the caller can act on. `srv` is the running ``server`` module,
    injected so this stays importable and testable on its own."""
    if not (token or "").strip():
        raise PulseAuthError("No bearer token was presented.")
    token = token.strip()

    # An RLDatix token is a JWS (three dot-separated segments); a Pulse session
    # token is "<payload>.<hexsig>" (two). The shape is what routes the token,
    # so the two paths never both run on the same one.
    if oauth_config() is not None and _looks_like_jws(token):
        return _authenticate_oauth(token)
    return _authenticate_pulse_session(token, srv)


def _looks_like_jws(token: str) -> bool:
    """Does this token have the three-segment shape of a JWS with a readable
    header? Anything else is left to the Pulse session path."""
    if token.count(".") != 2:
        return False
    try:
        jwt.get_unverified_header(token)
    except PyJWTError:
        return False
    return True


def _authenticate_oauth(token: str) -> PulseIdentity:
    """Verify an RLDatix OAuth token and resolve it to a Pulse user row.

    The token proves who is asking; the ``users`` row is what grants access. The
    identity's ``role`` is ALWAYS the mapped group — the stored row cannot
    widen the access gate — while ``account_role`` is the stored row's own role,
    which is what runs and download tokens are stamped with. See the module
    docstring: the gate and the grant are two different questions and the stored
    row is the honest answer to the second."""
    checker = verifier()
    if checker is None:                                  # pragma: no cover - guarded by authenticate
        raise PulseAuthError(_INVALID_TOKEN)

    payload = checker.verify(token)
    if payload is None:
        raise PulseAuthError(_INVALID_TOKEN)

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        # mintAccessToken writes `email: email || null`, so this is a real case.
        raise PulseAuthError("Your RLDatix token carries no email address, so Pulse cannot "
                             "identify you. Reconnect the Pulse connector and sign in again.")

    role = str(payload.get("role") or "").strip().lower()
    if not role:
        raise PulseAuthError("Your RLDatix token carries no role.")
    pulse_role = map_role(role)

    user = _require_active_user(email)

    # Verbatim, not lower-cased: this value is compared byte-for-byte against
    # analysis_runs.user_role by query_history, and his own login mints
    # _create_token(user["role"]) with exactly these bytes.
    account_role = str(user.get("role") or "").strip() or pulse_role

    return PulseIdentity(
        email=email,
        role=pulse_role,
        account_role=account_role,
        name=user.get("name") or email.split("@")[0],
        brand=user.get("brand") or "original",
        source="oauth",
    )


def _require_active_user(email: str) -> dict:
    """The caller's Pulse ``users`` row, or a 403.

    Shared by both token paths on purpose: the module docstring and docs/mcp.md
    both say the ``users`` row is what grants access, and that was true of the
    OAuth path only — a native or Google user deactivated in Pulse kept full MCP
    access until their 30-day session token expired.

    403, not 401: the token verified. Re-running the OAuth dance cannot conjure
    a Pulse account, and a connector that sees 401 will keep trying."""
    from perception.auth import get_user_by_email
    user = get_user_by_email(email)
    if user is None:
        raise PulseAuthError(f"There is no Pulse account for {email}. Ask a Pulse admin to "
                             f"invite that address, then try again.", status=403)
    if not user.get("is_active"):
        raise PulseAuthError(f"The Pulse account for {email} is deactivated.", status=403)
    return user


def _authenticate_pulse_session(token: str, srv: Any) -> PulseIdentity:
    """Accept a Pulse session token — but only an identity-bearing one.

    Google SSO and native email+password tokens carry an email. The five shared
    team passwords mint a token with a role and nothing else; those are refused,
    because an identity-less caller cannot be scoped to its own runs and cannot
    be metered by the daily cap. There is no env escape hatch for this.

    The ``users`` row is then checked exactly as on the OAuth path. A session
    token lives thirty days (``server._SESSION_TTL_DAYS``), so without this a
    deactivated person keeps running reports on the shared keys for up to a
    month after Pulse says no."""
    payload = srv._verify_token_full(token)
    if payload is None:
        raise PulseAuthError(_INVALID_TOKEN)

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        app_url = getattr(srv, "APP_URL", "")
        raise PulseAuthError(
            "This Pulse session token came from a shared team password and carries no "
            "identity. The MCP needs a per-person sign-in for run history and the daily "
            f"cap. Sign in at {app_url} with your own Google account or email and "
            "password, and use that token.")

    _require_active_user(email)

    # His own role passes through unmapped on this path — the token IS his
    # identity — so the gate and the grant are the same value here.
    role = str(payload.get("role") or "user")
    return PulseIdentity(
        email=email,
        role=role,
        account_role=role,
        name=payload.get("name") or email.split("@")[0],
        brand=payload.get("brand") or "original",
        source="pulse-session",
    )
