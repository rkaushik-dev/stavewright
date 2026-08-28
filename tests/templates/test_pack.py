from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from stavewright.errors import TemplateError
from stavewright.finding import Severity
from stavewright.model.profile import TerminalProfile
from stavewright.templates.pack import (
    TemplateSet,
    lint_template_set,
    read_template_set,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED = REPO_ROOT / "Matricode" / "Quattro" / "Templates"


@pytest.fixture
def shipped():
    return read_template_set(SHIPPED)


@pytest.fixture
def copied(tmp_path):
    """The shipped set, copied so a test can add to it."""
    root = tmp_path / "Templates"
    shutil.copytree(SHIPPED, root)
    return root


def rules(findings) -> set[str]:
    return {finding.rule for finding in findings}


def test_reading_the_shipped_set(shipped):
    assert shipped.names == ["T1"]
    assert shipped.data_path.name == "GS1-1.txt"


def test_the_set_describes_itself(shipped):
    assert shipped.describe() == "Templates: 1 template, GS1-1.txt"


def test_several_templates_pluralise(copied):
    shutil.copy(copied / "T1.spf", copied / "T2.spf")
    assert "2 templates" in read_template_set(copied).describe()


def test_length_and_membership(shipped):
    assert len(shipped) == 1
    assert "T1" in shipped
    assert "T9" not in shipped


def test_iterating_yields_entries(shipped):
    assert [entry.name for entry in shipped] == ["T1"]


def test_an_entry_carries_its_path_and_template(shipped):
    entry = shipped.get("T1")
    assert entry.path.name == "T1.spf"
    assert len(entry.template) > 1


def test_an_entry_describes_itself(shipped):
    assert shipped.get("T1").describe().startswith("T1 (4 blocks,")


def test_asking_for_a_template_that_is_not_there(shipped):
    with pytest.raises(TemplateError, match="no such template") as caught:
        shipped.get("T9")
    assert caught.value.context["known"] == ["T1"]


def test_the_fonts_the_set_needs(shipped):
    assert shipped.fonts == frozenset({"Arial"})


def test_the_columns_come_from_the_data_file(shipped):
    assert "SERIAL" in shipped.columns
    assert "GTIN" in shipped.columns


def test_the_bound_columns_come_from_the_templates(shipped):
    assert shipped.get("T1").bound_columns


def test_the_longest_element_string(shipped):
    assert shipped.longest_element_string() == 28


def test_a_set_with_no_data_file_has_no_columns(copied):
    (copied / "GS1-1.txt").unlink()
    templates = read_template_set(copied)
    assert templates.columns == frozenset()
    assert templates.longest_element_string() is None


def test_naming_the_data_file(copied):
    templates = read_template_set(copied, data_file=copied / "GS1-1.txt")
    assert templates.data_path.name == "GS1-1.txt"


def test_two_candidate_data_files_are_refused(copied):
    shutil.copy(copied / "GS1-1.txt", copied / "GS1-2.csv")
    with pytest.raises(TemplateError, match="more than one file here") as caught:
        read_template_set(copied)
    assert caught.value.context["candidates"] == ["GS1-1.txt", "GS1-2.csv"]


def test_naming_one_settles_it(copied):
    shutil.copy(copied / "GS1-1.txt", copied / "GS1-2.csv")
    templates = read_template_set(copied, data_file=copied / "GS1-2.csv")
    assert templates.data_path.name == "GS1-2.csv"


def test_a_directory_that_is_not_there(tmp_path):
    with pytest.raises(TemplateError, match="does not exist"):
        read_template_set(tmp_path / "nowhere")


def test_a_directory_with_no_templates(tmp_path):
    (tmp_path / "GS1-1.txt").write_text("INDEX\n1\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="no templates in this directory"):
        read_template_set(tmp_path)


def test_a_file_that_is_not_a_template_is_left_out(copied):
    (copied / "notes.spf").write_bytes(b"this is not a template")
    assert read_template_set(copied).names == ["T1"]


def test_linting_the_shipped_set_finds_what_it_always_finds(shipped):
    findings = lint_template_set(shipped)
    assert "capacity" in rules(findings)
    assert "bound-field" in rules(findings)
    assert "placeholder" in rules(findings)


def test_every_template_finding_names_the_template(shipped):
    findings = lint_template_set(shipped)
    per_template = [f for f in findings if f.rule != "template-set"]
    assert all(finding.subject == "T1" for finding in per_template)


def test_the_capacity_rule_only_fires_because_the_data_file_is_there(copied):
    (copied / "GS1-1.txt").unlink()
    findings = lint_template_set(read_template_set(copied))
    assert "capacity" not in rules(findings)


def test_the_bound_field_rule_only_fires_because_the_data_file_is_there(copied):
    (copied / "GS1-1.txt").unlink()
    findings = lint_template_set(read_template_set(copied))
    assert "bound-field" not in rules(findings)


def test_a_set_with_no_data_file_says_nothing_checked_the_bindings(copied):
    (copied / "GS1-1.txt").unlink()
    findings = lint_template_set(read_template_set(copied))
    warning = next(f for f in findings if "nothing checked the bindings" in f.message)
    assert warning.severity is Severity.WARNING


def test_unused_columns_are_reported_as_information(shipped):
    findings = lint_template_set(shipped)
    note = next(f for f in findings if "columns no template prints" in f.message)
    assert note.severity is Severity.INFO
    assert note.context["columns"]


def test_two_templates_differing_only_in_case(shipped):
    # Built by hand rather than on disk. NTFS folds the two names together, so
    # a bench laptop cannot make this directory, and a set that came off a Unix
    # build server or out of a zip can.
    entry = shipped.get("T1")
    both = TemplateSet(
        root=shipped.root,
        entries=(entry, replace(entry, name="t1")),
        dataset=shipped.dataset,
        data_path=shipped.data_path,
    )
    findings = lint_template_set(both)
    clash = next(finding for finding in findings if "only in case" in finding.message)
    assert clash.severity is Severity.ERROR
    assert clash.subject == "t1"
    assert clash.context["other"] == "T1"


def test_names_that_differ_by_more_than_case_are_fine(shipped):
    entry = shipped.get("T1")
    both = TemplateSet(root=shipped.root, entries=(entry, replace(entry, name="T2")))
    assert not any("only in case" in finding.message for finding in lint_template_set(both))


def test_the_profile_reaches_the_capacity_rule(shipped):
    profile = TerminalProfile(terminal_id="LINE-PC-04", fpga_revision="PCDC-1.4")
    with_profile = lint_template_set(shipped, profile)
    assert len(with_profile) >= len(lint_template_set(shipped))


def test_a_font_the_terminal_does_not_have(shipped):
    findings = lint_template_set(shipped, available_fonts=frozenset({"OCR-B"}))
    fonts = [f for f in findings if f.rule == "font"]
    assert fonts
    assert "Arial" in fonts[0].context["font"]


def test_the_shipped_fonts_are_accepted_by_default(shipped):
    assert "font" not in rules(lint_template_set(shipped))


def test_an_empty_set_has_no_fonts():
    assert TemplateSet(root=SHIPPED).fonts == frozenset()


def test_an_empty_set_lints_to_one_warning():
    findings = lint_template_set(TemplateSet(root=SHIPPED))
    assert [finding.rule for finding in findings] == ["template-set"]
