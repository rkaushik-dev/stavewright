from __future__ import annotations

import pytest

from v4t_setup.gs1.dataset import EXPECTED_COLUMNS
from v4t_setup.gs1.validate import Severity
from v4t_setup.templates.linter import (
    BlockOrderRule,
    BoundFieldRule,
    DuplicateIndexRule,
    FontRule,
    PlaceholderRule,
    SourceSlotRule,
    StructureRule,
    SymbologyRule,
    TemplateContext,
    lint_template,
)
from v4t_setup.templates.spf import MAGIC, SpfBlock, SpfTemplate

DATASET_COLUMNS = frozenset(EXPECTED_COLUMNS)


def u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def block(name: str, *strings: str, offset: int = 0) -> SpfBlock:
    payload = b"\x01\x02".join(u16(text) for text in strings)
    return SpfBlock(name=name, offset=offset, payload=payload)


def template(*blocks: SpfBlock) -> SpfTemplate:
    return SpfTemplate(magic=MAGIC, blocks=list(blocks), size=1024)


def rules_hit(findings) -> list[str]:
    return [finding.rule for finding in findings]


@pytest.fixture
def shipped(request):
    from v4t_setup.templates.spf import read_spf

    return read_spf(
        request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "T1.spf"
    )


def _good_template() -> SpfTemplate:
    return template(
        block("TEMPLATE"),
        block("BARCODELABEL_1", "GS1 DataMatrix"),
        block("TEXTLABEL", "F:Arial,12,0,0,S:3,SERIAL"),
        block("COGNEXLABEL", "F:OCR-B,10,0,0,S:4,#Cognex Code"),
    )


def test_a_clean_template_produces_nothing():
    assert lint_template(_good_template(), TemplateContext(dataset_columns=DATASET_COLUMNS)) == []


def test_a_template_with_no_header_block_is_an_error():
    findings = StructureRule().check(
        template(block("TEXTLABEL", "F:Arial,12,0,0")), TemplateContext()
    )
    assert findings[0].severity is Severity.ERROR
    assert "no TEMPLATE block" in findings[0].message


def test_two_header_blocks_is_an_error():
    findings = StructureRule().check(
        template(block("TEMPLATE"), block("TEMPLATE"), block("TEXTLABEL")), TemplateContext()
    )
    assert findings[0].context["count"] == 2


def test_a_template_with_only_a_header_prints_nothing():
    findings = StructureRule().check(template(block("TEMPLATE")), TemplateContext())
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


def test_an_empty_template_is_reported_twice():
    findings = StructureRule().check(template(), TemplateContext())
    assert len(findings) == 2


def test_a_header_that_is_not_first_is_a_warning():
    findings = BlockOrderRule().check(
        template(block("TEXTLABEL"), block("TEMPLATE")), TemplateContext()
    )
    assert len(findings) == 1
    assert findings[0].context["order"] == ["TEXTLABEL", "TEMPLATE"]


def test_a_header_first_is_fine():
    assert BlockOrderRule().check(_good_template(), TemplateContext()) == []


def test_an_empty_template_has_no_order_problem():
    assert BlockOrderRule().check(template(), TemplateContext()) == []


def test_two_blocks_with_the_same_kind_and_index_is_an_error():
    findings = DuplicateIndexRule().check(
        template(
            block("BARCODELABEL_1", offset=100),
            block("BARCODELABEL_1", offset=200),
        ),
        TemplateContext(),
    )
    assert len(findings) == 1
    assert findings[0].context["first_at"] == 100
    assert findings[0].context["second_at"] == 200


def test_different_indices_are_fine():
    assert (
        DuplicateIndexRule().check(
            template(block("BARCODELABEL_1"), block("BARCODELABEL_2")), TemplateContext()
        )
        == []
    )


def test_blocks_without_an_index_do_not_collide():
    assert (
        DuplicateIndexRule().check(
            template(block("TEXTLABEL"), block("TEXTLABEL")), TemplateContext()
        )
        == []
    )


@pytest.mark.parametrize("symbology", ["EAN-13+2", "GS1-128", "GS1 DataMatrix", "ITF-14"])
def test_supported_symbologies_pass(symbology):
    assert SymbologyRule().check(
        template(block("BARCODELABEL_1", symbology)), TemplateContext()
    ) == []


def test_an_unsupported_symbology_is_an_error():
    findings = SymbologyRule().check(
        template(block("BARCODELABEL_1", "EAN-99")), TemplateContext()
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert findings[0].context["symbology"] == "EAN-99"


def test_a_barcode_block_naming_nothing_recognisable_is_a_warning():
    findings = SymbologyRule().check(
        template(block("BARCODELABEL_1", "some other text")), TemplateContext()
    )
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING


def test_a_narrowed_symbology_set_rejects_what_the_head_cannot_do():
    context = TemplateContext(supported_symbologies=frozenset({"CODE128"}))
    findings = SymbologyRule().check(
        template(block("BARCODELABEL_1", "EAN-13+2")), context
    )
    assert findings[0].severity is Severity.ERROR


def test_a_placeholder_barcode_value_is_a_warning():
    findings = PlaceholderRule().check(
        template(block("BARCODELABEL_3", "EAN-13+2", "00000000000000")), TemplateContext()
    )
    assert len(findings) == 1
    assert findings[0].context["values"] == ["00000000000000"]


def test_a_real_barcode_value_is_fine():
    assert (
        PlaceholderRule().check(
            template(block("BARCODELABEL_3", "EAN-13+2", "01001000173250")), TemplateContext()
        )
        == []
    )


def test_placeholders_are_only_looked_for_in_barcode_blocks():
    assert (
        PlaceholderRule().check(
            template(block("TEXTLABEL", "00000000000000")), TemplateContext()
        )
        == []
    )


def test_a_known_font_passes():
    assert FontRule().check(
        template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,SERIAL")), TemplateContext()
    ) == []


def test_the_bundled_ocr_font_passes():
    assert FontRule().check(
        template(block("TEXTLABEL", "F:OCR-B,10,0,0,S:1,SERIAL")), TemplateContext()
    ) == []


def test_an_unknown_font_is_an_error():
    findings = FontRule().check(
        template(block("TEXTLABEL", "F:Wingdings,12,0,0,S:1,SERIAL")), TemplateContext()
    )
    assert len(findings) == 1
    assert findings[0].context["font"] == "Wingdings"


def test_a_zero_font_size_is_an_error():
    findings = FontRule().check(
        template(block("TEXTLABEL", "F:Arial,0,0,0,S:1,SERIAL")), TemplateContext()
    )
    assert len(findings) == 1
    assert "zero or less" in findings[0].message


def test_a_narrowed_font_set_rejects_arial():
    context = TemplateContext(available_fonts=frozenset({"OCR-B"}))
    findings = FontRule().check(
        template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,SERIAL")), context
    )
    assert findings[0].context["font"] == "Arial"


def test_a_field_bound_to_a_real_column_passes():
    assert (
        BoundFieldRule().check(
            template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,SERIAL")),
            TemplateContext(dataset_columns=DATASET_COLUMNS),
        )
        == []
    )


def test_a_field_bound_to_a_missing_column_is_an_error():
    findings = BoundFieldRule().check(
        template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,ERT")),
        TemplateContext(dataset_columns=DATASET_COLUMNS),
    )
    assert len(findings) == 1
    assert findings[0].context["reference"] == "ERT"


def test_a_literal_field_is_never_bound():
    assert (
        BoundFieldRule().check(
            template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,#Fixed text")),
            TemplateContext(dataset_columns=DATASET_COLUMNS),
        )
        == []
    )


def test_binding_is_not_checked_without_a_column_list():
    assert (
        BoundFieldRule().check(
            template(block("TEXTLABEL", "F:Arial,12,0,0,S:1,ERT")), TemplateContext()
        )
        == []
    )


def test_two_fields_on_the_same_slot_is_a_warning():
    findings = SourceSlotRule().check(
        template(
            block("TEXTLABEL", "F:Arial,12,0,0,S:3,SERIAL"),
            block("COGNEXLABEL", "F:Arial,12,0,0,S:3,BATCH"),
        ),
        TemplateContext(),
    )
    assert len(findings) == 1
    assert findings[0].context["slot"] == 3


def test_distinct_slots_are_fine():
    assert SourceSlotRule().check(_good_template(), TemplateContext()) == []


def test_findings_come_back_in_rule_order():
    broken = template(
        block("TEXTLABEL", "F:Wingdings,12,0,0,S:3,ERT"),
        block("TEMPLATE"),
    )
    findings = lint_template(broken, TemplateContext(dataset_columns=DATASET_COLUMNS))
    assert rules_hit(findings) == ["block-order", "font", "bound-field"]


def test_a_custom_rule_set_runs_only_what_it_is_given():
    broken = template(block("TEXTLABEL", "F:Wingdings,12,0,0"))
    assert lint_template(broken, rules=(SourceSlotRule(),)) == []


# The shipped template.


def test_the_shipped_template_has_one_finding_on_its_own(shipped):
    findings = lint_template(shipped)
    assert rules_hit(findings) == ["placeholder"]
    assert findings[0].context["values"] == ["00000000000000", "000000000000000"]


def test_the_shipped_template_binds_a_field_the_data_file_has_no_column_for(shipped):
    findings = lint_template(shipped, TemplateContext(dataset_columns=DATASET_COLUMNS))
    bound = [f for f in findings if f.rule == "bound-field"]
    assert len(bound) == 1
    assert bound[0].context["reference"] == "ERT"


def test_the_shipped_template_structure_is_sound(shipped):
    assert StructureRule().check(shipped, TemplateContext()) == []
    assert BlockOrderRule().check(shipped, TemplateContext()) == []
    assert DuplicateIndexRule().check(shipped, TemplateContext()) == []


def test_the_shipped_symbology_is_supported(shipped):
    assert SymbologyRule().check(shipped, TemplateContext()) == []


def test_the_shipped_fonts_are_available(shipped):
    assert FontRule().check(shipped, TemplateContext()) == []


def test_the_shipped_source_slots_do_not_collide(shipped):
    assert SourceSlotRule().check(shipped, TemplateContext()) == []
