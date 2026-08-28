from __future__ import annotations

import pytest

from stavewright.gs1.dataset import EXPECTED_COLUMNS, parse_dataset, read_dataset
from stavewright.gs1.datasetlint import (
    AlignmentMarkRule,
    ColumnAgreementRule,
    DuplicateSerialRule,
    Ean13ColumnRule,
    ElementStringRule,
    GtinColumnRule,
    IndexContinuityRule,
    LotCodeRule,
    RaggedRowRule,
    lint_dataset,
    summarise,
)
from stavewright.gs1.validate import MarkingContext, Severity

HEADER = ",".join(EXPECTED_COLUMNS)
CONTEXT = MarkingContext(reference_year=2016)

# A row built the way the file would look if the GTIN were right.
GOOD = (
    "1,0001,AD47-001,AB:08,01001000173250,A,"
    "(01)01001000173250(21)0001,(01)01001000173250(15)150807,0100100017325"
)


def build(*rows: str):
    return parse_dataset("\n".join([HEADER, *rows]) + "\n")


@pytest.fixture
def shipped(request):
    return read_dataset(
        request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "GS1-1.txt"
    )


def test_a_clean_row_produces_nothing():
    assert lint_dataset(build(GOOD), CONTEXT) == []


def test_ragged_rows_are_reported():
    short = GOOD.rsplit(",", 1)[0]
    findings = RaggedRowRule().check(build(GOOD, short), CONTEXT)
    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["missing"] == ["EAN13"]


def test_a_bad_gtin_column_is_reported():
    bad = GOOD.replace("01001000173250,A", "00125682945601,A")
    findings = GtinColumnRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["expected"] == "6"
    assert findings[0].context["found"] == "1"


def test_a_gtin_of_the_wrong_length_is_reported():
    # Ten digits is not a GTIN length at all. Note that dropping a single digit
    # is not enough to make this fail, because thirteen is legal and the shorter
    # value happens to check out once it is padded.
    bad = GOOD.replace("01001000173250,A", "0100100017,A")
    findings = GtinColumnRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert "8, 12, 13 or 14" in findings[0].message


def test_dropping_one_digit_from_a_gtin_can_still_validate():
    # 0100100017325 pads to 00100100017325 and its check digit holds. This is
    # why the column agreement rule exists as well as this one.
    bad = GOOD.replace("01001000173250,A", "0100100017325,A")
    assert GtinColumnRule().check(build(bad), CONTEXT) == []
    assert len(ColumnAgreementRule().check(build(bad), CONTEXT)) == 1


def test_an_unparseable_element_string_is_reported():
    bad = GOOD.replace("(01)01001000173250(21)0001", "(77)nonsense")
    findings = ElementStringRule().check(build(bad), CONTEXT)
    assert any(f.severity is Severity.ERROR for f in findings)
    assert findings[0].context["column"] == "GS1 DATA"


def test_per_string_findings_carry_the_line_and_column():
    bad = GOOD.replace(
        "(01)01001000173250(21)0001", "(01)00125682945601(21)0001"
    )
    findings = ElementStringRule().check(build(bad), CONTEXT)
    checkdigit = [f for f in findings if f.rule.endswith("check-digit")]
    assert len(checkdigit) == 1
    assert checkdigit[0].line == 2
    assert checkdigit[0].ai == "01"
    assert checkdigit[0].context["column"] == "GS1 DATA"


def test_a_gtin_that_disagrees_with_the_element_string_is_reported():
    bad = GOOD.replace("(01)01001000173250(21)0001", "(01)05000157024671(21)0001")
    findings = ColumnAgreementRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert findings[0].context["in_column"] == "01001000173250"
    assert findings[0].context["in_string"] == "05000157024671"


def test_a_serial_that_disagrees_with_the_element_string_is_reported():
    bad = GOOD.replace("(01)01001000173250(21)0001", "(01)01001000173250(21)9999")
    findings = ColumnAgreementRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert findings[0].context["column"] == "SERIAL"


def test_an_element_string_with_no_serial_is_reported():
    bad = GOOD.replace("(01)01001000173250(21)0001", "(01)01001000173250")
    findings = ColumnAgreementRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert "carries no (21)" in findings[0].message


def test_agreement_stays_quiet_when_the_string_will_not_parse():
    # ElementStringRule already reported it.
    bad = GOOD.replace("(01)01001000173250(21)0001", "(77)nope")
    assert ColumnAgreementRule().check(build(bad), CONTEXT) == []


def test_a_repeated_serial_is_reported_once_per_repeat():
    second = GOOD.replace("1,0001,", "2,0001,")
    findings = DuplicateSerialRule().check(build(GOOD, second), CONTEXT)
    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].context["first_seen"] == 2


def test_distinct_serials_are_fine():
    second = GOOD.replace("1,0001,", "2,0002,").replace("(21)0001", "(21)0002")
    assert DuplicateSerialRule().check(build(GOOD, second), CONTEXT) == []


def test_an_index_gap_is_reported():
    second = GOOD.replace("1,0001,", "5,0002,").replace("(21)0001", "(21)0002")
    findings = IndexContinuityRule().check(build(GOOD, second), CONTEXT)
    assert len(findings) == 1
    assert findings[0].context["expected"] == 2
    assert findings[0].context["found"] == 5


def test_an_index_that_does_not_start_at_one_is_reported():
    findings = IndexContinuityRule().check(build(GOOD.replace("1,0001,", "7,0001,")), CONTEXT)
    assert len(findings) == 1
    assert findings[0].context["expected"] == 1


def test_a_non_numeric_index_is_an_error():
    findings = IndexContinuityRule().check(build(GOOD.replace("1,0001,", "A,0001,")), CONTEXT)
    assert findings[0].severity is Severity.ERROR


@pytest.mark.parametrize("code", ["AB:08", "AC:00", "ZZ:99"])
def test_good_lot_codes(code):
    assert LotCodeRule().check(build(GOOD.replace("AB:08", code)), CONTEXT) == []


@pytest.mark.parametrize("code", ["ab:08", "AB-08", "A:08", "AB:8", "AB:088", ""])
def test_bad_lot_codes(code):
    findings = LotCodeRule().check(build(GOOD.replace("AB:08", code)), CONTEXT)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


@pytest.mark.parametrize("mark", ["A", "G", "Z"])
def test_good_alignment_marks(mark):
    assert AlignmentMarkRule().check(build(GOOD.replace(",A,", f",{mark},")), CONTEXT) == []


@pytest.mark.parametrize("mark", ["a", "AA", "1", ""])
def test_bad_alignment_marks(mark):
    findings = AlignmentMarkRule().check(build(GOOD.replace(",A,", f",{mark},")), CONTEXT)
    assert len(findings) == 1


def test_a_thirteen_digit_ean13_is_fine():
    assert Ean13ColumnRule().check(build(GOOD), CONTEXT) == []


def test_a_twelve_digit_ean13_is_a_warning():
    bad = GOOD.rsplit(",", 1)[0] + ",125682945601"
    findings = Ean13ColumnRule().check(build(bad), CONTEXT)
    assert len(findings) == 1
    assert findings[0].context["length"] == 12


def test_a_non_numeric_ean13_is_an_error():
    bad = GOOD.rsplit(",", 1)[0] + ",ABC"
    findings = Ean13ColumnRule().check(build(bad), CONTEXT)
    assert findings[0].severity is Severity.ERROR


def test_a_missing_ean13_is_left_to_the_ragged_rule():
    short = GOOD.rsplit(",", 1)[0]
    assert Ean13ColumnRule().check(build(short), CONTEXT) == []


def test_findings_are_sorted_by_line_then_rule():
    short = GOOD.replace("1,0001,", "3,0001,").rsplit(",", 1)[0]
    findings = lint_dataset(build(GOOD, short), CONTEXT)
    lines = [f.line for f in findings]
    assert lines == sorted(lines)


def test_summarise_orders_by_count():
    short = GOOD.rsplit(",", 1)[0]
    second = short.replace("1,0001,", "2,0002,").replace("(21)0001", "(21)0002")
    third = short.replace("1,0001,", "3,0003,").replace("(21)0001", "(21)0003")
    counts = summarise(lint_dataset(build(GOOD, second, third), CONTEXT))
    assert counts == {"ragged-row": 2}
    assert next(iter(counts)) == "ragged-row"


def test_summarise_breaks_ties_alphabetically():
    short = GOOD.rsplit(",", 1)[0]
    counts = summarise(lint_dataset(build(GOOD, short), CONTEXT))
    assert list(counts) == sorted(counts)


def test_summarise_of_nothing_is_empty():
    assert summarise([]) == {}


def test_a_custom_rule_set_runs_only_what_it_is_given():
    short = GOOD.rsplit(",", 1)[0]
    assert lint_dataset(build(short), CONTEXT, rules=(LotCodeRule(),)) == []


# The shipped file. Everything below is a finding about real example data.


def test_the_shipped_file_reports_the_expected_findings(shipped):
    assert summarise(lint_dataset(shipped, CONTEXT)) == {
        "element-string/check-digit": 130,
        "gtin-column": 130,
        "duplicate-serial": 100,
        "ean13-column": 98,
        "ragged-row": 32,
        "index-continuity": 3,
    }


def test_every_shipped_row_carries_the_bad_gtin(shipped):
    findings = GtinColumnRule().check(shipped, CONTEXT)
    assert len(findings) == len(shipped)
    assert {f.context["expected"] for f in findings} == {"6"}


def test_the_shipped_ean13_column_holds_twelve_digits_not_thirteen(shipped):
    findings = Ean13ColumnRule().check(shipped, CONTEXT)
    assert {f.context["length"] for f in findings} == {12}


def test_the_shipped_serials_cycle_every_thirty_rows(shipped):
    findings = DuplicateSerialRule().check(shipped, CONTEXT)
    assert len(findings) == len(shipped) - 30


def test_the_shipped_columns_agree_with_their_element_strings(shipped):
    # The GTIN is wrong, but it is consistently wrong, which is what this rule
    # is actually asking about.
    assert ColumnAgreementRule().check(shipped, CONTEXT) == []


def test_the_shipped_lot_codes_are_all_well_formed(shipped):
    assert LotCodeRule().check(shipped, CONTEXT) == []


def test_the_shipped_alignment_marks_are_all_well_formed(shipped):
    assert AlignmentMarkRule().check(shipped, CONTEXT) == []
