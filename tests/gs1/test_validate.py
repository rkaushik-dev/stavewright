from __future__ import annotations

from datetime import date

import pytest

from stavewright.gs1.parse import parse
from stavewright.gs1.validate import (
    CheckDigitRule,
    DateOrderRule,
    DateSanityRule,
    DuplicateAiRule,
    EncodedLengthRule,
    ExclusiveDateRule,
    Finding,
    MarkingContext,
    PrimaryIdentifierRule,
    Severity,
    has_errors,
    validate,
    worst_severity,
)

GOOD = "(01)01001000173250(21)0001"
BAD_GTIN = "(01)00125682945601(21)0001"
CONTEXT_2016 = MarkingContext(reference_year=2016, today=date(2016, 7, 18))


def codes(findings: list[Finding]) -> list[str]:
    return [finding.rule for finding in findings]


def test_a_good_element_string_produces_nothing():
    assert validate(parse(GOOD), CONTEXT_2016) == []


def test_the_shipped_bad_gtin_is_caught():
    findings = validate(parse(BAD_GTIN), CONTEXT_2016)
    assert codes(findings) == ["check-digit"]
    assert findings[0].severity is Severity.ERROR
    assert findings[0].ai == "01"
    assert findings[0].context["expected"] == "6"
    assert findings[0].context["found"] == "1"


def test_the_second_shipped_string_is_clean_apart_from_its_date():
    findings = validate(parse("(01)01001000173250(15)150807"), CONTEXT_2016)
    assert codes(findings) == ["date-sanity"]
    assert findings[0].severity is Severity.WARNING


def test_that_same_string_is_clean_when_marked_before_the_date():
    context = MarkingContext(reference_year=2015, today=date(2015, 1, 1))
    assert validate(parse("(01)01001000173250(15)150807"), context) == []


def test_check_digit_rule_ignores_ais_that_have_none():
    rule = CheckDigitRule()
    assert rule.check(parse("(01)01001000173250(21)9999"), CONTEXT_2016) == []


def test_check_digit_rule_catches_a_bad_sscc():
    rule = CheckDigitRule()
    findings = rule.check(parse("(00)123456789012345670"), CONTEXT_2016)
    assert len(findings) == 1
    assert findings[0].ai == "00"


def test_check_digit_rule_accepts_a_good_sscc():
    rule = CheckDigitRule()
    assert rule.check(parse("(00)123456789012345675"), CONTEXT_2016) == []


def test_a_string_with_no_primary_identifier_is_an_error():
    findings = PrimaryIdentifierRule().check(parse("(10)AD47-001(21)0001"), CONTEXT_2016)
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR


def test_that_can_be_switched_off_for_a_partial_string():
    context = MarkingContext(require_primary=False)
    assert PrimaryIdentifierRule().check(parse("(10)AD47-001(21)0001"), context) == []


def test_two_primary_identifiers_is_an_error():
    findings = PrimaryIdentifierRule().check(
        parse("(01)01001000173250(00)123456789012345675"), CONTEXT_2016
    )
    assert any(f.severity is Severity.ERROR for f in findings)


def test_a_primary_identifier_that_is_not_first_is_a_warning():
    findings = PrimaryIdentifierRule().check(
        parse("(10)AD47-001(01)01001000173250"), CONTEXT_2016
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["order"] == ["10", "01"]


def test_a_primary_identifier_first_is_fine():
    assert PrimaryIdentifierRule().check(parse(GOOD), CONTEXT_2016) == []


def test_a_repeated_ai_is_caught():
    findings = DuplicateAiRule().check(parse("(21)0001(21)0002"), CONTEXT_2016)
    assert len(findings) == 1
    assert findings[0].ai == "21"
    assert findings[0].context["count"] == 2


def test_no_duplicates_when_every_ai_is_distinct():
    assert DuplicateAiRule().check(parse(GOOD), CONTEXT_2016) == []


def test_an_unparseable_date_is_an_error():
    findings = DateSanityRule().check(parse("(01)01001000173250(15)151301"), CONTEXT_2016)
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].ai == "15"


def test_a_past_date_is_a_warning_not_an_error():
    findings = DateSanityRule().check(
        parse("(01)01001000173250(17)150807"), MarkingContext(reference_year=2016,
                                                             today=date(2025, 1, 1))
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["resolved"] == "2015-08-07"


def test_no_date_warning_when_no_today_was_supplied():
    context = MarkingContext(reference_year=2016)
    assert DateSanityRule().check(parse("(01)01001000173250(17)150807"), context) == []


def test_an_expiry_before_the_production_date_is_an_error():
    findings = DateOrderRule().check(
        parse("(01)01001000173250(11)250601(17)250501"), MarkingContext(reference_year=2025)
    )
    assert len(findings) == 1
    assert findings[0].ai == "17"
    assert findings[0].context["production"] == "2025-06-01"


def test_an_expiry_after_the_production_date_is_fine():
    assert (
        DateOrderRule().check(
            parse("(01)01001000173250(11)250501(17)250601"), MarkingContext(reference_year=2025)
        )
        == []
    )


def test_the_same_day_is_fine():
    assert (
        DateOrderRule().check(
            parse("(01)01001000173250(11)250501(17)250501"), MarkingContext(reference_year=2025)
        )
        == []
    )


def test_date_order_does_nothing_without_a_production_date():
    assert DateOrderRule().check(parse("(01)01001000173250(17)250501"), CONTEXT_2016) == []


def test_date_order_stays_quiet_when_a_date_will_not_parse():
    # DateSanityRule reports that. This rule should not report it twice.
    assert (
        DateOrderRule().check(
            parse("(01)01001000173250(11)251301(17)250501"), MarkingContext(reference_year=2025)
        )
        == []
    )


def test_best_before_and_expiry_together_is_a_warning():
    findings = ExclusiveDateRule().check(
        parse("(01)01001000173250(15)250501(17)250601"), CONTEXT_2016
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["found"] == ["15", "17"]


def test_either_one_alone_is_fine():
    for ai in ("15", "17"):
        assert ExclusiveDateRule().check(
            parse(f"(01)01001000173250({ai})250501"), CONTEXT_2016
        ) == []


def test_no_length_limit_means_no_length_rule():
    long_string = parse("(01)01001000173250(21)" + "A" * 20)
    assert EncodedLengthRule().check(long_string, MarkingContext()) == []


def test_a_string_that_fits_the_head_passes():
    context = MarkingContext(max_encoded_length=64)
    assert EncodedLengthRule().check(parse(GOOD), context) == []


def test_a_string_that_does_not_fit_is_an_error():
    context = MarkingContext(max_encoded_length=20)
    findings = EncodedLengthRule().check(parse(GOOD), context)
    assert len(findings) == 1
    assert findings[0].context["length"] == 22
    assert findings[0].context["limit"] == 20


def test_the_limit_is_inclusive():
    context = MarkingContext(max_encoded_length=22)
    assert EncodedLengthRule().check(parse(GOOD), context) == []


def test_findings_come_back_in_rule_order():
    findings = validate(parse("(21)0001(01)00125682945601"), CONTEXT_2016)
    assert codes(findings) == ["check-digit", "primary-identifier"]


def test_a_custom_rule_set_runs_only_what_it_is_given():
    findings = validate(parse(BAD_GTIN), CONTEXT_2016, rules=(DuplicateAiRule(),))
    assert findings == []


def test_validate_with_no_context_still_works():
    assert has_errors(validate(parse(BAD_GTIN)))


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], None),
        ([Severity.INFO], Severity.INFO),
        ([Severity.WARNING, Severity.INFO], Severity.WARNING),
        ([Severity.INFO, Severity.ERROR, Severity.WARNING], Severity.ERROR),
    ],
)
def test_worst_severity(severities, expected):
    findings = [Finding(rule="r", severity=s, message="m") for s in severities]
    assert worst_severity(findings) == expected


def test_has_errors():
    assert not has_errors([])
    assert not has_errors([Finding(rule="r", severity=Severity.WARNING, message="m")])
    assert has_errors([Finding(rule="r", severity=Severity.ERROR, message="m")])


def test_finding_renders_with_its_ai():
    finding = Finding(rule="check-digit", severity=Severity.ERROR, message="bad", ai="01")
    assert str(finding) == "[error] check-digit (01): bad"


def test_finding_renders_without_an_ai():
    finding = Finding(rule="primary-identifier", severity=Severity.WARNING, message="none")
    assert str(finding) == "[warning] primary-identifier: none"
