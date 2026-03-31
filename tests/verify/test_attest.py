from __future__ import annotations

import json

import pytest

from v4t_setup.apply.executor import ApplyResult, Outcome, StepResult
from v4t_setup.clock import frozen, utc
from v4t_setup.errors import VerifyError
from v4t_setup.finding import Severity
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.steps import Plan, Step, StepKind
from v4t_setup.verify.attest import (
    SCHEMA,
    Attestation,
    attest,
    check_attestation,
    loads,
    read_attestation,
)
from v4t_setup.verify.drift import DriftKind, DriftRecord, DriftReport

PROFILE = TerminalProfile(terminal_id="LINE-PC-04", fpga_revision="PCDC-1.4")
WHEN = utc(2025, 6, 3, 1, 30)


def plan() -> Plan:
    return Plan(
        profile=PROFILE,
        steps=[
            Step(step_id="dir.appdir", kind=StepKind.CREATE_DIRECTORY, title="make APPDIR"),
            Step(step_id="file.v4t", kind=StepKind.COPY_FILE, title="copy V4T.exe"),
            Step(step_id="reg.version", kind=StepKind.WRITE_REGISTRY, title="write version"),
        ],
        created_at=utc(2025, 6, 3, 1, 0),
    )


def result(outcome: Outcome = Outcome.COMPLETE) -> ApplyResult:
    return ApplyResult(
        outcome=outcome,
        run_id="run-0001",
        applied=[
            StepResult(step_id="dir.appdir", changed=True),
            StepResult(step_id="file.v4t", changed=True),
            StepResult(step_id="reg.version", changed=False, skipped=True),
        ],
    )


def clean_drift() -> DriftReport:
    return DriftReport(terminal="LINE-PC-04", plan_digest=str(plan().digest()), checked=3)


def dirty_drift() -> DriftReport:
    return DriftReport(
        terminal="LINE-PC-04",
        plan_digest=str(plan().digest()),
        checked=3,
        records=[
            DriftRecord(kind=DriftKind.MISSING, subject=r"C:\V4T.exe", step_id="file.v4t")
        ],
    )


def made(**kwargs) -> Attestation:
    with frozen(WHEN):
        return attest(plan(), result(), **kwargs)


def test_it_names_the_terminal_and_the_run():
    entry = made()
    assert entry.terminal == "LINE-PC-04"
    assert entry.run_id == "run-0001"


def test_it_carries_the_plan_digest():
    assert made().plan_digest == str(plan().digest())


def test_it_counts_what_was_applied_rather_than_what_was_planned():
    entry = made()
    assert entry.steps_applied == 2
    assert entry.steps_skipped == 1
    assert entry.changed_steps == ("dir.appdir", "file.v4t")


def test_it_takes_the_start_from_the_plan_and_the_finish_from_the_clock():
    entry = made()
    assert entry.started_at == utc(2025, 6, 3, 1, 0)
    assert entry.finished_at == WHEN


def test_without_a_reconcile_it_says_it_does_not_know():
    entry = made()
    assert entry.verified is None
    assert entry.drift_records == 0


def test_a_clean_reconcile_makes_it_verified():
    entry = made(drift=clean_drift())
    assert entry.verified is True
    assert entry.is_auditable


def test_a_dirty_reconcile_makes_it_not_verified():
    entry = made(drift=dirty_drift())
    assert entry.verified is False
    assert entry.drift_records == 1
    assert not entry.is_auditable


def test_a_run_that_did_not_finish_is_not_auditable():
    with frozen(WHEN):
        entry = attest(plan(), result(Outcome.FAILED), clean_drift())
    assert not entry.is_complete
    assert not entry.is_auditable


def test_notes_travel():
    assert made(notes="witnessed by QA").notes == "witnessed by QA"


def test_the_canonical_form_is_stable():
    assert made().canonical() == made().canonical()


def test_the_digest_is_stable():
    assert made().digest() == made().digest()


def test_the_canonical_form_has_no_whitespace():
    assert " " not in made().canonical().replace("witnessed by QA", "")


def test_a_different_run_digests_differently():
    with frozen(WHEN):
        changed = attest(plan(), ApplyResult(outcome=Outcome.COMPLETE, run_id="run-0002"))
    assert changed.digest() != made().digest()


def test_it_round_trips_through_json():
    entry = made(drift=clean_drift(), notes="witnessed by QA")
    assert loads(entry.dumps()) == entry


def test_the_written_form_carries_its_digest():
    payload = json.loads(made().dumps())
    assert payload["digest"] == str(made().digest())
    assert payload["schema"] == SCHEMA


def test_a_tampered_attestation_is_refused():
    payload = json.loads(made().dumps())
    payload["steps_applied"] = 99
    with pytest.raises(VerifyError, match="does not match its own digest") as caught:
        loads(json.dumps(payload))
    assert caught.value.context["stated"] != caught.value.context["found"]


def test_an_attestation_with_no_digest_is_still_readable():
    # Somebody may have pretty printed it by hand. Without a stated digest
    # there is nothing to disagree with.
    payload = json.loads(made().dumps())
    del payload["digest"]
    assert loads(json.dumps(payload)).run_id == "run-0001"


def test_something_that_is_not_json():
    with pytest.raises(VerifyError, match="not valid JSON"):
        loads("{")


def test_something_that_is_not_an_attestation():
    with pytest.raises(VerifyError, match="not an attestation"):
        loads(json.dumps({"schema": "something/else"}))


def test_a_json_list_is_not_an_attestation():
    with pytest.raises(VerifyError, match="not an attestation"):
        loads("[]")


def test_a_timestamp_that_is_not_one():
    payload = json.loads(made().dumps())
    payload["finished_at"] = "the day before yesterday"
    with pytest.raises(VerifyError, match="not a date and time"):
        loads(json.dumps(payload))


def test_reading_one_from_disk(tmp_path):
    path = tmp_path / "attestation.json"
    made().write(path)
    assert read_attestation(path).run_id == "run-0001"


def test_reading_one_that_is_not_there(tmp_path):
    with pytest.raises(VerifyError, match="cannot read attestation"):
        read_attestation(tmp_path / "gone.json")


def test_a_sound_attestation_has_one_warning_and_no_errors():
    findings = check_attestation(made(drift=clean_drift()), plan())
    assert findings == []


def test_an_unreconciled_run_is_a_warning():
    findings = check_attestation(made())
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert "nobody reconciled" in findings[0].message


def test_a_drifted_terminal_is_an_error():
    findings = check_attestation(made(drift=dirty_drift()))
    error = next(f for f in findings if f.subject == "verified")
    assert error.severity is Severity.ERROR
    assert error.context["records"] == 1


def test_a_run_that_did_not_finish_is_an_error():
    with frozen(WHEN):
        entry = attest(plan(), result(Outcome.FAILED), clean_drift())
    findings = check_attestation(entry)
    assert any("did not finish" in finding.message for finding in findings)


def test_an_attestation_for_another_plan():
    other = Plan(profile=PROFILE, steps=[], created_at=utc(2025, 1, 1))
    findings = check_attestation(made(drift=clean_drift()), other)
    error = next(f for f in findings if f.subject == "plan_digest")
    assert error.severity is Severity.ERROR
    assert error.context["plan"] == str(other.digest())


def test_the_plan_is_not_checked_when_it_is_not_supplied():
    assert check_attestation(made(drift=clean_drift())) == []


def test_an_attestation_with_no_run_id():
    entry = Attestation(terminal="X", run_id="", plan_digest="d", outcome="complete")
    assert "names no run" in " ".join(f.message for f in check_attestation(entry))


def test_it_describes_itself():
    assert made(drift=clean_drift()).describe().startswith(
        "LINE-PC-04 complete, verified, 2025-06-03, run run-0001"
    )


def test_an_unreconciled_one_says_so_in_its_description():
    assert "never reconciled" in made().describe()


def test_the_toolkit_version_is_recorded():
    from v4t_setup.__about__ import __version__

    assert made().toolkit_version == __version__
