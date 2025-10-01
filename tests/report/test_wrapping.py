"""Wrapping, which the text renderer is the only thing that does.

Split out of test_render.py because these are about one function and the rest
of that file is about four output formats. The bug that started it: a word
longer than the width was appended to the line before it, so a report carrying
a long Windows path ran past the width it had been given, and every test used
short words so nothing saw it.

Everything here goes through to_text rather than the private wrapper. The
width is the promise the renderer makes to whoever is reading a report in an
80 column terminal, and that is the thing worth pinning.
"""

from __future__ import annotations

import pytest

from v4t_setup.finding import Finding, Severity
from v4t_setup.report.document import Report, findings_section, lines_section
from v4t_setup.report.render import to_text

LONG_PATH = "C:" + "\\averylongdirectoryname" * 6 + "\\V4T.exe"


def note(message: str) -> Finding:
    return Finding(rule="wrap", severity=Severity.ERROR, message=message)


def report_of(message: str) -> Report:
    report = Report(title="Wrapping")
    report.add(findings_section("Findings", [note(message)]))
    return report


def widest(text: str) -> int:
    return max(len(line) for line in text.splitlines())


@pytest.mark.parametrize("width", [40, 60, 72, 80, 100])
def test_no_line_runs_past_the_width_it_was_given(width):
    assert widest(to_text(report_of(f"cannot read {LONG_PATH}"), width=width)) <= width


def test_an_over_long_word_is_broken_rather_than_hung_off_the_line_before():
    lines = to_text(report_of(f"cannot read {LONG_PATH}"), width=50).splitlines()
    assert any(LONG_PATH[:20] in line for line in lines)
    assert widest("\n".join(lines)) <= 50


def test_wrapping_keeps_every_word():
    message = " ".join(f"word{n}" for n in range(60))
    rendered = to_text(report_of(message), width=48)
    for n in range(60):
        assert f"word{n}" in rendered


def test_wrapping_does_not_join_two_words_together():
    message = " ".join(f"word{n}" for n in range(60))
    rendered = to_text(report_of(message), width=48)
    assert "word0word1" not in rendered


def test_an_empty_message_still_renders():
    assert "Findings" in to_text(report_of(""), width=60)


def test_a_message_of_only_spaces_does_not_produce_a_ragged_line():
    rendered = to_text(report_of("   "), width=60)
    assert not any(line.endswith(" ") for line in rendered.splitlines())


def test_a_line_section_does_not_wrap_at_all():
    """Deliberate, and the opposite of what the findings do.

    A line section carries paths and identifiers, and somebody is going to
    select one and paste it into a shell. A path broken across two lines to
    respect the width is a path that does not work when pasted, so these run
    long on purpose.
    """
    report = Report(title="Wrapping")
    report.add(lines_section("Where things go", [f"staging: {LONG_PATH}"]))
    rendered = to_text(report, width=64)
    assert f"staging: {LONG_PATH}" in rendered
    assert widest(rendered) > 64


def test_a_narrow_width_is_survivable():
    """Nobody reads a report at twenty columns. It must not raise or loop."""
    assert to_text(report_of(f"cannot read {LONG_PATH}"), width=20)


def test_the_same_report_at_two_widths_carries_the_same_words():
    message = "the terminal is running an FPGA revision this bundle was not built for"
    narrow = to_text(report_of(message), width=40)
    wide = to_text(report_of(message), width=100)
    assert [word for word in narrow.split() if "=" not in word and "-" not in word] == [
        word for word in wide.split() if "=" not in word and "-" not in word
    ]
