from __future__ import annotations

import json
from pathlib import Path

import pytest

from stavewright.report.builders.quality import template_set_report
from stavewright.report.document import Outcome
from stavewright.report.render import to_json, to_text
from stavewright.templates.pack import TemplateSet, lint_template_set, read_template_set

SHIPPED = Path(__file__).resolve().parents[2] / "Matricode" / "Quattro" / "Templates"


@pytest.fixture
def templates():
    return read_template_set(SHIPPED)


def section(report, heading):
    return next(part for part in report.sections if part.heading == heading)


def test_the_report_describes_the_set(templates):
    report = template_set_report(templates, [])
    assert report.title == "Template set"
    assert report.subtitle == "Templates: 1 template, GS1-1.txt"


def test_the_facts_carry_the_data_file(templates):
    facts = template_set_report(templates, []).fact_lines()
    assert facts["data_file"] == "GS1-1.txt"
    assert facts["templates"] == "1"
    assert int(facts["rows"]) > 0


def test_the_facts_carry_the_longest_element_string(templates):
    assert template_set_report(templates, []).fact_lines()["longest_element_string"] == "28"


def test_a_set_with_no_data_file_leaves_those_facts_blank(templates):
    bare = TemplateSet(root=templates.root, entries=templates.entries)
    facts = template_set_report(bare, []).fact_lines()
    assert facts["data_file"] == ""
    assert facts["rows"] == "0"
    assert facts["longest_element_string"] == ""


def test_the_table_has_a_row_per_template(templates):
    rows = section(template_set_report(templates, []), "Templates").rows
    assert [row[0] for row in rows] == ["T1"]


def test_the_table_carries_the_fonts(templates):
    rows = section(template_set_report(templates, []), "Templates").rows
    assert rows[0][3] == "Arial"


def test_the_table_counts_the_findings_per_template(templates):
    findings = lint_template_set(templates)
    rows = section(template_set_report(templates, findings), "Templates").rows
    per_template = [f for f in findings if f.subject == "T1"]
    assert rows[0][4] == str(len(per_template))


def test_a_clean_set_shows_zero_findings(templates):
    rows = section(template_set_report(templates, []), "Templates").rows
    assert rows[0][4] == "0"


def test_the_fonts_section_is_there(templates):
    lines = section(template_set_report(templates, []), "Fonts the terminal needs").lines
    assert lines == ["Arial"]


def test_a_set_that_names_no_font_has_no_such_section(templates):
    empty = TemplateSet(root=templates.root)
    headings = [part.heading for part in template_set_report(empty, []).sections]
    assert "Fonts the terminal needs" not in headings


def test_a_clean_set_passes(templates):
    assert template_set_report(templates, []).outcome is Outcome.PASS


def test_the_shipped_set_fails_because_it_has_real_findings(templates):
    report = template_set_report(templates, lint_template_set(templates))
    assert report.outcome is Outcome.FAIL


def test_the_findings_section_is_only_there_when_there_are_some(templates):
    headings = [part.heading for part in template_set_report(templates, []).sections]
    assert "Findings" not in headings


def test_the_report_renders_as_text(templates):
    text = to_text(template_set_report(templates, lint_template_set(templates)))
    assert "Template set" in text
    assert "T1" in text


def test_the_report_renders_as_json(templates):
    payload = json.loads(to_json(template_set_report(templates, [])))
    assert payload["facts"]["templates"] == "1"
    assert payload["sections"][0]["columns"][0] == "template"
