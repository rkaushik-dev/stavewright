from __future__ import annotations

import pytest

from stavewright.finding import Severity
from stavewright.gs1.label import (
    RULE,
    RULES,
    SSCC_LENGTH,
    LabelKind,
    LabelSpec,
    check_label,
    check_labels,
    check_sscc,
    describe_rules,
)
from stavewright.gs1.parse import parse

# A real SSCC: 17 digits and the mod 10 check digit that follows from them.
GOOD_SSCC = "006141411234567890"

ITEM = "(01)01001000173250(21)0001"
CASE = "(01)01001000173250(10)AD47-001(30)12"
PALLET = f"(00){GOOD_SSCC}(02)01001000173250(37)48"


def spec(kind: LabelKind, text: str, **kwargs) -> LabelSpec:
    return LabelSpec(kind=kind, string=parse(text), **kwargs)


def messages(findings) -> str:
    return " | ".join(finding.message for finding in findings)


def test_the_sscc_used_in_these_tests_is_a_real_one():
    assert check_sscc(GOOD_SSCC) is None


def test_an_sscc_of_the_wrong_length():
    assert "18 digits, this one is 3" in check_sscc("123")


def test_an_sscc_with_a_letter_in_it():
    assert check_sscc("00614141123456789A") == "an SSCC is digits only"


def test_an_sscc_with_the_wrong_check_digit():
    wrong = GOOD_SSCC[:-1] + ("0" if GOOD_SSCC[-1] != "0" else "1")
    assert "check digit should be" in check_sscc(wrong)


def test_an_empty_sscc():
    assert check_sscc("") is not None


def test_the_length_is_the_documented_one():
    assert SSCC_LENGTH == 18


def test_a_good_item_mark_passes():
    assert check_label(spec(LabelKind.ITEM, ITEM)) == []


def test_a_good_case_label_passes():
    assert check_label(spec(LabelKind.CASE, CASE)) == []


def test_a_good_pallet_label_passes():
    assert check_label(spec(LabelKind.PALLET, PALLET)) == []


def test_an_item_mark_with_only_a_gtin():
    findings = check_label(spec(LabelKind.ITEM, "(01)01001000173250"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert "to tell one from another" in findings[0].message


def test_a_batch_is_enough_to_tell_items_apart():
    assert check_label(spec(LabelKind.ITEM, "(01)01001000173250(10)AD47-001")) == []


def test_an_item_mark_with_no_gtin():
    findings = check_label(spec(LabelKind.ITEM, "(21)0001"))
    assert "has to carry a GTIN" in messages(findings)


def test_an_sscc_on_an_item_mark():
    findings = check_label(spec(LabelKind.ITEM, f"(01)01001000173250(21)1(00){GOOD_SSCC}"))
    assert "An SSCC does not belong on an item label" in messages(findings)


def test_a_count_on_an_item_mark():
    findings = check_label(spec(LabelKind.ITEM, "(01)01001000173250(21)1(30)12"))
    assert "does not belong on an item label" in messages(findings)


def test_a_case_label_with_no_batch():
    findings = check_label(spec(LabelKind.CASE, "(01)01001000173250(30)12"))
    error = next(f for f in findings if "has to carry" in f.message)
    assert error.severity is Severity.ERROR
    assert error.context["ai"] == "10"


def test_a_case_label_with_no_count_is_only_a_warning():
    findings = check_label(spec(LabelKind.CASE, "(01)01001000173250(10)AD47-001"))
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert "normally carries" in findings[0].message


def test_a_pallet_label_with_no_sscc():
    findings = check_label(spec(LabelKind.PALLET, "(02)01001000173250(37)48"))
    error = next(f for f in findings if "has to carry" in f.message)
    assert error.severity is Severity.ERROR
    assert "an SSCC" in error.message


def test_a_serial_on_a_pallet_label():
    findings = check_label(spec(LabelKind.PALLET, f"(00){GOOD_SSCC}(21)0001"))
    assert "A SERIAL does not belong on a pallet label" in messages(findings)


def test_a_pallet_label_with_a_broken_sscc():
    wrong = GOOD_SSCC[:-1] + ("0" if GOOD_SSCC[-1] != "0" else "1")
    findings = check_label(spec(LabelKind.PALLET, f"(00){wrong}(02)01001000173250(37)48"))
    error = next(f for f in findings if "not usable" in f.message)
    assert error.severity is Severity.ERROR
    assert error.context["value"] == wrong


def test_a_missing_content_gtin_on_a_pallet_is_only_a_warning():
    findings = check_label(spec(LabelKind.PALLET, f"(00){GOOD_SSCC}(37)48"))
    assert [finding.severity for finding in findings] == [Severity.WARNING]
    assert "normally carries a CONTENT" in findings[0].message


def test_a_trade_item_gtin_does_not_belong_on_a_pallet():
    # (00) and (01) are both primary identifiers, so this breaks the element
    # string rules as well. The table said (01) for a day and the validator
    # was what noticed.
    findings = check_label(spec(LabelKind.PALLET, f"(00){GOOD_SSCC}(01)01001000173250"))
    assert "A GTIN does not belong on a pallet label" in messages(findings)


def test_the_trade_item_count_does_not_satisfy_a_pallet():
    findings = check_label(spec(LabelKind.PALLET, f"(00){GOOD_SSCC}(02)01001000173250(30)48"))
    assert "normally carries a COUNT" in messages(findings)


def test_a_customer_can_insist_on_more():
    findings = check_label(spec(LabelKind.ITEM, ITEM, extra_required=("17",)))
    assert "has to carry an USE BY OR EXPIRY" in messages(findings)


def test_the_extra_requirement_is_satisfied_when_it_is_there():
    label = spec(LabelKind.ITEM, "(01)01001000173250(21)1(17)261231", extra_required=("17",))
    assert check_label(label) == []


def test_a_finding_carries_the_reference_when_there_is_one():
    findings = check_label(spec(LabelKind.PALLET, "(02)01001000173250", reference="T3.spf"))
    assert all(finding.subject == "T3.spf" for finding in findings)


def test_a_finding_falls_back_to_the_label_kind():
    findings = check_label(spec(LabelKind.PALLET, "(02)01001000173250"))
    assert all(finding.subject == "pallet" for finding in findings)


def test_every_finding_carries_the_rule_and_the_kind():
    findings = check_label(spec(LabelKind.PALLET, "(02)01001000173250"))
    assert all(finding.rule == RULE for finding in findings)
    assert all(finding.context["label"] == "pallet" for finding in findings)


def test_checking_several_labels_at_once():
    findings = check_labels(
        [
            spec(LabelKind.ITEM, ITEM),
            spec(LabelKind.PALLET, "(02)01001000173250"),
        ]
    )
    assert findings
    assert all(finding.context["label"] == "pallet" for finding in findings)


def test_checking_nothing():
    assert check_labels([]) == []


@pytest.mark.parametrize("kind", list(LabelKind))
def test_every_kind_has_rules(kind):
    assert RULES[kind].kind is kind


def test_no_ai_is_both_required_and_forbidden():
    for rules in RULES.values():
        assert not set(rules.required) & set(rules.forbidden)


def test_no_ai_is_both_expected_and_forbidden():
    for rules in RULES.values():
        assert not set(rules.expected) & set(rules.forbidden)


def test_the_rules_describe_themselves():
    lines = describe_rules()
    assert len(lines) == len(LabelKind)
    assert lines[0].startswith("item: needs GTIN")
    assert "an SSCC" not in lines[0]


def test_the_descriptions_read_with_the_right_articles():
    text = "\n".join(describe_rules())
    assert "an SERIAL" not in text
    assert "a SERIAL" in text


def test_the_canonical_pallet_label_passes_the_element_string_rules_too():
    # (00) and (02) are both primary identifiers and the validator refused the
    # pair until this module was written. A pallet label that the label rules
    # accept and the element string rules refuse would be useless.
    from stavewright.gs1.validate import validate

    assert validate(parse(PALLET)) == []
    assert check_label(spec(LabelKind.PALLET, PALLET)) == []


def test_two_primary_identifiers_that_are_not_that_pair_are_still_refused():
    from stavewright.gs1.validate import validate

    findings = validate(parse("(01)01001000173250(02)01001000173250"))
    assert any("more than one primary identifier" in f.message for f in findings)


def row(item: str, *, case: str = "", index: int = 1) -> str:
    """One data file row: NDX, SERIAL, BATCH, LOTCODE, GTIN, ADX, GS1 DATA, GS1, EAN13."""
    return ",".join(
        [str(index), "0001", "AD47-001", "AB:47", "01001000173250", "A", item, case, ""]
    )


def dataset_of(*rows: str):
    from stavewright.gs1.dataset import EXPECTED_COLUMNS, parse_dataset

    header = ",".join(EXPECTED_COLUMNS)
    return parse_dataset("\n".join([header, *rows]) + "\n")


def test_a_data_file_whose_rows_are_good_item_marks():
    from stavewright.gs1.label import check_dataset

    data = dataset_of(row("(01)01001000173250(21)0001"))
    assert check_dataset(data) == []


def test_a_data_file_whose_rows_have_no_serial():
    from stavewright.gs1.label import check_dataset

    data = dataset_of(row("(01)01001000173250"))
    findings = check_dataset(data)
    assert findings
    assert "to tell one from another" in findings[0].message
    assert findings[0].subject == "line 2"


def test_the_row_check_stops_after_the_limit():
    from stavewright.gs1.label import check_dataset

    rows = [row("(01)01001000173250", index=n) for n in range(20)]
    findings = check_dataset(dataset_of(*rows), limit=3)
    stop = next(f for f in findings if "stopped after" in f.message)
    assert stop.context["rows_checked"] == 3


def test_no_limit_checks_every_row():
    from stavewright.gs1.label import check_dataset

    rows = [row("(01)01001000173250", index=n) for n in range(8)]
    findings = check_dataset(dataset_of(*rows), limit=0)
    assert len(findings) == 8
    assert not any("stopped after" in f.message for f in findings)


def test_a_row_that_will_not_parse_is_left_to_the_dataset_rules():
    # Saying it twice in one report helps nobody, and the dataset rules
    # already report it with the column name.
    from stavewright.gs1.label import check_dataset

    data = dataset_of(row("not an element string"))
    assert check_dataset(data) == []


def test_a_row_with_no_element_string_is_skipped():
    from stavewright.gs1.label import check_dataset

    assert check_dataset(dataset_of(row(""))) == []


def test_the_case_column_is_read_for_a_case_label():
    from stavewright.gs1.label import LabelKind, check_dataset

    data = dataset_of(row("", case="(01)01001000173250(10)AD47(30)12"))
    assert check_dataset(data, LabelKind.CASE) == []


def test_a_customer_requirement_reaches_every_row():
    from stavewright.gs1.label import check_dataset

    data = dataset_of(row("(01)01001000173250(21)0001"))
    findings = check_dataset(data, extra_required=("17",))
    assert "USE BY OR EXPIRY" in findings[0].message


def test_the_shipped_file_reads_as_sound_item_marks():
    # Its GTIN check digit is wrong, which the element string rules report.
    # As item marks the rows are the right shape, and that is a separate
    # question with a separate answer.
    from pathlib import Path

    from stavewright.gs1.dataset import read_dataset
    from stavewright.gs1.label import check_dataset

    root = Path(__file__).resolve().parents[2]
    data = read_dataset(root / "Matricode" / "Quattro" / "Templates" / "GS1-1.txt")
    assert check_dataset(data) == []
