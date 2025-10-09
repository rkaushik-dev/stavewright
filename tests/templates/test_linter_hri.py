from __future__ import annotations

import pytest

from v4t_setup.finding import Severity
from v4t_setup.gs1.ai import FNC1
from v4t_setup.gs1.hri import MAX_HRI_CHARACTERS
from v4t_setup.templates.linter import (
    MIN_ENCODED_RUN,
    HumanReadableRule,
    TemplateContext,
    lint_template,
)
from v4t_setup.templates.spf import MAGIC, SpfBlock, SpfTemplate


def u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def block(name: str, *strings: str, offset: int = 0) -> SpfBlock:
    payload = b"\x01\x02".join(u16(text) for text in strings)
    return SpfBlock(name=name, offset=offset, payload=payload)


def text_template(*literals: str) -> SpfTemplate:
    blocks = [block("TEMPLATE")]
    blocks.extend(
        block(f"TEXTLABEL_{n}", f"F:Arial,12,0,0,S:{n},#{literal}")
        for n, literal in enumerate(literals, start=1)
    )
    return SpfTemplate(magic=MAGIC, blocks=blocks, size=1024)


def run(*literals: str):
    return HumanReadableRule().check(text_template(*literals), TemplateContext())


def messages(findings) -> str:
    return " | ".join(finding.message for finding in findings)


def test_ordinary_printed_text_passes():
    assert run("Cognex Code", "Best before", "(01)01001000173250") == []


def test_a_template_with_no_text_blocks_passes():
    only_barcode = SpfTemplate(
        magic=MAGIC,
        blocks=[block("TEMPLATE"), block("BARCODELABEL_1", "GS1 DataMatrix")],
        size=1024,
    )
    assert HumanReadableRule().check(only_barcode, TemplateContext()) == []


def test_a_bound_block_is_left_alone():
    # No literal, so the linter cannot see what it will print.
    bound = SpfTemplate(
        magic=MAGIC,
        blocks=[block("TEMPLATE"), block("TEXTLABEL", "F:Arial,12,0,0,S:3,SERIAL")],
        size=1024,
    )
    assert HumanReadableRule().check(bound, TemplateContext()) == []


@pytest.mark.parametrize("character", [FNC1, "\x00", "\r", "\t"])
def test_a_control_character_never_reaches_the_rule(character):
    # The block reader hands back printable runs only, so the separator has
    # already cut the literal in two. Nothing here can see it, which is why
    # the rule does not pretend to check for it.
    findings = run(f"AD47-001{character}0001")
    assert findings == []


def test_the_block_index_travels_with_the_finding():
    findings = run("ok", "9" * 20)
    assert findings[0].context["index"] == 2


def test_text_over_the_readable_limit_is_a_warning():
    findings = run("B" * (MAX_HRI_CHARACTERS + 1))
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["length"] == MAX_HRI_CHARACTERS + 1
    assert findings[0].context["limit"] == MAX_HRI_CHARACTERS


def test_text_exactly_at_the_limit_passes():
    assert run("B" * MAX_HRI_CHARACTERS) == []


def test_a_long_digit_run_reads_as_the_encoded_form():
    findings = run("0" * MIN_ENCODED_RUN)
    warning = next(f for f in findings if "encoded form" in f.message)
    assert warning.severity is Severity.WARNING
    assert warning.context["text"] == "0" * MIN_ENCODED_RUN


def test_the_real_case_that_prompted_the_rule():
    findings = run("0100125682945601210001")
    assert "no bracketed AI" in messages(findings)


def test_a_short_digit_run_is_left_alone():
    # Batch codes and weights are printed bare and always will be.
    assert run("9" * (MIN_ENCODED_RUN - 1)) == []


def test_a_bracketed_element_string_is_not_flagged():
    assert run("(01)00125682945601(21)0001") == []


def test_digits_mixed_with_text_are_not_flagged():
    assert run("LOT 0100125682945601") == []


def test_two_problems_on_one_literal_both_report():
    findings = run("1" * 60)
    assert "too long to read" in messages(findings)
    assert "encoded form" in messages(findings)


def test_the_rule_runs_as_part_of_the_default_set():
    findings = lint_template(text_template("0100125682945601210001"))
    assert "human-readable" in [finding.rule for finding in findings]


def test_the_shipped_template_has_no_human_readable_defects(request):
    from v4t_setup.templates.spf import read_spf

    shipped = read_spf(
        request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "T1.spf"
    )
    assert HumanReadableRule().check(shipped, TemplateContext()) == []
