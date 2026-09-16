"""Shared fakes for the MCP tests: a stand-in server module and a stand-in
database connection.

No test in this family opens Postgres, calls Anthropic, launches Chromium or
touches the network. `perception.db.get_connection` is the single seam both new
modules route through, so `install_fake_db` is all it takes to make them
testable.
"""
import asyncio
import time
import types
import uuid

_ANY = object()


class FakeConnection:
    """Stands in for perception.db._PgConnection.

    Same shape his code relies on — execute() returns self so
    `.execute(...).fetchone()` chains — backed by a list of
    (sql_fragment, rows) pairs. Every call is recorded on the shared `log` so a
    test can assert on the SQL that was actually issued, which is how the
    ownership tests prove `ran_by` is in every read."""

    def __init__(self, responses=None, log=None, description=None):
        self.responses = list(responses or [])
        self.log = log if log is not None else []
        self.closed = False
        self._rows = []
        self._description = description

    def execute(self, query, params=None):
        self.log.append((query, list(params) if params is not None else None))
        self._rows = self._match(query)
        return self

    def _match(self, query):
        collapsed = " ".join(query.split())
        for fragment, rows in self.responses:
            if fragment is _ANY or " ".join(fragment.split()) in collapsed:
                return list(rows)
        return []

    def executemany(self, query, params_list=None):
        self.log.append((query, list(params_list or [])))
        return self

    @property
    def description(self):
        return self._description

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        self.closed = True


class FakeDb:
    """Every connection a test's code opens, and every statement it ran."""

    def __init__(self, responses):
        self.responses = list(responses or [])
        self.log = []
        self.connections = []

    def __call__(self):
        con = FakeConnection(self.responses, log=self.log)
        self.connections.append(con)
        return con

    @property
    def statements(self):
        return [sql for sql, _params in self.log]

    def ran(self, fragment):
        """Was a statement containing `fragment` issued?"""
        needle = " ".join(fragment.split())
        return any(needle in " ".join(sql.split()) for sql in self.statements)


def install_fake_db(monkeypatch, responses=None):
    """Point perception.db.get_connection at a FakeDb and hand it back."""
    fake = FakeDb(responses)
    monkeypatch.setattr("perception.db.get_connection", fake)
    return fake


def _put(loop, queue, event):
    """His server._put, copied so the fake runners stream from a worker thread
    exactly as the real ones do.

    The one addition is the guard: the timeout test deliberately outlives its
    event loop, and a RuntimeError raised on a closed loop inside a daemon
    worker is a test-lifetime artifact, not the behaviour under test."""
    coroutine = queue.put(event)
    try:
        asyncio.run_coroutine_threadsafe(coroutine, loop)
    except RuntimeError:
        coroutine.close()


class FakeServer(types.SimpleNamespace):
    """The `server` module as the MCP sees it.

    The token functions are the REAL ones, imported from server.py, so the PDF
    link and legacy-token tests exercise his actual HMAC signing rather than a
    re-implementation that could agree with a bug."""

    def __init__(self, **overrides):
        import server

        super().__init__(
            _jobs={},
            APP_URL="https://pulse.test",
            _normalize_input=server._normalize_input,
            _create_token=server._create_token,
            _verify_token_full=server._verify_token_full,
            _zip_to_city_state=lambda zip_code: ("Murray", "UT"),
            calls=[],
            **overrides,
        )

    def _new_job(self, role, brand="original", email=None):
        """Registers a job the same way server._new_job does, including the real
        asyncio.Queue the job functions stream onto."""
        job_id = str(uuid.uuid4())
        self.calls.append(("_new_job", role, brand, email))
        self._jobs[job_id] = {"status": "running", "loop": asyncio.get_running_loop(),
                              "queue": asyncio.Queue(), "role": role, "brand": brand,
                              "email": email, "started_at": time.time()}
        return job_id

    def runner(self, name, *, result=None, error=None, phases=("Scoring the four pillars",),
               delay=0.0):
        """Build a job runner that analyses nothing.

        It records its positional arguments, pushes a couple of phase events and
        the closing sentinel like his real runners do, and then reports done or
        error."""
        def run(job_id, *args):
            job = self._jobs[job_id]
            loop, queue = job["loop"], job["queue"]
            self.calls.append((name, job_id) + args)
            for text in phases:
                _put(loop, queue, {"type": "phase", "name": "phase", "text": text})
            if delay:
                time.sleep(delay)
            if error is not None:
                job["status"] = "error"
                job["error"] = error
            else:
                job["status"] = "done"
                job["result"] = dict(result or {})
            _put(loop, queue, None)
        return run

    def arguments_for(self, name):
        """The positional arguments the named runner was called with."""
        for call in self.calls:
            if call and call[0] == name:
                return call[1:]
        return None
