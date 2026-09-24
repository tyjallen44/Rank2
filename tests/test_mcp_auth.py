"""Token verification for the MCP endpoint (perception/mcp_auth.py).

The JWKS is served from a real loopback HTTP server and the tokens are signed
with a real RSA key, so the verifier's actual fetch, cache and refusal paths run
— urllib is never mocked. No Postgres, no Anthropic, no outbound network.

Run with: python -m pytest tests/test_mcp_auth.py -v
"""
import sys
import os
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from perception import mcp_auth
from perception.mcp_auth import JwtVerifier, PulseAuthError, PulseIdentity
from tests._mcp_fakes import FakeServer

ISSUER = "https://mitch-crm.example/oauth"
AUDIENCE = "https://pulse.example/mcp"
KID = "pulse-test-key-1"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Keys, tokens and a loopback JWKS
# ─────────────────────────────────────────────────────────────────────────────

def _jwk(public_key, kid):
    """Public key as an RS256 JWK, the shape our authorization server publishes."""
    numbers = public_key.public_numbers()

    def b64(value):
        import base64
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
            "n": b64(numbers.n), "e": b64(numbers.e)}


class _Key:
    """One RSA keypair with the PEM and JWK forms the tests need."""

    def __init__(self, bits=2048, kid=KID):
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        self.pem = self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()).decode()
        self.public_pem = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.jwk = _jwk(self.private.public_key(), kid)

    def sign(self, claims, *, kid=None, algorithm="RS256"):
        return jwt.encode(claims, self.pem, algorithm=algorithm,
                          headers={"kid": kid or self.kid})


def _forge_hs256(secret, payload, kid):
    """Hand-assemble an HS256 token whose "secret" is the RSA public key.

    PyJWT refuses to build this one, so it is written out byte by byte — the
    point of the test is that OUR verifier refuses it, not that the library
    would have made it awkward."""
    import base64
    import hashlib
    import hmac

    def segment(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = f'{segment({"alg": "HS256", "typ": "JWT", "kid": kid})}.{segment(payload)}'
    digest = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f'{signing_input}.{base64.urlsafe_b64encode(digest).rstrip(b"=").decode()}'


class _JwksServer:
    """A loopback HTTP server that serves whatever document it is given and
    counts the requests, so the refetch-cooldown test can assert on hits."""

    def __init__(self, document):
        self.document = document
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                body = json.dumps(outer.document).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/jwks.json"

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="session")
def key():
    return _Key()


@pytest.fixture
def jwks(key):
    server = _JwksServer({"keys": [key.jwk]})
    yield server
    server.stop()


def claims(**overrides):
    """A token payload shaped like the one mitch-crm's mintAccessToken emits."""
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123", "iat": now,
               "exp": now + 900, "jti": "abc123", "ae_id": "taylor",
               "role": "ae", "email": "taylor.davis@rldatix.com", "scope": "pulse"}
    payload.update(overrides)
    return payload


@pytest.fixture
def oauth_env(monkeypatch, jwks):
    """Configure the OAuth path and clear the process-wide verifier cache."""
    monkeypatch.setenv(mcp_auth.ISSUER_ENV, ISSUER)
    monkeypatch.setenv(mcp_auth.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(mcp_auth.JWKS_URL_ENV, jwks.url)
    monkeypatch.delenv(mcp_auth.ROLE_MAP_ENV, raising=False)
    mcp_auth.reset_verifier_cache()
    yield jwks
    mcp_auth.reset_verifier_cache()


@pytest.fixture
def active_user(monkeypatch):
    """A Pulse users row for the token's email.

    role "user" is what create_user writes for a Google-approved account
    (server.py's oauth callback), and it is deliberately NOT the group an "ae"
    maps to: every test that confuses the access gate with the grant passes when
    those two strings are equal."""
    row = {"email": "taylor.davis@rldatix.com", "name": "Taylor Davis",
           "is_active": True, "brand": "rldatix", "role": "user"}
    monkeypatch.setattr("perception.auth.get_user_by_email", lambda email: dict(row))
    return row


@pytest.fixture
def active_session_user(monkeypatch):
    """The users row behind a Pulse session token. Both paths read the row now,
    so a session-token test without this one would open Postgres."""
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "name": "Ty Allen",
                                       "is_active": True, "role": "admin",
                                       "brand": "original"})


@pytest.fixture
def srv():
    return FakeServer()


# ─────────────────────────────────────────────────────────────────────────────
# 2. The happy path and the role map
# ─────────────────────────────────────────────────────────────────────────────

def test_good_jwt_maps_to_salesteam(key, oauth_env, active_user, srv):
    identity = mcp_auth.authenticate(key.sign(claims(role="ae")), srv)
    assert isinstance(identity, PulseIdentity)
    assert identity.email == "taylor.davis@rldatix.com"
    assert identity.role == "salesteam"
    assert identity.source == "oauth"
    assert identity.name == "Taylor Davis"
    assert identity.brand == "rldatix"


def test_account_role_is_the_pulse_users_row_not_the_mapped_group(key, oauth_env,
                                                                  active_user, srv):
    """The two answer different questions and only one of them GRANTS.

    `role` is the mapped group and gates access. `account_role` is what this
    person holds in Pulse, and it is what a run is stamped with and what a
    download token carries — so neither can exceed the person's own Pulse login.
    Minting the mapped group gave a Google-approved "user" account a working
    "salesteam" session credential, and with it every salesteam run's history,
    ran_by emails and PDFs."""
    identity = mcp_auth.authenticate(key.sign(claims(role="ae")), srv)
    assert identity.role == "salesteam"
    assert identity.account_role == "user"


def test_account_role_falls_back_to_the_mapped_group_when_the_row_has_none(
        key, oauth_env, srv, monkeypatch):
    """A row with no role at all is the only case where the mapped group is the
    best answer available — and it is stated, not silently defaulted."""
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "is_active": True, "role": None})
    identity = mcp_auth.authenticate(key.sign(claims(role="ae")), srv)
    assert identity.account_role == "salesteam"


def test_account_role_is_not_case_folded(key, oauth_env, srv, monkeypatch):
    """query_history compares user_role byte-for-byte and his own login mints
    _create_token(user["role"]) verbatim, so lower-casing here would file MCP
    runs somewhere his browser reads cannot find them."""
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "is_active": True,
                                       "role": "CustomerSuccess"})
    identity = mcp_auth.authenticate(key.sign(claims(role="ae")), srv)
    assert identity.account_role == "CustomerSuccess"


@pytest.mark.parametrize("rldatix_role,pulse_role", [
    ("ae", "salesteam"), ("bdr", "salesteam"), ("bdr_lead", "salesteam"),
    ("sales_lead", "salesteam"), ("cs", "customersuccess"), ("cs_lead", "customersuccess"),
    ("cro", "rldatix"), ("executive", "rldatix"), ("admin", "rldatix"),
])
def test_role_mapping_table(key, oauth_env, active_user, srv, rldatix_role, pulse_role):
    identity = mcp_auth.authenticate(key.sign(claims(role=rldatix_role)), srv)
    assert identity.role == pulse_role


def test_rldatix_admin_never_becomes_pulse_admin(key, oauth_env, active_user, srv):
    identity = mcp_auth.authenticate(key.sign(claims(role="admin")), srv)
    assert identity.role == "rldatix"
    # Pulse admin can delete runs, read every user's history and manage users.
    assert identity.role != "admin"


def test_stored_role_never_overrides_the_mapped_group(key, oauth_env, monkeypatch, srv):
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "name": "T", "is_active": True,
                                       "role": "admin", "brand": "original"})
    identity = mcp_auth.authenticate(key.sign(claims(role="ae")), srv)
    assert identity.role == "salesteam"


def test_unmapped_role_refused(key, oauth_env, active_user, srv):
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(claims(role="marketing")), srv)
    assert "marketing" in str(excinfo.value)
    assert mcp_auth.ROLE_MAP_ENV in str(excinfo.value)


def test_role_map_env_override(key, oauth_env, active_user, srv, monkeypatch):
    monkeypatch.setenv(mcp_auth.ROLE_MAP_ENV, json.dumps({"marketing": "marketing"}))
    identity = mcp_auth.authenticate(key.sign(claims(role="marketing")), srv)
    assert identity.role == "marketing"


@pytest.mark.parametrize("elevated", ["admin", "integrations_admin", "ADMIN", " admin "])
def test_role_map_override_cannot_grant_pulse_admin(elevated):
    """The "never Pulse admin" rule is stated twice in prose and was enforced
    only by the default table, so one line on the deploy command —
    PULSE_MCP_ROLE_MAP='{"marketing":"admin"}' — defeated it silently. Pulse
    admin can delete runs, read every user's history and manage users."""
    mapping = mcp_auth.role_map({mcp_auth.ROLE_MAP_ENV:
                                 json.dumps({"marketing": elevated})})
    assert "marketing" not in mapping
    assert not any(value in ("admin", "integrations_admin") for value in mapping.values())


def test_role_map_override_refusal_does_not_drop_the_rest_of_the_map():
    """One refused entry is refused; the others still apply."""
    mapping = mcp_auth.role_map({mcp_auth.ROLE_MAP_ENV:
                                 json.dumps({"marketing": "admin", "support": "partner"})})
    assert "marketing" not in mapping
    assert mapping["support"] == "partner"
    assert mapping["ae"] == "salesteam"


def test_malformed_role_map_is_ignored_whole():
    mapping = mcp_auth.role_map({mcp_auth.ROLE_MAP_ENV: "{not json"})
    assert mapping["ae"] == "salesteam"
    assert "marketing" not in mapping


# ─────────────────────────────────────────────────────────────────────────────
# 3. Token refusals
# ─────────────────────────────────────────────────────────────────────────────

def test_wrong_audience_refused(key, oauth_env, active_user, srv):
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(key.sign(claims(aud="https://somewhere.else/mcp")), srv)


def test_wrong_issuer_refused(key, oauth_env, active_user, srv):
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(key.sign(claims(iss="https://attacker.example")), srv)


def test_expired_token_refused(key, oauth_env, active_user, srv):
    now = int(time.time())
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(key.sign(claims(iat=now - 7200, exp=now - 3600)), srv)


def test_missing_exp_refused(key, oauth_env, active_user, srv):
    payload = claims()
    payload.pop("exp")
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(key.sign(payload), srv)


def test_alg_none_refused(key, oauth_env, active_user, srv):
    token = jwt.encode(claims(), key=None, algorithm="none", headers={"kid": key.kid})
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(token, srv)


def test_hs256_signed_with_public_key_refused(key, oauth_env, active_user, srv):
    # The public key is public; a verifier that trusts the header's alg would
    # accept this. PyJWT refuses to mint it, so it is assembled by hand exactly
    # as an attacker would.
    token = _forge_hs256(key.public_pem, claims(), key.kid)
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(token, srv)


def test_token_signed_by_a_different_key_refused(key, oauth_env, active_user, srv):
    other = _Key(kid=key.kid)
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(other.sign(claims()), srv)


def test_empty_token_refused(srv):
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate("   ", srv)
    assert "No bearer token" in str(excinfo.value)


def test_invalid_token_messages_are_identical(key, oauth_env, active_user, srv):
    """An attacker must not learn which of the two paths their guess reached."""
    forged_jws = key.sign(claims(), kid="no-such-kid")
    with pytest.raises(PulseAuthError) as oauth_side:
        mcp_auth.authenticate(forged_jws, srv)
    with pytest.raises(PulseAuthError) as session_side:
        mcp_auth.authenticate("not-a-real.token", srv)
    assert str(oauth_side.value) == str(session_side.value)


# ─────────────────────────────────────────────────────────────────────────────
# 4. The JWKS cache
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_kid_refetches_once_then_cools_down(key, jwks):
    clock = {"now": 1000.0}
    verifier = JwtVerifier(ISSUER, AUDIENCE, jwks.url, clock=lambda: clock["now"])

    assert verifier.verify(key.sign(claims())) is not None
    assert jwks.hits == 1

    forged = key.sign(claims(), kid="rotated-or-forged")
    assert verifier.verify(forged) is None
    assert jwks.hits == 2, "an unknown kid must trigger exactly one refetch"

    # Inside the cooldown: no further outbound calls however many arrive.
    for _ in range(5):
        assert verifier.verify(forged) is None
    assert jwks.hits == 2

    # Past the cooldown: rotation heals.
    clock["now"] += verifier.REFETCH_COOLDOWN_SECONDS + 1
    assert verifier.verify(forged) is None
    assert jwks.hits == 3


def test_jwks_fetch_failure_empties_cache(key, jwks):
    verifier = JwtVerifier(ISSUER, AUDIENCE, jwks.url)
    good = key.sign(claims())
    assert verifier.verify(good) is not None

    jwks.stop()
    # An unknown kid forces a refetch; the fetch now fails and must clear the
    # cache rather than keep serving keys we can no longer confirm.
    assert verifier.verify(key.sign(claims(), kid="unknown")) is None
    assert verifier.verify(good) is None


def test_undersized_rsa_key_rejected():
    weak = _Key(bits=1024, kid="weak-key")
    server = _JwksServer({"keys": [weak.jwk]})
    try:
        verifier = JwtVerifier(ISSUER, AUDIENCE, server.url)
        assert verifier.verify(weak.sign(claims())) is None
    finally:
        server.stop()


def test_encryption_only_jwk_is_skipped(key):
    entry = dict(key.jwk, use="enc")
    server = _JwksServer({"keys": [entry]})
    try:
        verifier = JwtVerifier(ISSUER, AUDIENCE, server.url)
        assert verifier.verify(key.sign(claims())) is None
    finally:
        server.stop()


def test_jwks_document_without_keys_array(key):
    server = _JwksServer({"not_keys": []})
    try:
        verifier = JwtVerifier(ISSUER, AUDIENCE, server.url)
        assert verifier.verify(key.sign(claims())) is None
    finally:
        server.stop()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/jwks.json", "jwks.json"])
def test_non_http_jwks_url_fails_at_construction(url):
    with pytest.raises(ValueError):
        JwtVerifier(ISSUER, AUDIENCE, url)


def test_blank_issuer_or_audience_fails_at_construction(jwks):
    with pytest.raises(ValueError):
        JwtVerifier("  ", AUDIENCE, jwks.url)
    with pytest.raises(ValueError):
        JwtVerifier(ISSUER, "", jwks.url)


def test_oauth_config_needs_all_three():
    assert mcp_auth.oauth_config({mcp_auth.ISSUER_ENV: ISSUER,
                                  mcp_auth.AUDIENCE_ENV: AUDIENCE}) is None
    assert mcp_auth.oauth_config({mcp_auth.ISSUER_ENV: ISSUER,
                                  mcp_auth.AUDIENCE_ENV: AUDIENCE,
                                  mcp_auth.JWKS_URL_ENV: "https://x/jwks"}) == (
        ISSUER, AUDIENCE, "https://x/jwks")


# ─────────────────────────────────────────────────────────────────────────────
# 5. The Pulse users row
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_email_refused(key, oauth_env, srv, monkeypatch):
    monkeypatch.setattr("perception.auth.get_user_by_email", lambda email: None)
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(claims()), srv)
    assert "taylor.davis@rldatix.com" in str(excinfo.value)
    assert "invite" in str(excinfo.value)
    # 403, not 401: the token verified. A connector answers 401 by re-running
    # the OAuth dance, which cannot conjure a Pulse account, so a rep with a
    # valid RLDatix token and no Pulse account would sign in forever.
    assert excinfo.value.status == 403


def test_inactive_user_refused(key, oauth_env, srv, monkeypatch):
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "is_active": False})
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(claims()), srv)
    assert "deactivated" in str(excinfo.value)
    assert excinfo.value.status == 403


def test_token_refusals_are_401(key, oauth_env, active_user, srv):
    """The other half of the same rule: a token that did not verify IS a token
    problem, and re-authenticating is exactly the right answer."""
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(claims(aud="https://somewhere.else/mcp")), srv)
    assert excinfo.value.status == 401
    with pytest.raises(PulseAuthError) as empty:
        mcp_auth.authenticate("   ", srv)
    assert empty.value.status == 401


def test_null_email_claim_refused(key, oauth_env, active_user, srv):
    # mintAccessToken really writes `email: email || null`.
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(claims(email=None)), srv)
    assert "no email address" in str(excinfo.value)


def test_missing_role_claim_refused(key, oauth_env, active_user, srv):
    payload = claims()
    payload.pop("role")
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(key.sign(payload), srv)
    assert "no role" in str(excinfo.value)


# ─────────────────────────────────────────────────────────────────────────────
# 6. His own session tokens
# ─────────────────────────────────────────────────────────────────────────────

def test_legacy_pulse_token_with_email_accepted(srv, monkeypatch, active_session_user):
    monkeypatch.delenv(mcp_auth.ISSUER_ENV, raising=False)
    monkeypatch.delenv(mcp_auth.AUDIENCE_ENV, raising=False)
    monkeypatch.delenv(mcp_auth.JWKS_URL_ENV, raising=False)
    mcp_auth.reset_verifier_cache()
    token = srv._create_token("admin", uid="u1", email="Ty@Rank2.example",
                              name="Ty Allen", brand="original")
    identity = mcp_auth.authenticate(token, srv)
    assert identity.source == "pulse-session"
    assert identity.email == "ty@rank2.example"
    # His own role passes through unmapped: this IS his identity, not a mapping
    # of ours. On this path the gate and the grant are therefore the same value.
    assert identity.role == "admin"
    assert identity.account_role == "admin"
    assert identity.name == "Ty Allen"


def test_legacy_token_for_deactivated_user_refused(srv, monkeypatch):
    """A session token lives thirty days (server._SESSION_TTL_DAYS). Without the
    users-row check on this path, a person deactivated in Pulse kept running
    reports on the shared Anthropic and Places keys, and reading their history,
    for up to a month after Pulse said no — while the module docstring and
    docs/mcp.md both claimed the users row is what grants access."""
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "is_active": False, "role": "admin"})
    token = srv._create_token("admin", email="ty@rank2.example", name="Ty Allen")
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(token, srv)
    assert "deactivated" in str(excinfo.value)
    assert excinfo.value.status == 403


def test_legacy_token_for_unknown_email_refused(srv, monkeypatch):
    monkeypatch.setattr("perception.auth.get_user_by_email", lambda email: None)
    token = srv._create_token("admin", email="ghost@rank2.example")
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(token, srv)
    assert "no Pulse account for ghost@rank2.example" in str(excinfo.value)
    assert excinfo.value.status == 403


def test_legacy_team_password_token_refused(srv):
    # server.login() mints exactly this: a role and nothing else.
    token = srv._create_token("salesteam")
    with pytest.raises(PulseAuthError) as excinfo:
        mcp_auth.authenticate(token, srv)
    message = str(excinfo.value)
    assert "shared team password" in message
    assert "https://pulse.test" in message


def test_legacy_token_still_accepted_when_oauth_is_configured(key, oauth_env, srv,
                                                              active_session_user):
    token = srv._create_token("customersuccess", email="cs@rldatix.com", name="CS")
    identity = mcp_auth.authenticate(token, srv)
    assert identity.source == "pulse-session"
    assert identity.role == "customersuccess"


def test_tampered_legacy_token_refused(srv):
    token = srv._create_token("admin", email="ty@rank2.example")
    body, _sig = token.rsplit(".", 1)
    with pytest.raises(PulseAuthError):
        mcp_auth.authenticate(f"{body}.{'0' * 64}", srv)


def test_pulse_auth_error_message_property():
    error = PulseAuthError("plain text")
    assert error.message == "plain text"
    assert error.status == 401, "401 is the default; 403 is opted into"
