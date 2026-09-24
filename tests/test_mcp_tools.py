"""The nine MCP tools (perception/mcp_server.py).

Every test runs against the fakes in tests/_mcp_fakes.py: no Postgres, no
Anthropic call, no Chromium, no network. The tool implementations are called
directly rather than through the SDK's call_tool, whose signature moves between
SDK versions; one test asserts the registered names and schemas instead.

Run with: python -m pytest tests/test_mcp_tools.py -v
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from perception import mcp_server, mcp_usage
from perception.mcp_auth import PulseAuthError, PulseIdentity
from tests._mcp_fakes import FakeServer, install_fake_db

CALLER = "taylor.davis@rldatix.com"
#: Captured before the autouse stub below replaces it, so one test can still
#: exercise the real resolver.
REAL_RESOLVE = mcp_server._resolve_addresses
TIERS = json.dumps({"clinical_outcomes_safety": 68, "credentials_recognition": 74,
                    "patient_experience_reviews": 70, "access_fit": 73})


@pytest.fixture
def identity():
    """An AE's RLDatix token, resolved.

    `role` is the mapped group and gates access; `account_role` is what this
    person's own Pulse users row says, and it is what runs and download tokens
    are stamped with. They are deliberately DIFFERENT here — a Google-approved
    Pulse account is created with role "user" (server.py's oauth callback) —
    because every test that confuses the two passes when they are equal."""
    return PulseIdentity(email=CALLER, role="salesteam", account_role="user",
                         name="Taylor Davis", brand="original", source="oauth")


@pytest.fixture
def srv():
    return FakeServer()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve any hostname to one public address.

    _clean_url resolves the host to refuse private and loopback targets, and no
    test in this file may reach real DNS. Literal IPs never come through here —
    _clean_url parses those directly — so the refusal tests exercise the real
    check, and one test below calls the real _resolve_addresses against
    localhost, which /etc/hosts answers without a network."""
    monkeypatch.setattr(mcp_server, "_resolve_addresses",
                        lambda host: ["93.184.216.34"])


@pytest.fixture
def reserved(monkeypatch):
    """Record every cap reservation without touching a database.

    usage_today is stubbed from the same list: pulse_network_report reads it
    before discovery, and a test that left it live would open Postgres."""
    calls = []

    def reserve(email, cap=None, **kwargs):
        calls.append((email, cap))
        return len(calls)

    monkeypatch.setattr(mcp_usage, "reserve_run", reserve)
    monkeypatch.setattr(mcp_usage, "usage_today", lambda email, **kwargs: len(calls))
    return calls


def run_row(**overrides):
    """One analysis_runs + rank-1 ranked_providers row, in the tool's own column
    order — so a column reorder in the query cannot silently pass."""
    row = {"entity_name": "Intermountain Medical Center", "location": "Murray, UT",
           "generated_at": "2026-09-18", "entity_type": "hospital", "specialty": None,
           "service_line": None, "parent_system": None, "confidence": "high",
           "confidence_note": "312 reviews across 4 locations", "mqcr": None,
           "ran_by": CALLER, "pdf_path": "/reports/imc.pdf", "teaser_pdf_path": None,
           "briefing_pdf_path": "/reports/imc-briefing.pdf", "individual_report": True,
           "ai_visibility_score": 71,
           "tier_scores": TIERS, "overall_rating": "B", "weighting_profile": "procedural"}
    row.update(overrides)
    return tuple(row[column] for column in mcp_server._RUN_COLUMNS)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Registration and the parameter allowlist
# ─────────────────────────────────────────────────────────────────────────────

async def test_tool_names_registered(srv):
    mcp = mcp_server.build_mcp(srv)
    names = sorted(tool.name for tool in await mcp.list_tools())
    assert names == sorted(mcp_server._TOOL_NAMES)
    assert len(names) == 9


async def test_no_tool_accepts_a_free_text_notes_parameter(srv):
    """Nothing but an organization name and a location crosses to Pulse.

    This is the regression test for that rule: it fails the moment somebody adds
    a `notes`, `context` or `account_id` parameter to any tool."""
    allowed = {
        "organization", "organization_a", "organization_b", "city", "city_a", "city_b",
        "state", "state_a", "state_b", "zip_code", "specialty", "specialty_a",
        "specialty_b", "entity_kind", "entity_type_a", "entity_type_b", "report_type",
        "aggregate", "briefing", "website_url", "attach_to_run_id", "run_id", "days",
        "limit", "network_name", "hq_location", "facility_type", "facilities", "source_url",
    }
    mcp = mcp_server.build_mcp(srv)
    for tool in await mcp.list_tools():
        properties = set((tool.input_schema.get("properties") or {}).keys())
        assert properties <= allowed, f"{tool.name} takes {properties - allowed}"


async def test_facilities_entries_are_reduced_to_name_city_state(srv, identity):
    """The parameter-name allowlist above checks NAMES only, so `facilities`
    passed it while being the one unconstrained shape on the surface: a list of
    dicts with arbitrary keys and arbitrary string values, handed to Places and
    Anthropic. This asserts the SHAPE of an entry, which is the rule the name
    check exists to protect."""
    cleaned = mcp_server._clean_facilities(
        srv, [{"name": "alpha murray", "city": "murray", "state": "ut"}])
    assert cleaned == [{"name": "Alpha Murray", "city": "Murray", "state": "UT"}]

    for entry, expected in (
            ({"name": "Alpha", "city": "Murray", "state": "UT",
              "notes": "EB is the CFO, renewal at risk"}, "notes"),
            ({"name": "Alpha", "contact": "Jane Doe"}, "contact"),
            ({"name": "Alpha", "account_id": "0064x00001"}, "account_id")):
        message = await mcp_server._pulse_network_report(srv, identity, "Alpha Health System",
                                                         facilities=[entry])
        assert message.startswith("Refused: facilities entry 1 carries")
        assert expected in message

    not_a_dict = await mcp_server._pulse_network_report(srv, identity, "Alpha Health System",
                                                        facilities=["Alpha Murray"])
    assert not_a_dict.startswith("Refused: facilities entry 1 is not an object.")

    # A bad value inside an entry names the entry, because the shared cleaners
    # speak about "organization" and "state" and cannot know which of two
    # hundred rows they were handed.
    bad_state = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"},
                    {"name": "Alpha Provo", "city": "Provo", "state": "Utah"}])
    assert bad_state == ("Refused: facilities entry 2 — state must be a two-letter US "
                         'abbreviation, for example "UT".')


@pytest.mark.parametrize("value", [
    "file:///etc/passwd",
    "http://127.0.0.1:8000/api/admin/users",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5:8080/",
    "http://[::1]:8000/",
    "ftp://example.org/",
    "javascript:alert(1)",
    "//example.org/",
])
async def test_website_url_must_be_a_public_http_address(srv, identity, value):
    """The crawl runs inside Ty's Cloud Run service on behalf of a model that can
    be steered by what it reads, so an unvalidated URL is an internal port
    scanner: analyze_content echoes fetch_status, pages_crawled and the origin
    back to the caller."""
    message = await mcp_server._pulse_content_check(srv, identity, "Some Hospital",
                                                    "Murray", "UT", website_url=value)
    assert message.startswith("Refused: "), message
    assert "public" in message, message


async def test_website_url_may_not_carry_a_query_string(srv, identity):
    """A query string on a URL Pulse fetches is an egress channel: arbitrary text
    out of the model's context, through Ty's server, to whoever owns the host.
    A front door has no legitimate one."""
    message = await mcp_server._pulse_content_check(
        srv, identity, "Some Hospital", "Murray", "UT",
        website_url="https://attacker.example/?leak=the%20whole%20account%20plan")
    assert message == ("Refused: website_url must be a plain front-door address with no "
                       "query string or fragment.")


async def test_source_url_gets_the_same_guard(srv, identity):
    """pulse_network_report's source_url lands in analyze_content too, through
    network_analyzer, so it is the same hole under a different name."""
    message = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System", source_url="http://127.0.0.1:8000/",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert message.startswith("Refused: source_url resolves to 127.0.0.1")


def test_private_address_guard_runs_against_real_resolution(monkeypatch):
    """Every other test in this file stubs _resolve_addresses. This one restores
    the real one — localhost is answered by /etc/hosts, not by the network — so
    the shipped resolution path is the thing under test rather than the stub."""
    monkeypatch.setattr(mcp_server, "_resolve_addresses", REAL_RESOLVE)
    assert "127.0.0.1" in mcp_server._resolve_addresses("localhost")
    with pytest.raises(mcp_server._Refusal) as refusal:
        mcp_server._clean_url("http://localhost:8000/")
    assert "not a public address" in str(refusal.value)


async def test_public_website_url_is_passed_through_unchanged(srv, identity, monkeypatch,
                                                              analyzed):
    install_fake_db(monkeypatch, [])
    await mcp_server._pulse_content_check(srv, identity, "Some Hospital", "Murray", "UT",
                                          website_url="https://example.org/find-a-doctor")
    assert analyzed["urls"] == ["https://example.org/find-a-doctor"]


@pytest.mark.parametrize("tool,kwargs", [
    ("_pulse_run_report", {"organization": "Some Clinic", "city": "Murray", "state": "UT"}),
    ("_pulse_compare", {"organization_a": "A Clinic", "city_a": "Murray", "state_a": "UT",
                        "organization_b": "B Clinic", "city_b": "Provo", "state_b": "UT"}),
])
async def test_specialty_is_length_bounded(srv, identity, reserved, tool, kwargs):
    """specialty names a service line. Uncapped it is free text a model can fill
    with anything it happens to be holding, and it travels to Anthropic."""
    key = "specialty" if tool == "_pulse_run_report" else "specialty_a"
    message = await getattr(mcp_server, tool)(srv, identity, **kwargs, **{key: "x" * 81})
    assert message == ("Refused: specialty must be 80 characters or fewer. It names a "
                       "service line, not a description.")
    assert reserved == [], "a refused argument must not spend a run"


async def test_hq_location_is_length_bounded(srv, identity, reserved):
    message = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System", hq_location="x" * 121,
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert message.startswith("Refused: hq_location must be 120 characters or fewer.")
    assert reserved == []


async def test_identity_is_required(srv):
    """A tool must never run without a verified caller, even if the middleware
    wiring changes."""
    mcp = mcp_server.build_mcp(srv)
    registered = {tool.name for tool in await mcp.list_tools()}
    assert "pulse_history" in registered
    with pytest.raises(PulseAuthError):
        mcp_server._current_identity()


@pytest.mark.parametrize("state", ["Utah", "U", "ZZ", "", "  "])
async def test_state_must_be_a_two_letter_abbreviation(srv, identity, state):
    message = await mcp_server._pulse_run_report(srv, identity, "Some Hospital", "Murray", state)
    assert message == ('Refused: state must be a two-letter US abbreviation, for '
                       'example "UT".')


async def test_organization_length_is_bounded(srv, identity):
    message = await mcp_server._pulse_run_report(srv, identity, "X", "Murray", "UT")
    assert message.startswith("Refused: organization must be between 2 and 200")


# ─────────────────────────────────────────────────────────────────────────────
# 2. pulse_find_entity
# ─────────────────────────────────────────────────────────────────────────────

async def test_find_entity_formats_candidates(srv, identity, monkeypatch):
    monkeypatch.setattr("perception.data.places.search_entity_candidates",
                        lambda name, city, state: [
                            {"name": "Intermountain Medical Center",
                             "address": "5121 S Cottonwood St, Murray, UT 84107",
                             "rating": 4.1, "review_count": 2431}])
    out = await mcp_server._pulse_find_entity(srv, identity, "INTERMOUNTAIN MEDICAL CENTER",
                                              "murray", "ut")
    assert "1. Intermountain Medical Center" in out
    assert "5121 S Cottonwood St, Murray, UT 84107" in out
    assert "Google 4.1 (2,431 reviews)" in out
    assert "pulse_run_report" in out


async def test_find_entity_no_results_refusal(srv, identity, monkeypatch):
    monkeypatch.setattr("perception.data.places.search_entity_candidates",
                        lambda name, city, state: [])
    out = await mcp_server._pulse_find_entity(srv, identity, "Nowhere Clinic", "Murray", "UT")
    assert out.startswith("Not found:")
    # search_entity_candidates returns [] for a missing API key AND for no
    # matches, so the message must not claim to know which.
    assert "API key" not in out


async def test_find_entity_resolves_a_zip(srv, identity, monkeypatch):
    seen = {}

    def search(name, city, state):
        seen.update(name=name, city=city, state=state)
        return []

    monkeypatch.setattr("perception.data.places.search_entity_candidates", search)
    await mcp_server._pulse_find_entity(srv, identity, "Some Hospital", zip_code="84107")
    assert seen["city"] == "Murray" and seen["state"] == "UT"


async def test_find_entity_does_not_consume_cap(srv, identity, monkeypatch, reserved):
    monkeypatch.setattr("perception.data.places.search_entity_candidates",
                        lambda name, city, state: [])
    await mcp_server._pulse_find_entity(srv, identity, "Some Hospital", "Murray", "UT")
    assert reserved == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. pulse_content_check
# ─────────────────────────────────────────────────────────────────────────────

class _Finding:
    def __init__(self, finding_id="CIK-001", severity="high", platform="website",
                 summary="No llms.txt at the root."):
        self.finding_id = finding_id
        self.severity = severity
        self.platform = platform
        self.teaser_summary = summary
        self.current_state = "404 at https://example.org/llms.txt"
        self.expected_state = "a published llms.txt naming the primary service pages"

    def model_dump(self):
        return {"finding_id": self.finding_id, "severity": self.severity,
                "platform": self.platform, "teaser_summary": self.teaser_summary,
                "remediation_type": "website_fix", "status": "verified"}


class _Findings:
    def __init__(self):
        self.status = "partial"
        self.findings = [_Finding()]
        self.source_snapshot = {"website_urls": ["https://example.org"], "pages_crawled": 3,
                                "wikidata_qid": "Q1234567",
                                "wikipedia_article": "Intermountain Medical Center"}


@pytest.fixture
def analyzed(monkeypatch):
    """Capture the arguments analyze_content is called with."""
    seen = {}

    def analyze_content(entity_name, website_urls, city="", state="", entity_kind="hospital",
                        reputation=None, safety=None, on_event=None):
        seen.update(entity_name=entity_name, urls=website_urls, city=city, state=state,
                    entity_kind=entity_kind, reputation=reputation, safety=safety)
        return _Findings()

    monkeypatch.setattr("perception.content_analyzer.analyze_content", analyze_content)
    return seen


async def test_content_check_calls_analyzer_with_none_reputation_and_safety(
        srv, identity, monkeypatch, analyzed):
    install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_content_check(srv, identity, "Intermountain Medical Center",
                                                "Murray", "UT",
                                                website_url="https://example.org")
    assert analyzed["reputation"] is None
    assert analyzed["safety"] is None
    assert "[CIK-001]" in out
    assert "reputation and safety findings need a full report run" in out


async def test_content_check_is_ephemeral_by_default(srv, identity, monkeypatch, analyzed):
    db = install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_content_check(srv, identity, "Intermountain Medical Center",
                                                "Murray", "UT",
                                                website_url="https://example.org")
    assert "Findings were not saved." in out
    assert not db.ran("INSERT INTO content_findings")


async def test_content_check_saves_when_attach_to_run_id_given(srv, identity, monkeypatch,
                                                               analyzed):
    db = install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    out = await mcp_server._pulse_content_check(srv, identity, "Intermountain Medical Center",
                                                "Murray", "UT",
                                                website_url="https://example.org",
                                                attach_to_run_id="run-1")
    assert db.ran("INSERT INTO content_findings")
    assert "saved against run run-1" in out


async def test_content_check_refuses_to_attach_to_another_users_run(srv, identity, monkeypatch,
                                                                    analyzed):
    db = install_fake_db(monkeypatch,
                         [("FROM analysis_runs a", [run_row(ran_by="someone.else@x.com")])])
    out = await mcp_server._pulse_content_check(srv, identity, "Intermountain Medical Center",
                                                "Murray", "UT",
                                                website_url="https://example.org",
                                                attach_to_run_id="run-1")
    assert "no run run-1 of yours" in out
    assert not db.ran("INSERT INTO content_findings")


async def test_content_check_does_not_consume_cap(srv, identity, monkeypatch, analyzed,
                                                  reserved):
    install_fake_db(monkeypatch, [])
    await mcp_server._pulse_content_check(srv, identity, "Intermountain Medical Center",
                                          "Murray", "UT", website_url="https://example.org")
    assert reserved == [], "the free tool must not be metered"


async def test_content_check_refuses_without_a_resolvable_website(srv, identity, monkeypatch,
                                                                  analyzed):
    install_fake_db(monkeypatch, [])
    monkeypatch.setattr("perception.data.places.fetch_provider",
                        lambda name, city, state: (None, None))
    out = await mcp_server._pulse_content_check(srv, identity, "Nowhere Clinic", "Murray", "UT")
    assert out.startswith("Refused: no website could be resolved")


# ─────────────────────────────────────────────────────────────────────────────
# 4. pulse_run_report
# ─────────────────────────────────────────────────────────────────────────────

def _done_runner(srv, name="single", run_id="run-1"):
    return srv.runner(name, result={"run_id": run_id, "location": "Murray, UT"})


async def test_run_report_reserves_cap_before_running(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])

    def refuse(email, cap=None, **kwargs):
        raise mcp_usage.DailyCapExceeded(email, cap or 20)

    monkeypatch.setattr(mcp_usage, "reserve_run", refuse)
    srv._job_run_single = _done_runner(srv)
    out = await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                             "Murray", "UT")
    assert out.startswith("Refused: you have used your daily Pulse limit of 20 report runs.")
    assert srv.arguments_for("single") is None, "the runner must never start"
    assert srv._jobs == {}, "no job may be created once the cap refuses"


async def test_run_report_stamps_caller_email(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    srv._job_run_single = _done_runner(srv)
    await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                       "Murray", "UT")
    # _new_job's email is what set_run_role writes to analysis_runs.ran_by, which
    # is what every ownership-scoped read then filters on. Its ROLE is what the
    # same call writes to user_role, and that must be the caller's own Pulse
    # account role — "user" here — not the mapped group "salesteam", or the run
    # is filed into a group this person does not belong to and the download
    # token minted to read it back grants that whole group's history.
    assert ("_new_job", "user", "original", CALLER) in srv.calls
    assert not any(call[1] == "salesteam" for call in srv.calls if call[0] == "_new_job")


async def test_run_report_never_sets_force_rerun_or_override_today_lock(srv, identity,
                                                                        monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    srv._job_run_single = _done_runner(srv)
    await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                       "Murray", "UT")
    job = list(srv._jobs.values())[0]
    assert job["force_rerun"] is False
    assert job["override_today_lock"] is False
    assert job["source"] == "mcp"


@pytest.mark.parametrize("report_type,runner_name,expected", [
    ("hospital", "_job_run_single", ("Murray", "UT", None, True, None, "hospital")),
    ("practice", "_job_run_practice", ("Some Clinic", "Murray", "UT", None, True, None)),
    ("fqhc", "_job_run_fqhc", ("Some Clinic", "Murray", "UT", True)),
])
async def test_run_report_routes_report_type(srv, identity, monkeypatch, reserved,
                                             report_type, runner_name, expected):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    for name in ("_job_run_single", "_job_run_practice", "_job_run_fqhc"):
        setattr(srv, name, srv.runner(name, result={"run_id": "run-1"}))
    await mcp_server._pulse_run_report(srv, identity, "Some Clinic", "Murray", "UT",
                                       report_type=report_type)
    arguments = srv.arguments_for(runner_name)
    assert arguments is not None, f"{runner_name} was not called"
    assert arguments[1:] == expected
    for other in ("_job_run_single", "_job_run_practice", "_job_run_fqhc"):
        if other != runner_name:
            assert srv.arguments_for(other) is None


async def test_run_report_refuses_unknown_report_type(srv, identity):
    out = await mcp_server._pulse_run_report(srv, identity, "Some Clinic", "Murray", "UT",
                                             report_type="dental")
    assert out == 'Refused: report_type must be "hospital", "practice" or "fqhc".'


async def test_run_report_timeout_message(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [])
    monkeypatch.setattr(mcp_server, "_run_timeout", lambda: 0.2)
    srv._job_run_single = srv.runner("single", result={"run_id": "run-1"},
                                     phases=("Scoring the four pillars",), delay=2.0)
    out = await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                             "Murray", "UT")
    assert out.startswith("Still running after 1 minute: Intermountain Medical Center "
                          "(Murray, UT).")
    assert "Last step: Scoring the four pillars" in out
    assert "counted against your daily cap" in out
    assert "pulse_history" in out and "pulse_get_report" in out
    # The entry stays so the sweeper collects it later; deleting it would lose
    # the run from Admin -> Operations while it is still going.
    assert len(srv._jobs) == 1


def test_timeout_message_pluralizes_minutes(monkeypatch):
    assert mcp_server._timeout_message("Alpha", "", 60).startswith("Still running after 1 minute:")
    assert mcp_server._timeout_message("Alpha", "", 30).startswith("Still running after 1 minute:")
    assert mcp_server._timeout_message("Alpha", "", 200).startswith(
        "Still running after 3 minutes:")
    assert mcp_server._timeout_message("Alpha", "", 0).startswith(
        "Still running after 1 minute:")


async def test_run_report_overloaded_error_message(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [])
    srv._job_run_single = srv.runner("single", error="__OVERLOADED__")
    out = await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                             "Murray", "UT")
    assert out.startswith("Refused: the analysis service is overloaded right now.")
    assert "still counted against your daily cap" in out


async def test_run_report_plain_error_message_does_not_echo_the_exception(
        srv, identity, monkeypatch, reserved, capsys):
    """His job functions record raw exception strings, which routinely carry
    connection strings, absolute REPORTS_DIR paths, upstream API bodies and SQL.
    Everything this tool returns lands in the model's context and in somebody's
    chat transcript, so the detail is logged and the caller gets a bounded
    message."""
    install_fake_db(monkeypatch, [])
    leak = ("could not connect to postgresql://pulse:hunter2@ep-neon.aws.neon.tech/rank2 "
            "while writing /data/reports/imc.pdf")
    srv._job_run_single = srv.runner("single", error=leak)
    out = await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                             "Murray", "UT")
    assert out == ("Refused: the run failed on the server. A Pulse admin can find it in "
                   "Admin -> Operations.")
    assert "postgresql://" not in out
    assert "hunter2" not in out
    assert "/data/reports" not in out
    # Logged, not lost: the operator still needs the cause.
    assert leak in capsys.readouterr().out


async def test_run_report_prints_the_score_and_the_remaining_cap(srv, identity, monkeypatch,
                                                                 reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    srv._job_run_single = _done_runner(srv)
    out = await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                             "Murray", "UT")
    assert out.startswith("Report complete — Intermountain Medical Center (Murray, UT)")
    assert "Runs used today: 1 of 20." in out
    assert "AI Visibility Score: 71" in out


async def test_run_report_sweeps_only_its_own_finished_jobs(srv, identity, monkeypatch,
                                                            reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    old = time.time() - 7 * 3600
    srv._jobs.update({
        "his-old": {"status": "done", "started_at": old},
        "mine-old": {"status": "done", "started_at": old, "source": "mcp"},
        "mine-running": {"status": "running", "started_at": old, "source": "mcp"},
        "mine-recent": {"status": "done", "started_at": time.time(), "source": "mcp"},
    })
    srv._job_run_single = _done_runner(srv)
    await mcp_server._pulse_run_report(srv, identity, "Intermountain Medical Center",
                                       "Murray", "UT")
    assert "his-old" in srv._jobs, "a job this module did not create is never touched"
    assert "mine-old" not in srv._jobs
    assert "mine-running" in srv._jobs
    assert "mine-recent" in srv._jobs


# ─────────────────────────────────────────────────────────────────────────────
# 5. pulse_get_report
# ─────────────────────────────────────────────────────────────────────────────

async def test_get_report_reads_score_from_ranked_providers(srv, identity, monkeypatch):
    # The score is NOT in job["result"] for hospital/practice/FQHC runs.
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "AI Visibility Score: 71  (Grade B, Q2 Upper Middle)" in out
    assert "Evidence: high — 312 reviews across 4 locations" in out
    for label, value in (("Outcomes & Safety", 68), ("Credentials & Recognition", 74),
                         ("Experience & Reviews", 70), ("Access & Fit", 73)):
        assert label in out and str(value) in out


async def test_get_report_uses_practice_labels_for_practice_profile(srv, identity, monkeypatch):
    install_fake_db(monkeypatch,
                    [("FROM analysis_runs a",
                      [run_row(weighting_profile="practice_hybrid", entity_type="practice")])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "Practitioner Credentials & Clinical Quality" in out
    assert "Identity & Machine-Readability" in out
    assert "Outcomes & Safety" not in out
    assert "(practice rubric)" in out


async def test_get_report_flags_entity_scores_disagreement(srv, identity, monkeypatch):
    canonical = ("Intermountain Medical Center", 66, TIERS, "B−", "Good", "", "report",
                 "run-0", "2026-09-18", "procedural", "")
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()]),
                                  ("FROM entity_scores", [canonical])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "71" in out and "66" in out
    assert "disagree" in out


async def test_get_report_notes_a_matching_canonical_score(srv, identity, monkeypatch):
    canonical = ("Intermountain Medical Center", 71, TIERS, "B", "Good", "", "report",
                 "run-1", "2026-09-18", "procedural", "")
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()]),
                                  ("FROM entity_scores", [canonical])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "71 (matches)" in out


async def test_get_report_refuses_another_users_run(srv, identity, monkeypatch):
    install_fake_db(monkeypatch,
                    [("FROM analysis_runs a", [run_row(ran_by="other.rep@rldatix.com")])])
    mine = await mcp_server._pulse_get_report(srv, identity, "run-1")
    install_fake_db(monkeypatch, [])
    missing = await mcp_server._pulse_get_report(srv, identity, "run-1")
    # "someone else's" and "does not exist" must read identically.
    assert mine == missing == ("Not found: no run run-1 of yours. pulse_history lists the "
                               "runs you have started.")


async def test_get_report_ownership_is_case_insensitive(srv, identity, monkeypatch):
    install_fake_db(monkeypatch,
                    [("FROM analysis_runs a", [run_row(ran_by="Taylor.Davis@RLDatix.com")])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "AI Visibility Score: 71" in out


async def test_get_report_pdf_link_is_short_lived_and_carries_identity(srv, identity,
                                                                       monkeypatch):
    monkeypatch.setenv("PULSE_MCP_PDF_LINK_TTL_SECONDS", "3600")
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")

    token = out.split("/api/reports/run-1/pdf?token=")[1].split()[0]
    payload = srv._verify_token_full(token)
    assert payload is not None, "the minted token must verify with his own verifier"
    assert payload["email"] == CALLER
    # The minted role must be the run's user_role, or download_pdf's
    # query_history(role) lookup will not find the run.
    assert payload["role"] == "user"
    assert payload["exp"] - int(time.time()) <= 3600 + 5


async def test_pdf_link_token_never_carries_the_mapped_rldatix_group(srv, identity,
                                                                     monkeypatch):
    """The link is not a PDF capability — it is a full Pulse session token that
    require_auth accepts on every REST route. Minting the MAPPED group handed a
    Google-approved account whose Pulse role is "user" a working "salesteam"
    credential, and with it /api/history and every salesteam run's PDF."""
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    for link in [line for line in out.splitlines() if "?token=" in line]:
        payload = srv._verify_token_full(link.split("?token=")[1].strip())
        assert payload["role"] == identity.account_role == "user"
        assert payload["role"] != identity.role


async def test_every_minted_link_carries_the_account_role(srv, identity, monkeypatch,
                                                          reserved):
    """All THREE mint sites, not just _pdf_links: pulse_compare and
    pulse_network_report each call _create_token themselves, and a fix applied to
    one of them is a fix applied to none of them."""
    def roles_in(text):
        return [srv._verify_token_full(part.split("?token=")[1].split()[0])["role"]
                for part in text.splitlines() if "?token=" in part]

    install_fake_db(monkeypatch, [
        ("FROM comparison_runs WHERE id = ?",
         [("run-a", "run-b", "Alpha", "Beta", "/reports/cmp.pdf", CALLER, "2026-09-18")]),
        ("FROM analysis_runs a", [run_row()])])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1"})
    compared = await mcp_server._pulse_compare(srv, identity, "Alpha", "Murray", "UT",
                                               "Beta", "Provo", "UT")
    assert roles_in(compared) == ["user"], compared

    install_fake_db(monkeypatch, [])
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)
    networked = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert roles_in(networked) == ["user"], networked


async def test_pdf_link_ttl_defaults_to_ten_minutes(srv, identity, monkeypatch):
    """The default has to be small because the credential is large. An hour of a
    full session token printed into a chat transcript is the wrong trade."""
    monkeypatch.delenv("PULSE_MCP_PDF_LINK_TTL_SECONDS", raising=False)
    assert mcp_server._pdf_ttl() == 600
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "Download (links expire in 10 minutes):" in out
    token = out.split("/api/reports/run-1/pdf?token=")[1].split()[0]
    assert srv._verify_token_full(token)["exp"] - int(time.time()) <= 600 + 5


async def test_get_report_refuses_a_market_run(srv, identity, monkeypatch):
    """A market report's rank-1 ranked_providers row is the top COMPETITOR and
    analyzer.py writes entity_name = NULL for those runs, so rendering one as a
    Deep Diagnostic reports a rival's score to a rep as their prospect's, under
    the header "Unknown"."""
    install_fake_db(monkeypatch, [("FROM analysis_runs a",
                                   [run_row(individual_report=False, entity_name=None,
                                            ai_visibility_score=88, overall_rating="A")])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert out == ("Not found: run run-1 is a market report, not a single-organization "
                   "report — it scores a whole market and has no one score for an "
                   "organization. Open it in Pulse History for the ranking.")
    assert "88" not in out
    assert "Unknown" not in out


async def test_get_report_omits_links_for_missing_pdf_paths(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [("FROM analysis_runs a",
                                   [run_row(briefing_pdf_path=None, teaser_pdf_path=None)])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "/api/reports/run-1/pdf?token=" in out
    assert "briefing-pdf" not in out
    assert "teaser-pdf" not in out


async def test_get_report_without_a_score_says_so(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [("FROM analysis_runs a",
                                   [run_row(ai_visibility_score=None, tier_scores=None)])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert out == "Not found: run run-1 has no score yet — it may still be finishing."


async def test_get_report_names_a_community_health_run_his_way(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [("FROM analysis_runs a",
                                   [run_row(entity_type="community_health", mqcr=0.62)])])
    out = await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert "Community Health report" in out
    assert "Community Health, Deep Diagnostic" not in out
    assert "Mission Query Capture Rate: 0.62" in out


async def test_fqhc_run_sets_entity_name_on_the_job(srv, identity, monkeypatch, reserved):
    # _job_run_fqhc reads job["entity_name"] when it builds job["result"], so a
    # missing one would give the caller a nameless report.
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    srv._job_run_fqhc = srv.runner("_job_run_fqhc", result={"run_id": "run-1"})
    await mcp_server._pulse_run_report(srv, identity, "Wasatch Community Health", "Murray",
                                       "UT", report_type="fqhc")
    job = list(srv._jobs.values())[0]
    assert job["entity_name"] == "Wasatch Community Health"
    assert job["entity_type"] == "community_health"
    assert job["site_roster"] == []


async def test_get_report_does_not_consume_cap(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    await mcp_server._pulse_get_report(srv, identity, "run-1")
    assert reserved == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. pulse_compare
# ─────────────────────────────────────────────────────────────────────────────

COMPARISON_ROW = ("run-a", "run-b", "Alpha Health", "Beta Health", "/reports/cmp.pdf",
                  CALLER, "2026-09-18")


async def test_compare_reads_both_sides_from_comparison_runs(srv, identity, monkeypatch,
                                                             reserved):
    db = install_fake_db(monkeypatch, [
        ("FROM comparison_runs WHERE id = ?", [COMPARISON_ROW]),
        ("FROM analysis_runs a", [run_row(entity_name="Alpha Health",
                                          ai_visibility_score=71)]),
    ])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1",
                                                            "comparison": True})
    out = await mcp_server._pulse_compare(srv, identity, "Alpha Health", "Murray", "UT",
                                          "Beta Health", "Provo", "UT")
    assert "Alpha Health" in out and "Beta Health" in out
    assert "Score 71" in out
    assert "/api/compare/cmp-1/pdf?token=" in out
    assert db.ran("FROM comparison_runs WHERE id = ?")


async def test_compare_refuses_a_comparison_that_is_not_the_callers(srv, identity, monkeypatch,
                                                                      reserved):
    """_COMPARISON_SQL filters on id alone, unlike _TREND_SQL and
    _HISTORY_COMPARISON_SQL, which both also filter on ran_by. That makes
    _render_comparison the only wall this row has. Neither entity name nor
    score may reach the response when the row belongs to someone else."""
    other_row = ("run-a", "run-b", "Alpha Health", "Beta Health", "/reports/cmp.pdf",
                "someone.else@rldatix.com", "2026-09-18")
    db = install_fake_db(monkeypatch, [("FROM comparison_runs WHERE id = ?", [other_row])])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1"})
    out = await mcp_server._pulse_compare(srv, identity, "Alpha Health", "Murray", "UT",
                                          "Beta Health", "Provo", "UT")
    assert out == mcp_server._not_yours("cmp-1")
    assert "Alpha Health" not in out
    assert "Beta Health" not in out
    assert not any("analysis_runs" in s for s in db.statements), \
        "must refuse before reading either side's own run row"


async def test_compare_tolerates_a_null_side_run_id(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [
        ("FROM comparison_runs WHERE id = ?", [("run-a", None, "Alpha Health",
                                                "Beta Health", None, CALLER, "2026-09-18")]),
        ("FROM analysis_runs a", [run_row(entity_name="Alpha Health")]),
    ])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1"})
    out = await mcp_server._pulse_compare(srv, identity, "Alpha Health", "Murray", "UT",
                                          "Beta Health", "Provo", "UT")
    assert "Beta Health" in out
    assert "no separate run row was saved for this side" in out


async def test_compare_never_bypasses_the_caches(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM comparison_runs WHERE id = ?", [COMPARISON_ROW])])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1"})
    await mcp_server._pulse_compare(srv, identity, "Alpha Health", "Murray", "UT",
                                    "Beta Health", "Provo", "UT")
    _job_id, request = srv.arguments_for("compare")
    assert request["force_rerun_a"] is False
    assert request["force_rerun_b"] is False
    assert request["override_today_lock"] is False
    assert request["entity_a_name"] == "Alpha Health"
    assert request["state_b"] == "UT"


async def test_compare_counts_one_against_the_cap(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM comparison_runs WHERE id = ?", [COMPARISON_ROW])])
    srv._job_run_comparison = srv.runner("compare", result={"run_id": "cmp-1"})
    await mcp_server._pulse_compare(srv, identity, "Alpha Health", "Murray", "UT",
                                    "Beta Health", "Provo", "UT")
    assert len(reserved) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. pulse_network_report
# ─────────────────────────────────────────────────────────────────────────────

NETWORK_RESULT = {"run_id": "net-1", "network_name": "Alpha Health System",
                  "network_canonical_name": "Alpha Health System",
                  "ai_visibility_score": 64, "grade": "B−", "grade_band": "Q3 Lower Middle",
                  "total_hospitals": 12, "states_covered": 3,
                  "pdf_path": "/reports/net.pdf", "teaser_pdf_path": None,
                  "full_detail_pdf_path": None}


async def test_network_report_uses_job_result_scores(srv, identity, monkeypatch, reserved):
    db = install_fake_db(monkeypatch, [])
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)
    out = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert "AI Visibility Score: 64  (Grade B−, Q3 Lower Middle)" in out
    assert "/api/network/net-1/pdf?token=" in out
    assert not db.ran("FROM network_runs"), "a complete job result needs no second read"


async def test_network_report_falls_back_to_the_persisted_row(srv, identity, monkeypatch,
                                                              reserved):
    db = install_fake_db(monkeypatch, [
        ("FROM network_runs WHERE run_id = ?",
         [("Alpha Health System", 64, "B−", 12, "2026-09-18", CALLER, "/reports/net.pdf",
           None, None)])])
    srv._job_network_analyze = srv.runner("network", result={"run_id": "net-1"})
    out = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert "AI Visibility Score: 64" in out
    assert db.ran("FROM network_runs WHERE run_id = ?")


async def test_network_report_refuses_oversized_roster(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [])
    roster = [{"name": f"Site {i}", "city": "Murray", "state": "UT"} for i in range(201)]
    out = await mcp_server._pulse_network_report(srv, identity, "Alpha Health System",
                                                 facilities=roster)
    assert out.startswith("Refused: 201 facilities is more than this tool will score")
    assert reserved == [], "a refused roster must not spend a run"


async def test_network_report_discovers_a_roster_and_says_so(srv, identity, monkeypatch,
                                                             reserved):
    install_fake_db(monkeypatch, [])
    monkeypatch.setattr(
        "perception.network_analyzer.discover_hospitals_by_name",
        lambda name, hq, kind: {"facilities": [{"name": "Alpha Murray", "city": "Murray",
                                                "state": "UT"}],
                                "network_canonical_name": "Alpha Health System",
                                "total_found": 1, "confidence_note": "one source"})
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)
    out = await mcp_server._pulse_network_report(srv, identity, "Alpha Health System")
    assert "Discovered 1 facilities" in out
    assert "one source" in out


async def test_network_report_checks_the_cap_before_discovering_a_roster(
        srv, identity, monkeypatch, reserved):
    """Roster discovery is a real Claude call and, with GEMINI_API_KEY set, a
    real Gemini call. Both used to run BEFORE the cap was consulted, so a caller
    at their limit could bill the shared keys an unbounded number of times and
    get the refusal after the money was spent."""
    install_fake_db(monkeypatch, [])
    discovered = []
    monkeypatch.setattr(
        "perception.network_analyzer.discover_hospitals_by_name",
        lambda name, hq, kind: discovered.append(name) or {"facilities": []})
    monkeypatch.setattr(mcp_usage, "usage_today", lambda email, **kwargs: 20)
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)

    out = await mcp_server._pulse_network_report(srv, identity, "Alpha Health System")
    assert out.startswith("Refused: you have used your daily Pulse limit of 20 report runs.")
    assert discovered == [], "no model call may run once the cap is spent"
    assert reserved == []


async def test_network_report_cap_check_does_not_block_a_supplied_roster(
        srv, identity, monkeypatch, reserved):
    """The pre-check guards the discovery call only. With a roster in hand there
    is nothing to discover, so reserve_run stays the single gate and an
    oversized-roster refusal still costs nothing."""
    install_fake_db(monkeypatch, [])
    monkeypatch.setattr(mcp_usage, "usage_today",
                        lambda email, **kwargs: pytest.fail("usage_today must not be read "
                                                            "when a roster was supplied"))
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)
    out = await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    assert "AI Visibility Score: 64" in out


async def test_network_report_never_ignores_the_cache(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [])
    srv._job_network_analyze = srv.runner("network", result=NETWORK_RESULT)
    await mcp_server._pulse_network_report(
        srv, identity, "Alpha Health System",
        facilities=[{"name": "Alpha Murray", "city": "Murray", "state": "UT"}])
    arguments = srv.arguments_for("network")
    # (job_id, network_name, hq, source_url, facilities, facility_type, brand,
    #  ignore_cache, teaser, service_line_audit, full_detail)
    assert arguments[7:] == (False, False, False, False)


# ─────────────────────────────────────────────────────────────────────────────
# 8. pulse_history
# ─────────────────────────────────────────────────────────────────────────────

HISTORY_ROW = ("run-1", "Intermountain Medical Center", "Murray, UT", "2026-09-18",
               "hospital", None, "/reports/imc.pdf")


async def test_history_filters_on_ran_by(srv, identity, monkeypatch):
    called = []
    monkeypatch.setattr("perception.db.query_history",
                        lambda role: called.append(role) or [])
    db = install_fake_db(monkeypatch, [("FROM analysis_runs", [HISTORY_ROW])])
    out = await mcp_server._pulse_history(srv, identity)
    assert "Intermountain Medical Center" in out
    assert db.statements, "the tool must issue its own SQL"
    for statement in db.statements:
        assert "ran_by" in statement, f"unscoped read: {statement}"
    assert called == [], "query_history filters on user_role and must never be used here"


async def test_history_does_not_offer_market_run_ids(srv, identity, monkeypatch):
    """pulse_history must not hand the model run ids pulse_get_report cannot
    read correctly — a market run has no one score for an organization. His own
    single-entity reads use the same predicate."""
    db = install_fake_db(monkeypatch, [("FROM analysis_runs", [HISTORY_ROW])])
    await mcp_server._pulse_history(srv, identity)
    analysis = [s for s in db.statements if "FROM analysis_runs" in s][0]
    assert "individual_report = TRUE" in " ".join(analysis.split())


async def test_history_excludes_comparison_side_runs(srv, identity, monkeypatch):
    db = install_fake_db(monkeypatch, [("FROM analysis_runs", [HISTORY_ROW])])
    await mcp_server._pulse_history(srv, identity)
    analysis = [s for s in db.statements if "FROM analysis_runs" in s][0]
    assert "comparison_id IS NOT NULL AND pdf_path IS NULL" in " ".join(analysis.split())


async def test_history_wildcard_is_in_the_parameter_not_the_sql(srv, identity, monkeypatch):
    db = install_fake_db(monkeypatch, [("FROM analysis_runs", [HISTORY_ROW])])
    await mcp_server._pulse_history(srv, identity, organization="Intermountain")
    sql, params = [c for c in db.log if "FROM analysis_runs" in c[0]][0]
    # perception.db._translate doubles a literal '%' when params are present.
    assert "%" not in sql
    assert "%Intermountain%" in params


async def test_history_merges_all_three_tables(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [
        ("FROM analysis_runs", [HISTORY_ROW]),
        ("FROM network_runs", [("net-1", "Alpha Health System", "2026-09-19", "hospital", 64)]),
        ("FROM comparison_runs", [("cmp-1", "Alpha Health", "Beta Health", "2026-09-17")]),
    ])
    out = await mcp_server._pulse_history(srv, identity)
    lines = [line for line in out.splitlines() if line.strip()][1:]
    assert lines[0].startswith("2026-09-19"), "newest first"
    assert "Alpha Health System" in lines[0]
    assert "Intermountain Medical Center" in lines[1]
    assert "Alpha Health vs Beta Health" in lines[2]


async def test_history_empty_message(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_history(srv, identity, days=7)
    assert out == "No runs of yours in the last 7 days. pulse_run_report starts one."


async def test_history_bounds_days_and_limit(srv, identity, monkeypatch):
    db = install_fake_db(monkeypatch, [("FROM analysis_runs", [HISTORY_ROW])])
    await mcp_server._pulse_history(srv, identity, days=5000, limit=9999)
    _sql, params = [c for c in db.log if "FROM analysis_runs" in c[0]][0]
    assert params[-1] == 100


async def test_history_non_numeric_days_is_a_refusal_not_an_exception(srv, identity, monkeypatch):
    """days and limit are string-typed on the MCP wire; a non-numeric value
    used to reach bare int(days or 45) and raise ValueError past the _tool
    boundary. It must come back as a refusal string instead."""
    install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_history(srv, identity, days="a week")
    assert out == "Refused: days must be a whole number."


async def test_history_non_numeric_limit_is_a_refusal_not_an_exception(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_history(srv, identity, limit="all of them")
    assert out == "Refused: limit must be a whole number."


# ─────────────────────────────────────────────────────────────────────────────
# 9. pulse_trend
# ─────────────────────────────────────────────────────────────────────────────

def trend_row(run_id="run-1", day="2026-06-02", score=67):
    footprint = json.dumps({"front_door": {"rating": 4.1, "count": 2431}})
    return (run_id, day, score, TIERS, footprint, "Murray, UT", "procedural")


async def test_trend_filters_on_ran_by(srv, identity, monkeypatch):
    called = []
    monkeypatch.setattr("perception.db.get_entity_trend",
                        lambda name: called.append(name) or [])
    db = install_fake_db(monkeypatch, [("FROM analysis_runs a", [trend_row(),
                                                                 trend_row("run-2",
                                                                           "2026-09-18", 71)])])
    out = await mcp_server._pulse_trend(srv, identity, "Intermountain Medical Center")
    for statement in db.statements:
        assert "ran_by" in statement, f"unscoped read: {statement}"
    # get_entity_trend has no per-user filter at all.
    assert called == []
    assert "Score moved +4 across 2 snapshots since 2026-06-02." in out
    assert "Google front door" in out


async def test_trend_single_snapshot_message(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [trend_row()])])
    out = await mcp_server._pulse_trend(srv, identity, "Intermountain Medical Center")
    assert "Only one snapshot of yours for this organization, so there is no trend yet." in out


async def test_trend_no_snapshots_message(srv, identity, monkeypatch):
    install_fake_db(monkeypatch, [])
    out = await mcp_server._pulse_trend(srv, identity, "Intermountain Medical Center")
    assert out.startswith('No snapshots of yours for "Intermountain Medical Center".')


async def test_trend_city_filter_is_a_parameter(srv, identity, monkeypatch):
    db = install_fake_db(monkeypatch, [("FROM analysis_runs a", [trend_row()])])
    await mcp_server._pulse_trend(srv, identity, "Intermountain Medical Center", city="Murray")
    sql, params = db.log[0]
    assert "%" not in sql
    assert "%Murray%" in params


# ─────────────────────────────────────────────────────────────────────────────
# 10. pulse_content_draft
# ─────────────────────────────────────────────────────────────────────────────

SAVED_FINDINGS = {
    "run_id": "run-1", "norm_entity": "intermountain medical center",
    "status": "verified",
    "source_snapshot": {"website_urls": ["https://example.org"], "wikidata_qid": "Q1",
                        "wikipedia_article": "Intermountain Medical Center"},
    "findings": [{"finding_id": "CIK-001", "platform": "wikidata",
                  "remediation_type": "wikidata_edit", "status": "verified"}],
}


async def test_content_draft_keeps_verify_placeholders_literal(srv, identity, monkeypatch,
                                                               reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    monkeypatch.setattr("perception.db.get_content_findings",
                        lambda run_id: json.loads(json.dumps(SAVED_FINDINGS)))
    saved = {}
    monkeypatch.setattr(
        "perception.db.save_content_findings",
        lambda run_id, norm, snap, findings, status: saved.update(
            run_id=run_id, norm=norm, snap=snap, findings=findings, status=status))
    monkeypatch.setattr("perception.content_drafting.draft_findings",
                        lambda name, loc, kind, facts, findings: {
                            "CIK-001": "Set P1082 to [VERIFY: bed count] on the Wikidata item."})

    out = await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert "[VERIFY: bed count]" in out
    assert "a fact a human must confirm before publishing" in out
    assert saved["findings"][0]["draft_content"] == (
        "Set P1082 to [VERIFY: bed count] on the Wikidata item.")
    assert saved["run_id"] == "run-1"
    assert saved["norm"] == "intermountain medical center"
    assert "Content Analysis panel" in out


async def test_content_draft_refuses_without_saved_findings(srv, identity, monkeypatch,
                                                            reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    monkeypatch.setattr("perception.db.get_content_findings", lambda run_id: None)
    out = await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert out.startswith("Refused: run run-1 has no saved content findings.")
    assert "attach_to_run_id=run-1" in out
    assert reserved == [], "a refusal before drafting must not spend a run"


async def test_content_draft_refuses_when_nothing_is_draftable(srv, identity, monkeypatch,
                                                               reserved):
    """draft_findings returns {} with ZERO model calls when no finding has a
    remediation_type and a status other than not_assessed. Reporting that as
    "the model call failed, try again" is false, and it sends the rep back to a
    call that will fail identically forever, one cap unit each time."""
    findings = {**SAVED_FINDINGS,
                "findings": [{"finding_id": "CIK-001", "platform": "wikidata",
                              "remediation_type": "", "status": "verified"},
                             {"finding_id": "CIK-002", "platform": "website",
                              "remediation_type": "website_fix", "status": "not_assessed"}]}
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    monkeypatch.setattr("perception.db.get_content_findings",
                        lambda run_id: json.loads(json.dumps(findings)))
    drafted = []
    monkeypatch.setattr("perception.content_drafting.draft_findings",
                        lambda *args, **kwargs: drafted.append(args) or {})

    out = await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert out == ("Refused: run run-1 has 2 saved content findings but none of them are "
                   "draftable — they are informational or not_assessed. Nothing to write.")
    assert reserved == [], "a futile call must not spend a run"
    assert drafted == [], "and must not reach the drafter at all"


async def test_content_draft_consumes_cap(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    monkeypatch.setattr("perception.db.get_content_findings",
                        lambda run_id: json.loads(json.dumps(SAVED_FINDINGS)))
    monkeypatch.setattr("perception.db.save_content_findings",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr("perception.content_drafting.draft_findings",
                        lambda *args, **kwargs: {"CIK-001": "draft"})
    await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert reserved == [(CALLER, 20)]


async def test_content_draft_refuses_another_users_run(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch,
                    [("FROM analysis_runs a", [run_row(ran_by="other@rldatix.com")])])
    out = await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert out == ("Not found: no run run-1 of yours. pulse_history lists the runs you have "
                   "started.")


async def test_content_draft_reports_a_failed_model_call(srv, identity, monkeypatch, reserved):
    install_fake_db(monkeypatch, [("FROM analysis_runs a", [run_row()])])
    monkeypatch.setattr("perception.db.get_content_findings",
                        lambda run_id: json.loads(json.dumps(SAVED_FINDINGS)))
    written = []
    monkeypatch.setattr("perception.db.save_content_findings",
                        lambda *args, **kwargs: written.append(args))
    monkeypatch.setattr("perception.content_drafting.draft_findings",
                        lambda *args, **kwargs: {})
    out = await mcp_server._pulse_content_draft(srv, identity, "run-1")
    assert out.startswith("Refused: drafting produced no content")
    assert written == [], "nothing may be saved when drafting produced nothing"
