from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from v4t_setup.__about__ import __version__
from v4t_setup.cli.common import EXIT_FINDINGS, EXIT_OK, EXIT_TOOLKIT
from v4t_setup.cli.main import app

from .conftest import AIP, GS1_DATA, TEMPLATE, json_output


def run(runner: CliRunner, *args: str):
    return runner.invoke(app, ["-q", *args])


def test_version(runner):
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == EXIT_OK
    assert f"v4t-setup {__version__}" in result.output


def test_help_lists_the_groups(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == EXIT_OK
    for group in ("bundle", "run", "gs1", "template"):
        assert group in result.output


def test_no_arguments_shows_help(runner):
    result = runner.invoke(app, [])
    assert "Usage" in result.output


def test_checks_lists_the_preflight_checks(runner, bench):
    result = run(runner, "checks")
    assert result.exit_code == EXIT_OK
    assert "free-space:" in result.output
    assert "elevation:" in result.output


def test_formats_lists_the_renderers(runner, bench):
    result = run(runner, "formats")
    assert result.exit_code == EXIT_OK
    assert set(result.output.split()) == {"csv", "json", "junit", "markdown", "text"}


def test_an_unknown_format_is_a_toolkit_error(runner, bench):
    result = run(runner, "gs1", "identifiers", "--format", "yaml")
    assert result.exit_code == EXIT_TOOLKIT
    assert "unknown format" in result.output


def test_gs1_identifiers(runner, bench):
    result = run(runner, "gs1", "identifiers", "--format", "json")
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    rows = payload["sections"][0]["rows"]
    assert any(row[0] == "01" for row in rows)


def test_gs1_parse_a_good_element_string(runner, bench):
    result = run(runner, "gs1", "parse", "(01)01001000173250(21)0001")
    assert result.exit_code == EXIT_OK
    assert "GTIN" in result.output


def test_gs1_parse_the_shipped_bad_one(runner, bench):
    result = run(runner, "gs1", "parse", "(01)00125682945601(21)0001")
    assert result.exit_code == EXIT_FINDINGS
    assert "check-digit" in result.output


def test_gs1_parse_reports_the_encoded_length(runner, bench):
    result = run(runner, "gs1", "parse", "(01)01001000173250(21)0001", "--format", "json")
    assert json_output(result)["facts"]["encoded_length"] == "22"


def test_gs1_parse_with_a_length_limit(runner, bench):
    result = run(
        runner, "gs1", "parse", "(01)01001000173250(21)0001", "--max-length", "10"
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "encoded-length" in result.output


def test_gs1_parse_of_nonsense_is_a_toolkit_error(runner, bench):
    result = run(runner, "gs1", "parse", "not an element string")
    assert result.exit_code == EXIT_TOOLKIT
    assert "GS1" in result.output


def test_gs1_check_on_the_shipped_file(runner, bench):
    result = run(runner, "gs1", "check", str(GS1_DATA), "--format", "json")
    assert result.exit_code == EXIT_FINDINGS
    payload = json_output(result)
    assert payload["facts"]["rows"] == "130"
    assert payload["counts"]["error"] > 0


def test_gs1_check_writes_to_a_file(runner, bench):
    out = bench / "gs1.json"
    result = run(runner, "gs1", "check", str(GS1_DATA), "--format", "json", "--output", str(out))
    assert result.exit_code == EXIT_FINDINGS
    assert json.loads(out.read_text(encoding="utf-8"))["title"] == "GS1 data"


def test_gs1_check_of_a_missing_file(runner, bench):
    result = run(runner, "gs1", "check", "nope.txt")
    assert result.exit_code == EXIT_TOOLKIT
    assert "does not exist" in result.output


def test_gs1_check_of_a_directory(runner, bench):
    result = run(runner, "gs1", "check", str(bench))
    assert result.exit_code == EXIT_TOOLKIT
    assert "expected a file" in result.output


def test_template_show(runner, bench):
    result = run(runner, "template", "show", str(TEMPLATE), "--format", "json")
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    blocks = next(s for s in payload["sections"] if s["heading"] == "Blocks")
    assert len(blocks["rows"]) == 4


def test_template_show_lists_the_fields(runner, bench):
    result = run(runner, "template", "show", str(TEMPLATE), "--format", "json")
    payload = json_output(result)
    fields = next(s for s in payload["sections"] if s["heading"] == "Fields")
    assert any("Arial" in row for row in fields["rows"])


def test_template_lint(runner, bench):
    result = run(runner, "template", "lint", str(TEMPLATE))
    assert result.exit_code == EXIT_OK
    assert "placeholder" in result.output


def test_template_lint_against_the_dataset_columns(runner, bench):
    result = run(runner, "template", "lint", str(TEMPLATE), "--with-dataset")
    assert result.exit_code == EXIT_FINDINGS
    assert "bound-field" in result.output


def test_template_lint_set(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE.parent), "--format", "json")
    payload = json_output(result)
    assert payload["facts"]["templates"] == "1"


def test_template_lint_set_of_a_directory_with_no_templates(runner, bench):
    # An empty report used to come back, which reads as a clean set. It is
    # almost always the wrong directory.
    result = run(runner, "template", "lint-set", str(bench), "--format", "json")
    assert result.exit_code == EXIT_TOOLKIT
    assert "no templates in this directory" in result.output


def test_template_lint_set_finds_the_data_file_beside_the_templates(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE.parent), "--format", "json")
    facts = json_output(result)["facts"]
    assert facts["data_file"] == "GS1-1.txt"
    assert facts["longest_element_string"] == "28"


def test_template_lint_set_reports_the_fonts(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE.parent))
    assert "Fonts the terminal needs" in result.output
    assert "Arial" in result.output


def test_template_lint_set_with_a_named_data_file(runner, bench):
    result = run(
        runner, "template", "lint-set", str(TEMPLATE.parent),
        "--data", str(GS1_DATA), "--format", "json",
    )
    assert json_output(result)["facts"]["data_file"] == "GS1-1.txt"


def test_template_lint_set_with_a_data_file_that_is_not_there(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE.parent), "--data", "gone.txt")
    assert result.exit_code == EXIT_TOOLKIT
    assert "data file does not exist" in result.output


def test_template_lint_set_with_a_profile(runner, bench, profile_file):
    result = run(
        runner, "template", "lint-set", str(TEMPLATE.parent),
        "--profile", str(profile_file), "--format", "json",
    )
    assert json_output(result)["facts"]["templates"] == "1"


def test_template_lint_set_with_a_profile_that_is_not_there(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE.parent), "--profile", "gone.toml")
    assert result.exit_code == EXIT_TOOLKIT
    assert "terminal profile does not exist" in result.output


def test_template_lint_set_of_a_file(runner, bench):
    result = run(runner, "template", "lint-set", str(TEMPLATE))
    assert result.exit_code == EXIT_TOOLKIT
    assert "expected a directory" in result.output


def test_bundle_inspect_the_shipped_project(runner, bench):
    result = run(runner, "bundle", "inspect", str(AIP), "--format", "json")
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    rows = dict(payload["sections"][0]["rows"])
    assert rows["components"] == "58"
    assert rows["files"] == "35"


def test_bundle_new_writes_a_manifest(runner, bench):
    out = bench / "fresh.json"
    result = run(runner, "bundle", "new", "--out", str(out), "--notes", "bench build")
    assert result.exit_code == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["notes"] == "bench build"


def test_bundle_new_refuses_to_overwrite(runner, bench, manifest_file):
    result = run(runner, "bundle", "new", "--out", str(manifest_file))
    assert result.exit_code == EXIT_TOOLKIT
    assert "refusing to overwrite" in result.output


def test_bundle_keygen_writes_both_halves(runner, bench):
    private = bench / "bench.pem"
    result = run(runner, "bundle", "keygen", "--out", str(private))
    assert result.exit_code == EXIT_OK
    assert private.is_file()
    assert (bench / "bench.pub.pem").is_file()
    assert "key id" in result.output


def test_bundle_keygen_refuses_to_overwrite(runner, bench):
    private = bench / "bench.pem"
    run(runner, "bundle", "keygen", "--out", str(private))
    result = run(runner, "bundle", "keygen", "--out", str(private))
    assert result.exit_code == EXIT_TOOLKIT


def test_sign_and_verify_round_trip(runner, bench, manifest_file):
    private = bench / "bench.pem"
    run(runner, "bundle", "keygen", "--out", str(private))
    keys = bench / "keys"
    keys.mkdir()
    (keys / "bench.pem").write_bytes((bench / "bench.pub.pem").read_bytes())

    signed = run(runner, "bundle", "sign", str(manifest_file), "--key", str(private))
    assert signed.exit_code == EXIT_OK

    verified = run(runner, "bundle", "verify", str(manifest_file), "--keys", str(keys))
    assert verified.exit_code == EXIT_OK
    assert "verifies against key" in verified.output


def test_verify_catches_a_tampered_manifest(runner, bench, manifest_file):
    private = bench / "bench.pem"
    run(runner, "bundle", "keygen", "--out", str(private))
    keys = bench / "keys"
    keys.mkdir()
    (keys / "bench.pem").write_bytes((bench / "bench.pub.pem").read_bytes())
    run(runner, "bundle", "sign", str(manifest_file), "--key", str(private))

    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    payload["notes"] = "edited after signing"
    manifest_file.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    result = run(runner, "bundle", "verify", str(manifest_file), "--keys", str(keys))
    assert result.exit_code == EXIT_TOOLKIT
    assert "does not match its signature" in result.output


def test_verify_without_a_key_directory(runner, bench, manifest_file):
    result = run(runner, "bundle", "verify", str(manifest_file))
    assert result.exit_code == EXIT_TOOLKIT
    assert "no trusted key directory" in result.output


def test_verify_with_an_empty_key_directory(runner, bench, manifest_file):
    keys = bench / "keys"
    keys.mkdir()
    result = run(runner, "bundle", "verify", str(manifest_file), "--keys", str(keys))
    assert result.exit_code == EXIT_TOOLKIT
    assert "no PEM public keys" in result.output


def test_bundle_stage(runner, bench, manifest_file, payload_root):
    result = run(
        runner, "bundle", "stage", str(manifest_file), "--source", str(payload_root),
        "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    # Two of the three items have payload. The directory item has nothing to
    # stage and shows up under "Nothing to stage" instead.
    assert payload["facts"]["items"] == "2"
    assert any(s["heading"] == "Nothing to stage" for s in payload["sections"])


def test_bundle_stage_requires_a_signature_when_configured(
    runner, bench, manifest_file, payload_root
):
    (bench / "v4t-setup.toml").write_text("require_signature = true\n", encoding="utf-8")
    result = run(
        runner, "bundle", "stage", str(manifest_file), "--source", str(payload_root)
    )
    assert result.exit_code == EXIT_TOOLKIT
    assert "signatures are required" in result.output


def test_run_plan(runner, bench, manifest_file, profile_file):
    result = run(
        runner, "run", "plan", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    assert payload["facts"]["terminal"] == "LINE-PC-04"
    assert payload["facts"]["steps"] == "3"


def test_run_plan_without_a_project_file(runner, bench, manifest_file, profile_file):
    result = run(runner, "run", "plan", str(manifest_file), "--profile", str(profile_file))
    assert result.exit_code == EXIT_TOOLKIT
    assert "no project file" in result.output


def test_run_preflight_offline(runner, bench, manifest_file, profile_file):
    result = run(
        runner, "run", "preflight", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline", "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    assert json_output(result)["outcome"] in {"pass", "warn"}


def test_run_apply_dry(runner, bench, staged_bundle, profile_file):
    result = run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run", "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    assert payload["facts"]["outcome"] == "complete"


def test_run_apply_writes_a_journal(runner, bench, staged_bundle, profile_file):
    run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
    )
    journals = list((bench / "_journal").glob("run-*.jsonl"))
    assert len(journals) == 1


def test_run_verify_against_an_empty_machine(runner, bench, staged_bundle, profile_file):
    result = run(
        runner, "run", "verify", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline", "--format", "json",
    )
    assert result.exit_code == EXIT_FINDINGS
    assert json_output(result)["counts"]["error"] == 3


def test_run_verify_of_something_that_is_not_a_bundle(runner, bench, profile_file):
    result = run(
        runner, "run", "verify", str(bench),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline",
    )
    assert result.exit_code == EXIT_TOOLKIT
    assert "no manifest" in result.output


def test_run_profiles_lists_a_file(runner, bench, profile_file):
    result = run(runner, "run", "profiles", str(profile_file))
    assert result.exit_code == EXIT_OK
    assert "LINE-PC-04" in result.output


def test_run_profiles_lists_a_directory(runner, bench, profile_file):
    result = run(runner, "run", "profiles", str(bench))
    assert result.exit_code == EXIT_OK
    assert "LINE-PC-04" in result.output


def test_run_profiles_of_a_missing_path(runner, bench):
    result = run(runner, "run", "profiles", "nowhere")
    assert result.exit_code == EXIT_TOOLKIT


def test_a_named_settings_file_is_used(runner, bench, manifest_file, payload_root):
    other = bench / "other.toml"
    other.write_text('staging_root = "elsewhere"\nrequire_signature = false\n', encoding="utf-8")
    result = runner.invoke(
        app,
        ["-q", "--config", str(other), "bundle", "stage", str(manifest_file),
         "--source", str(payload_root)],
    )
    assert result.exit_code == EXIT_OK
    assert (bench / "elsewhere").is_dir()


def test_an_unreadable_settings_file_is_a_toolkit_error(runner, bench):
    result = runner.invoke(app, ["-q", "--config", "nope.toml", "checks"])
    assert result.exit_code == EXIT_TOOLKIT
    assert "cannot read configuration file" in result.output


def test_a_bad_log_level_is_a_toolkit_error(runner, bench):
    result = runner.invoke(app, ["-q", "--log-level", "CHATTY", "checks"])
    assert result.exit_code == EXIT_TOOLKIT


@pytest.mark.parametrize("fmt", ["text", "json", "csv", "junit"])
def test_every_format_works_on_a_real_command(runner, bench, fmt):
    result = run(runner, "gs1", "check", str(GS1_DATA), "--format", fmt)
    assert result.exit_code == EXIT_FINDINGS
    assert result.stdout.strip()


def test_bundle_build_refuses_a_manifest_with_holes(runner, bench):
    out = bench / "built.json"
    result = run(runner, "bundle", "build", str(AIP), "--out", str(out))
    assert result.exit_code == EXIT_TOOLKIT
    assert "payload sources are missing" in result.output
    assert not out.exists()


def test_bundle_build_writes_one_when_told_to(runner, bench):
    out = bench / "built.json"
    result = run(runner, "bundle", "build", str(AIP), "--out", str(out), "--allow-missing")
    assert result.exit_code == EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 58


def test_bundle_build_without_digests_is_complete(runner, bench):
    out = bench / "built.json"
    result = run(runner, "bundle", "build", str(AIP), "--out", str(out), "--no-digest")
    assert result.exit_code == EXIT_OK
    assert "0 items digested" in result.output


def test_bundle_build_refuses_to_overwrite(runner, bench, manifest_file):
    result = run(runner, "bundle", "build", str(AIP), "--out", str(manifest_file), "--no-digest")
    assert result.exit_code == EXIT_TOOLKIT
    assert "refusing to overwrite" in result.output


def test_a_built_manifest_signs_stages_and_plans(runner, bench, profile_file):
    out = bench / "built.json"
    assert run(
        runner, "bundle", "build", str(AIP), "--out", str(out), "--no-digest"
    ).exit_code == EXIT_OK

    private = bench / "bench.pem"
    run(runner, "bundle", "keygen", "--out", str(private))
    assert run(runner, "bundle", "sign", str(out), "--key", str(private)).exit_code == EXIT_OK

    planned = run(
        runner, "run", "plan", str(out),
        "--profile", str(profile_file), "--aip", str(AIP), "--format", "json",
    )
    assert planned.exit_code == EXIT_OK
    assert int(json.loads(planned.stdout)["facts"]["steps"]) > 40


def test_run_diff_of_a_manifest_against_itself(runner, bench, manifest_file, profile_file):
    result = run(
        runner, "run", "diff", str(manifest_file), str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    section = next(s for s in payload["sections"] if s["heading"] == "Difference")
    assert section["lines"] == ["the two plans are the same"]


def test_run_diff_reports_a_removed_item(runner, bench, manifest_file, profile_file):
    trimmed = bench / "trimmed.json"
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    payload["items"] = [i for i in payload["items"] if i["item_id"] != "file.imager"]
    trimmed.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    result = run(
        runner, "run", "diff", str(manifest_file), str(trimmed),
        "--profile", str(profile_file), "--aip", str(AIP), "--format", "json",
    )
    assert result.exit_code == EXIT_OK
    rows = next(
        s for s in json_output(result)["sections"] if s["heading"] == "Difference"
    )["rows"]
    assert any(row[0] == "file.imager" and row[1] == "removed" for row in rows)


def test_run_diff_says_when_nothing_would_change(runner, bench, manifest_file, profile_file):
    result = run(
        runner, "run", "diff", str(manifest_file), str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP),
    )
    assert "nothing on this terminal would change" in result.output


def test_run_history_on_an_empty_directory(runner, bench):
    (bench / "_journal").mkdir()
    result = run(runner, "run", "history", "--format", "json")
    assert result.exit_code == EXIT_OK
    assert json_output(result)["facts"]["runs"] == "0"


def test_run_history_after_an_apply(runner, bench, staged_bundle, profile_file):
    run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
    )
    result = run(runner, "run", "history", "--format", "json")
    assert result.exit_code == EXIT_OK
    payload = json_output(result)
    assert payload["facts"]["runs"] == "1"
    rows = next(s for s in payload["sections"] if s["heading"] == "Runs")["rows"]
    assert rows[0][1] == "LINE-PC-04"
    assert rows[0][3] == "complete"


def test_run_history_can_be_narrowed_to_one_terminal(runner, bench, staged_bundle, profile_file):
    run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
    )
    result = run(runner, "run", "history", "--terminal", "LINE-PC-99", "--format", "json")
    assert json_output(result)["facts"]["runs"] == "0"


def test_run_history_of_a_directory_that_is_not_there(runner, bench):
    result = run(runner, "run", "history", "--journals", "nowhere")
    assert result.exit_code == EXIT_TOOLKIT
    assert "does not exist" in result.output


def test_preflight_reports_a_run_that_was_never_finished(
    runner, bench, manifest_file, profile_file
):
    from v4t_setup import clock
    from v4t_setup.apply.journal import Journal, RecordKind

    journals = bench / "_journal"
    journals.mkdir(exist_ok=True)
    with clock.frozen(clock.utc(2026, 2, 3, 9, 0)), Journal(
        journals / "run-0001aaaa.jsonl"
    ) as journal:
        journal.begin("sha256:abc", "LINE-PC-04", "0001aaaa")
        journal.append(RecordKind.STEP_OK, step_id="dir.appdir")

    result = run(
        runner, "run", "preflight", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline",
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "never finished" in result.output


def test_preflight_without_templates_says_nothing_about_fonts(
    runner, bench, manifest_file, profile_file
):
    result = run(
        runner, "run", "preflight", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline", "--format", "json",
    )
    findings = [
        finding
        for section in json_output(result)["sections"]
        for finding in section.get("findings", [])
    ]
    assert not any(finding["rule"] == "template-font" for finding in findings)


def test_preflight_with_templates_checks_their_fonts(
    runner, bench, manifest_file, profile_file
):
    # Arial is on any Windows box but not on the empty machine --offline uses,
    # and the bundle installs no font, so the check has something to say.
    result = run(
        runner, "run", "preflight", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline",
        "--templates", str(TEMPLATE.parent), "--format", "json",
    )
    findings = [
        finding
        for section in json_output(result)["sections"]
        for finding in section.get("findings", [])
    ]
    fonts = [finding for finding in findings if finding["rule"] == "template-font"]
    assert fonts
    assert fonts[0]["context"]["fonts"] == ["Arial"]


def test_preflight_with_a_template_directory_that_is_not_there(
    runner, bench, manifest_file, profile_file
):
    result = run(
        runner, "run", "preflight", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP), "--offline",
        "--templates", "nowhere",
    )
    assert result.exit_code == EXIT_TOOLKIT
    assert "template directory does not exist" in result.output


def test_apply_takes_the_same_option(runner, bench, staged_bundle, profile_file):
    # A missing font is a warning, so the run goes ahead. It still has to
    # reach the operator, which it does on stderr rather than as a second
    # document on stdout.
    result = run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
        "--templates", str(TEMPLATE.parent),
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "preflight warning:" in result.output
    assert "Arial" in result.output


def test_apply_without_templates_warns_about_no_fonts(
    runner, bench, staged_bundle, profile_file
):
    result = run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "Arial" not in result.output


def test_a_preflight_warning_carries_its_detail(runner, bench, staged_bundle, profile_file):
    result = run(
        runner, "run", "apply", str(staged_bundle),
        "--profile", str(profile_file), "--aip", str(AIP), "--dry-run",
        "--templates", str(TEMPLATE.parent),
    )
    assert "fonts=['Arial']" in result.output


MSI = AIP.parent / "msi-quattro" / "msi-Jet600.msi"


def test_bundle_msi_reads_the_shipped_installer(runner, bench):
    result = run(runner, "bundle", "msi", str(MSI), "--format", "json")
    assert result.exit_code == EXIT_OK, result.output
    facts = json_output(result)["facts"]
    assert facts["product"] == "msi-Jet600"
    assert facts["version"] == "1.0.0"
    assert facts["codepage"] == "1252"


def test_bundle_msi_lists_the_tables(runner, bench):
    result = run(runner, "bundle", "msi", str(MSI), "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    assert len(rows) == 32
    assert ["Directory", "539", "3", "Directory"] in rows


def test_bundle_msi_of_something_that_is_not_one(runner, bench):
    result = run(runner, "bundle", "msi", str(AIP))
    assert result.exit_code == EXIT_TOOLKIT
    assert "signature" in result.output


def test_bundle_msi_of_a_missing_file(runner, bench):
    result = run(runner, "bundle", "msi", "gone.msi")
    assert result.exit_code == EXIT_TOOLKIT
    assert "installer does not exist" in result.output


def test_bundle_match_says_the_shipped_pair_do_not_match(runner, bench):
    result = run(runner, "bundle", "match", str(MSI), str(AIP))
    assert result.exit_code == EXIT_FINDINGS
    assert "UpgradeCode does not match" in result.output


def test_bundle_match_shows_the_table_counts(runner, bench):
    result = run(runner, "bundle", "match", str(MSI), str(AIP), "--format", "json")
    rows = json_output(result)["sections"][0]["rows"]
    directory = next(row for row in rows if row[0] == "Directory")
    assert directory[1] == "38"
    assert directory[2] == "539"


def test_bundle_match_writes_to_a_file(runner, bench):
    out = bench / "match.json"
    result = run(
        runner, "bundle", "match", str(MSI), str(AIP), "--format", "json", "--output", str(out)
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "Installer against project" in out.read_text(encoding="utf-8")


def test_bundle_match_with_a_missing_project_file(runner, bench):
    result = run(runner, "bundle", "match", str(MSI), "gone.aip")
    assert result.exit_code == EXIT_TOOLKIT
    assert "project file does not exist" in result.output


def test_bundle_msi_reports_the_package_code(runner, bench):
    result = run(runner, "bundle", "msi", str(MSI), "--format", "json")
    facts = json_output(result)["facts"]
    assert facts["package_code"] == "{345299C7-6DE9-4E9E-AFCD-D230D2F0F642}"
    assert facts["platform"] == "Intel"
    assert facts["schema"] == "200"


def test_bundle_msi_of_a_sound_installer_passes(runner, bench):
    # The shipped one is internally consistent, whatever else is wrong with it.
    result = run(runner, "bundle", "msi", str(MSI))
    assert result.exit_code == EXIT_OK
    assert "Findings" not in result.output


def test_rules_lists_every_rule_group(runner, bench):
    result = run(runner, "rules")
    assert result.exit_code == EXIT_OK
    for group in ("gs1:", "gs1 data file:", "template:", "preflight:"):
        assert group in result.output


def test_rules_names_rules_that_exist(runner, bench):
    result = run(runner, "rules")
    assert "gs1: check-digit" in result.output
    assert "template: human-readable" in result.output
    assert "preflight: template-font" in result.output


def test_every_rule_the_command_lists_is_unique(runner, bench):
    result = run(runner, "rules")
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == len(set(lines))


def test_the_gs1_check_only_reports_rules_the_command_lists(runner, bench):
    listed = {
        line.split(": ", 1)[1]
        for line in run(runner, "rules").output.splitlines()
        if line.strip()
    }
    result = run(runner, "gs1", "check", str(GS1_DATA), "--format", "json")
    reported = {
        finding["rule"]
        for section in json_output(result)["sections"]
        for finding in section.get("findings", [])
    }
    assert reported <= listed, sorted(reported - listed)


GOOD_SSCC = "006141411234567890"


def test_gs1_labels_lists_the_kinds(runner, bench):
    result = run(runner, "gs1", "labels")
    assert result.exit_code == EXIT_OK
    for kind in ("item:", "case:", "pallet:"):
        assert kind in result.output


def test_gs1_parse_without_a_label_says_nothing_about_one(runner, bench):
    result = run(runner, "gs1", "parse", "(01)01001000173250", "--format", "json")
    assert result.exit_code == EXIT_OK
    assert "label" not in json_output(result)["facts"]


def test_gs1_parse_as_an_item_mark(runner, bench):
    # Legal as an element string, useless as an item mark: nothing can be
    # recalled by a GTIN on its own.
    result = run(runner, "gs1", "parse", "(01)01001000173250", "--label", "item")
    assert result.exit_code == EXIT_FINDINGS
    assert "to tell one from another" in result.output


def test_a_good_item_mark_passes_as_one(runner, bench):
    result = run(
        runner, "gs1", "parse", "(01)01001000173250(21)0001", "--label", "item", "--format", "json"
    )
    assert result.exit_code == EXIT_OK
    assert json_output(result)["facts"]["label"] == "item"


def test_gs1_parse_as_a_pallet_label(runner, bench):
    result = run(
        runner, "gs1", "parse", f"(00){GOOD_SSCC}(02)01001000173250(37)48",
        "--label", "pallet",
    )
    assert result.exit_code == EXIT_OK, result.output


def test_a_pallet_label_with_a_serial_on_it(runner, bench):
    result = run(
        runner, "gs1", "parse", f"(00){GOOD_SSCC}(21)0001", "--label", "pallet"
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "does not belong on a pallet label" in result.output


def test_a_customer_requirement_can_be_added(runner, bench):
    result = run(
        runner, "gs1", "parse", "(01)01001000173250(21)0001",
        "--label", "item", "--require", "17",
    )
    assert result.exit_code == EXIT_FINDINGS
    assert "has to carry an USE BY OR EXPIRY" in result.output


def test_a_label_kind_that_is_not_one(runner, bench):
    result = run(runner, "gs1", "parse", "(01)01001000173250", "--label", "crate")
    assert result.exit_code == EXIT_TOOLKIT
    assert "expected one of item, case, pallet" in result.output


def test_the_rules_command_lists_the_label_rule(runner, bench):
    assert "gs1 label: label" in run(runner, "rules").output


def test_apply_writes_an_attestation(runner, bench, staged_bundle, profile_file):
    out = bench / "attestation.json"
    result = run(
        runner, "run", "apply", str(staged_bundle), "--profile", str(profile_file),
        "--aip", str(AIP), "--dry-run", "--attest", str(out),
    )
    assert result.exit_code == EXIT_OK, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["terminal"] == "LINE-PC-04"
    assert payload["outcome"] == "complete"
    assert payload["digest"]


def test_the_attestation_records_the_reconcile(runner, bench, staged_bundle, profile_file):
    out = bench / "attestation.json"
    run(
        runner, "run", "apply", str(staged_bundle), "--profile", str(profile_file),
        "--aip", str(AIP), "--dry-run", "--attest", str(out),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    # A dry run applies to a scratch machine and then reconciles against it,
    # so it comes back verified rather than "never reconciled".
    assert payload["verified"] is True


def test_apply_says_where_it_put_the_attestation(runner, bench, staged_bundle, profile_file):
    out = bench / "attestation.json"
    result = run(
        runner, "run", "apply", str(staged_bundle), "--profile", str(profile_file),
        "--aip", str(AIP), "--dry-run", "--attest", str(out),
    )
    assert "attestation sha" in result.output


def test_apply_without_the_flag_writes_none(runner, bench, staged_bundle, profile_file):
    result = run(
        runner, "run", "apply", str(staged_bundle), "--profile", str(profile_file),
        "--aip", str(AIP), "--dry-run",
    )
    assert result.exit_code == EXIT_OK
    assert not (bench / "attestation.json").exists()


@pytest.fixture
def attestation(runner, bench, staged_bundle, profile_file):
    out = bench / "attestation.json"
    result = runner.invoke(
        app,
        [
            "-q", "run", "apply", str(staged_bundle), "--profile", str(profile_file),
            "--aip", str(AIP), "--dry-run", "--attest", str(out),
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    return out


def test_auditing_a_sound_attestation(runner, bench, attestation):
    result = run(runner, "run", "audit", str(attestation))
    assert result.exit_code == EXIT_OK, result.output
    assert "Commissioning attestation" in result.output
    assert "reconciled afterwards and matched" in result.output


def test_the_audit_shows_the_digest(runner, bench, attestation):
    result = run(runner, "run", "audit", str(attestation), "--format", "json")
    stated = json.loads(attestation.read_text(encoding="utf-8"))["digest"]
    assert json_output(result)["facts"]["digest"] == stated


def test_auditing_a_tampered_attestation(runner, bench, attestation):
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    payload["steps_applied"] = 99
    attestation.write_text(json.dumps(payload), encoding="utf-8")
    result = run(runner, "run", "audit", str(attestation))
    assert result.exit_code == EXIT_TOOLKIT
    assert "does not match its own digest" in result.output


def test_auditing_something_that_is_not_an_attestation(runner, bench):
    result = run(runner, "run", "audit", str(AIP))
    assert result.exit_code == EXIT_TOOLKIT
    assert "not valid JSON" in result.output


def test_auditing_a_file_that_is_not_there(runner, bench):
    result = run(runner, "run", "audit", "gone.json")
    assert result.exit_code == EXIT_TOOLKIT
    assert "attestation does not exist" in result.output


def test_auditing_against_the_bundle_it_came_from(
    runner, bench, attestation, manifest_file, profile_file
):
    result = run(
        runner, "run", "audit", str(attestation), "--manifest", str(manifest_file),
        "--profile", str(profile_file), "--aip", str(AIP),
    )
    assert result.exit_code == EXIT_OK, result.output


def test_a_manifest_without_a_profile_is_refused(runner, bench, attestation, manifest_file):
    result = run(runner, "run", "audit", str(attestation), "--manifest", str(manifest_file))
    assert result.exit_code == EXIT_TOOLKIT
    assert "needs --profile" in result.output


def test_journals_says_what_it_would_remove(runner, bench, attestation):
    result = run(runner, "run", "journals", "--format", "json")
    assert result.exit_code == EXIT_OK, result.output
    facts = json_output(result)["facts"]
    assert facts["journals"] == "1"
    assert facts["removing"] == "0"
    assert facts["latest"] == "1"


def test_journals_keeps_the_only_run_however_short_the_window(runner, bench, attestation):
    result = run(
        runner, "run", "journals", "--keep-days", "0", "--keep-runs", "0", "--format", "json"
    )
    assert json_output(result)["facts"]["removing"] == "0"


def test_journals_removes_nothing_without_the_flag(runner, bench, attestation):
    before = list((bench / "_journal").iterdir())
    run(runner, "run", "journals", "--keep-days", "0", "--keep-runs", "0")
    assert list((bench / "_journal").iterdir()) == before


def test_journals_says_how_many_it_removed(runner, bench, attestation):
    result = run(runner, "run", "journals", "--remove")
    assert result.exit_code == EXIT_OK
    assert "removed 0 journals" in result.output


def test_journals_of_a_directory_that_is_not_there(runner, bench):
    result = run(runner, "run", "journals", "--journals", "nowhere")
    assert result.exit_code == EXIT_TOOLKIT
    assert "journal directory does not exist" in result.output


def test_journals_of_an_empty_directory(runner, bench):
    empty = bench / "_journal"
    empty.mkdir(exist_ok=True)
    result = run(runner, "run", "journals", "--format", "json")
    assert result.exit_code == EXIT_OK
    assert json_output(result)["facts"]["journals"] == "0"
