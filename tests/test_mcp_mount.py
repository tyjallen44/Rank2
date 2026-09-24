"""Both halves of the PULSE_MCP_ENABLED flag (server.py + perception/mcp_server.py).

This is the file to read first: it asserts that with the flag OFF the app has no
/mcp route and every existing route is identical, and that with the flag ON the
endpoint answers a real JSON-RPC initialize at exactly /mcp.

TestClient runs the app's lifespan inside its context manager, which is what
proves the chained lifespan actually starts the MCP session manager — without it
the first request fails with "Task group is not initialized".

Run with: python -m pytest tests/test_mcp_mount.py -v
No Postgres, no Anthropic key, no outbound network.
"""
import sys
import os
import asyncio
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from perception import mcp_auth, mcp_server

MCP_ENV = {
    "PULSE_MCP_ENABLED": "1",
    "PULSE_MCP_OAUTH_ISSUER": "https://mitch-crm.example/oauth",
    "PULSE_MCP_OAUTH_AUDIENCE": "https://pulse.example/mcp",
    "PULSE_MCP_OAUTH_JWKS_URL": "https://mitch-crm.example/.well-known/jwks.json",
}
FLAG_KEYS = tuple(MCP_ENV)
ISSUER = MCP_ENV["PULSE_MCP_OAUTH_ISSUER"]
AUDIENCE = MCP_ENV["PULSE_MCP_OAUTH_AUDIENCE"]
JSON_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "pulse-mcp-tests", "version": "1"}}}


@pytest.fixture(autouse=True)
def pulse_account(monkeypatch):
    """An active Pulse users row for every session token these tests present.

    Both token paths check the row now, so without this the endpoint would open
    a real Postgres connection — which no test in this family may do."""
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "name": "Ty Allen",
                                       "is_active": True, "role": "admin",
                                       "brand": "original"})


@pytest.fixture
def load_server(monkeypatch):
    """Import server.py fresh under a chosen environment.

    server.py carries module-level state (_jobs, _pool, the FastAPI app), so each
    load gets its own module object and sys.modules is restored afterwards —
    otherwise the flag-off and flag-on tests leak into each other."""
    original = sys.modules.get("server")

    def load(**env):
        for key in FLAG_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        mcp_auth.reset_verifier_cache()
        sys.modules.pop("server", None)
        return importlib.import_module("server")

    yield load

    sys.modules.pop("server", None)
    if original is not None:
        sys.modules["server"] = original
    mcp_auth.reset_verifier_cache()


def route_signatures(app):
    """(path, methods) for every route, so two loads can be compared exactly."""
    return {(getattr(route, "path", None), frozenset(getattr(route, "methods", None) or ()))
            for route in app.routes}


def catch_all_index(app):
    return [i for i, route in enumerate(app.routes)
            if getattr(route, "path", None) == "/{full_path:path}"][0]


def session_token(server, **extra):
    """A Pulse session token from the loaded module's own signer."""
    return server._create_token("admin", email="ty@rank2.example", name="Ty Allen",
                                brand="original", **extra)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Flag off
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_off_leaves_app_untouched(load_server):
    server = load_server()
    assert not any(getattr(route, "path", None) == "/mcp" for route in server.app.routes)

    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert response.status_code in (404, 405)
        assert "jsonrpc" not in response.text

        # The SPA catch-all answers the discovery path, exactly as it does today.
        metadata = client.get("/.well-known/oauth-protected-resource")
        assert metadata.status_code == 200
        assert "<h1>Pulse</h1>" in metadata.text or "<!" in metadata.text


def test_flag_off_does_not_import_the_mcp_sdk(monkeypatch):
    """A deploy that has not been flipped must not even import the SDK."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items() if k not in FLAG_KEYS}
    probe = ("import sys; import server; "
             "print('mcp' in sys.modules, 'perception.mcp_server' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("False False"), out.stdout


# ─────────────────────────────────────────────────────────────────────────────
# 2. Flag on
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_on_mounts_mcp(load_server):
    server = load_server(**MCP_ENV)
    paths = [getattr(route, "path", None) for route in server.app.routes]
    assert "/mcp" in paths
    # A route registered after the SPA catch-all is a route the catch-all eats.
    assert paths.index("/mcp") < catch_all_index(server.app)
    assert paths.index("/.well-known/oauth-protected-resource") < catch_all_index(server.app)


def test_mcp_endpoint_is_at_exactly_slash_mcp(load_server):
    server = load_server(**MCP_ENV)
    token = session_token(server)
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["serverInfo"]["name"] == "Pulse"
        assert "instructions" in body["result"]

        listed = client.post("/mcp", headers={**JSON_HEADERS,
                                              "Authorization": f"Bearer {token}"},
                             json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                   "params": {}})
        assert listed.status_code == 200, listed.text
        names = sorted(tool["name"] for tool in listed.json()["result"]["tools"])
        assert len(names) == 9
        assert names[0] == "pulse_compare" and names[-1] == "pulse_trend"

        # The SDK's default path is /mcp; attaching it under a Mount would have
        # produced /mcp/mcp instead.
        nested = client.post("/mcp/mcp", json=INITIALIZE,
                             headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
        assert nested.status_code != 200
        assert "jsonrpc" not in nested.text


def test_unauthenticated_mcp_request_401s_with_www_authenticate(load_server):
    server = load_server(**MCP_ENV)
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith('Bearer realm="pulse"')
    assert 'resource_metadata="' in challenge
    assert "/.well-known/oauth-protected-resource/mcp" in challenge
    assert response.json()["error"] == "invalid_token"


def test_www_authenticate_omits_resource_metadata_without_oauth(load_server):
    server = load_server(PULSE_MCP_ENABLED="1")
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="pulse"'


def test_query_token_is_refused_on_mcp(load_server):
    """His REST routes accept ?token= by design, for EventSource. The MCP
    endpoint reads the Authorization header and nowhere else: a token in a URL
    lands in every access log and Referer between the client and Cloud Run."""
    server = load_server(**MCP_ENV)
    token = session_token(server)
    with TestClient(server.app) as client:
        refused = client.post(f"/mcp?token={token}", json=INITIALIZE, headers=JSON_HEADERS)
        assert refused.status_code == 401
        # The same token in the header works, so the refusal is about the
        # channel and not about the token.
        accepted = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
        assert accepted.status_code == 200


def test_shared_team_password_token_is_refused_over_mcp(load_server):
    server = load_server(**MCP_ENV)
    token = server._create_token("salesteam")          # what /api/auth/login mints
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "shared team password" in response.json()["error_description"]


def test_no_pulse_account_is_403_without_a_challenge(load_server, monkeypatch):
    """An authorization failure, not a token failure. A connector that sees 401
    plus WWW-Authenticate re-runs the OAuth dance, so returning 401 here puts a
    rep with a valid token and no Pulse account into an endless sign-in loop
    instead of showing them the message the code went to the trouble of
    writing."""
    server = load_server(**MCP_ENV)
    monkeypatch.setattr("perception.auth.get_user_by_email", lambda email: None)
    token = session_token(server)
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert "WWW-Authenticate" not in response.headers
    assert response.json()["error"] == "access_denied"
    assert "no Pulse account" in response.json()["error_description"]


def test_deactivated_pulse_account_is_403(load_server, monkeypatch):
    server = load_server(**MCP_ENV)
    monkeypatch.setattr("perception.auth.get_user_by_email",
                        lambda email: {"email": email, "is_active": False, "role": "admin"})
    token = session_token(server)
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert "WWW-Authenticate" not in response.headers
    assert "deactivated" in response.json()["error_description"]


def test_authentication_runs_off_the_event_loop(load_server, monkeypatch):
    """authenticate() does blocking I/O — a psycopg round trip on every call and,
    on a JWKS refetch, a urllib fetch with a five-second timeout held under a
    lock. This app has ONE event loop and it also serves every browser user, so
    an unauthenticated caller with a bogus kid could stall the whole service.

    The check is that no running loop is visible from the thread authenticate is
    called on: it succeeds only if the call really left the loop."""
    server = load_server(**MCP_ENV)
    seen = {}
    real = mcp_server.authenticate

    def probe(token, srv):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return real(token, srv)

    monkeypatch.setattr(mcp_server, "authenticate", probe)
    token = session_token(server)
    with TestClient(server.app) as client:
        response = client.post("/mcp", json=INITIALIZE,
                               headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert seen == {"on_loop": False}, "authenticate() must not block the event loop"


def test_malformed_jwks_url_fails_at_import(load_server):
    """The comment on that check says it fails at boot "rather than on the first
    request, which is the difference between a revision that does not start and
    one that 401s everybody". That is only true because mount_mcp builds the
    verifier eagerly; without the eager call a typo'd URL deploys green and then
    answers every request with the generic refusal."""
    with pytest.raises(ValueError) as excinfo:
        load_server(**{**MCP_ENV,
                       "PULSE_MCP_OAUTH_JWKS_URL": "mitch-crm.example/jwks.json"})
    assert "jwks_url must be http(s)" in str(excinfo.value)


def test_protected_resource_metadata_served_at_both_paths(load_server):
    server = load_server(**MCP_ENV)
    with TestClient(server.app) as client:
        root = client.get("/.well-known/oauth-protected-resource")
        suffixed = client.get("/.well-known/oauth-protected-resource/mcp")
    assert root.status_code == suffixed.status_code == 200
    assert root.json() == suffixed.json()
    assert root.json() == {"resource": MCP_ENV["PULSE_MCP_OAUTH_AUDIENCE"],
                           "authorization_servers": [MCP_ENV["PULSE_MCP_OAUTH_ISSUER"]],
                           "bearer_methods_supported": ["header"]}


def test_discovery_routes_absent_without_oauth(load_server):
    server = load_server(PULSE_MCP_ENABLED="1")
    paths = [getattr(route, "path", None) for route in server.app.routes]
    assert "/mcp" in paths
    assert "/.well-known/oauth-protected-resource" not in paths


# ─────────────────────────────────────────────────────────────────────────────
# 3. Nothing else moved
# ─────────────────────────────────────────────────────────────────────────────

def test_existing_routes_unchanged(load_server):
    off = load_server()
    off_routes = route_signatures(off.app)
    off_count = len(off.app.routes)

    on = load_server(**MCP_ENV)
    added = {path for path, _methods in route_signatures(on.app)} - {
        path for path, _methods in off_routes}

    assert added == {"/mcp", "/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp"}
    assert len(on.app.routes) == off_count + 3, (
        f"flag off: {off_count} routes, flag on: {len(on.app.routes)}")

    non_mcp = {(path, methods) for path, methods in route_signatures(on.app)
               if path not in added}
    assert non_mcp == off_routes, "an existing route changed path or methods"


def test_existing_route_still_answers_with_the_flag_on(load_server):
    on = load_server(**MCP_ENV)
    with TestClient(on.app) as client:
        response = client.get("/api/version")
    assert response.status_code == 200
    assert "version" in response.json()


# ─────────────────────────────────────────────────────────────────────────────
# 4. The documented deploy line
# ─────────────────────────────────────────────────────────────────────────────

def test_docs_quote_the_real_set_env_vars_line():
    """docs/mcp.md tells a reader to paste over deploy.sh:158, and that flag
    REPLACES the whole list — so a doc that paraphrases the line is a doc that
    silently drops a variable. It dropped REPORTS_DIR once, which would write
    every generated PDF outside the mounted GCS volume and 404 every report link
    after the next restart. This pins the quote to the file."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "deploy.sh"), encoding="utf-8") as handle:
        real = [line.strip() for line in handle if "--set-env-vars" in line]
    assert len(real) == 1, f"deploy.sh has {len(real)} --set-env-vars lines"
    with open(os.path.join(root, "docs", "mcp.md"), encoding="utf-8") as handle:
        doc = handle.read()

    # The unmodified line appears verbatim.
    assert real[0] in doc, f"docs/mcp.md does not quote deploy.sh verbatim:\n{real[0]}"

    # And the line it tells you to paste is that same line with only the four
    # PULSE_MCP_* variables appended — nothing dropped, nothing invented.
    prefix = real[0][:real[0].rindex('"')]
    pasteable = [line.strip() for line in doc.splitlines()
                 if line.strip().startswith(prefix) and "PULSE_MCP_ENABLED" in line]
    assert len(pasteable) == 1, "docs/mcp.md has no single paste-ready --set-env-vars line"
    added = pasteable[0][len(prefix):].strip('" \\').lstrip(",")
    assert sorted(pair.split("=")[0] for pair in added.split(",")) == [
        "PULSE_MCP_ENABLED", "PULSE_MCP_OAUTH_AUDIENCE", "PULSE_MCP_OAUTH_ISSUER",
        "PULSE_MCP_OAUTH_JWKS_URL"]

    # ACCESS_PASSWORD and the keys are --set-secrets entries, not env vars.
    assert "ACCESS_PASSWORD=" not in pasteable[0]


# ─────────────────────────────────────────────────────────────────────────────
# 5. A tool call, through the real transport
# ─────────────────────────────────────────────────────────────────────────────
# Every test above proves the mount, the auth gate or the documented deploy
# line in isolation. This one runs a real RS256 JWT through the actual /mcp
# endpoint and checks that the tool it reaches is scoped to that JWT's own
# email: the whole identity chain (mcp_auth.authenticate, PulseIdentity,
# _current_identity, then the tool's own SQL) in one request, not the pieces
# tested one at a time. tests/test_mcp_auth.py owns the full verifier test
# matrix; this needs only one real, independently verifiable token.

class _ToolCallJwks:
    """A loopback HTTP server serving one RSA key as a JWKS document."""

    def __init__(self, kid="pulse-mount-test-key"):
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.kid = kid
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()).decode()
        numbers = private.public_key().public_numbers()

        def b64(value):
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        document = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                              "n": b64(numbers.n), "e": b64(numbers.e)}]}
        body = json.dumps(document).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
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

    def sign(self, **claims):
        import time as _time

        import jwt as _jwt
        now = int(_time.time())
        payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-456", "iat": now,
                  "exp": now + 900, "role": "ae"}
        payload.update(claims)
        return _jwt.encode(payload, self.pem, algorithm="RS256", headers={"kid": self.kid})

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


def test_tool_call_runs_as_the_authenticated_caller(load_server, monkeypatch):
    """A real RS256 JWT for one caller, POSTed to /mcp as tools/call for
    pulse_history, must produce a response scoped to that caller's own email.
    pulse_history is history filtered on ran_by, never role-wide (see
    perception/mcp_server.py's own docstring on it), and the SQL is the proof
    that the identity the middleware built from the JWT is the identity the
    tool actually ran with."""
    from tests._mcp_fakes import install_fake_db

    jwks = _ToolCallJwks()
    try:
        server = load_server(**{**MCP_ENV, "PULSE_MCP_OAUTH_JWKS_URL": jwks.url})
        caller = "houston.mercer@rldatix.com"
        token = jwks.sign(email=caller)
        db = install_fake_db(monkeypatch, [])

        with TestClient(server.app) as client:
            client.post("/mcp", json=INITIALIZE,
                        headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"})
            response = client.post(
                "/mcp", headers={**JSON_HEADERS, "Authorization": f"Bearer {token}"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "pulse_history", "arguments": {}}})

        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body
        text = body["result"]["content"][0]["text"]
        assert text == "No runs of yours in the last 45 days. pulse_run_report starts one."

        analysis_params = [params for sql, params in db.log if "FROM analysis_runs" in sql]
        assert analysis_params, "pulse_history must issue its own scoped SQL"
        assert analysis_params[0][0] == caller, (
            "the SQL must be scoped to the JWT's own email, not any other caller's")
    finally:
        jwks.stop()
