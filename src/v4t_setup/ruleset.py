"""The scaffolding every rule engine in this toolkit was writing for itself.

There are four of them: the GS1 element string rules, the dataset rules, the
template rules and the preflight checks. All four had grown the same shape, a
class with a ``name``, a ``check`` that returns findings, and a private helper
that stamps the rule name onto a :class:`~v4t_setup.finding.Finding`.

They had also grown apart. Two of the four private helpers took a line number
and two did not, one took a subject, and the preflight one had picked up a
different argument order. Adding a field to a finding meant editing four
helpers and remembering all four.

The generic here is over what a rule looks at and what it is told about the
context, because those are the only two things the four differ in.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from v4t_setup.finding import Finding, Severity

Subject = TypeVar("Subject")
Context = TypeVar("Context")


class Rule(Generic[Subject, Context]):
    """A named check over *Subject*, told about *Context*.

    Subclasses set :attr:`name` and implement :meth:`check`. :attr:`title` is
    optional and only used where a command lists the rules for an operator.
    """

    name: str = "rule"
    title: str = ""

    def check(self, subject: Subject, context: Context, /) -> list[Finding]:
        """What this rule found.

        Both arguments are positional only so a subclass can name them after
        what they actually are. A preflight check reads far better with
        ``(context, target)`` than with ``(subject, context)``, and without the
        slash a type checker calls that an incompatible override.
        """
        raise NotImplementedError

    def _make(
        self,
        severity: Severity,
        message: str,
        *,
        ai: str | None = None,
        line: int | None = None,
        subject: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Finding:
        """Build a finding stamped with this rule's name.

        Takes the structured context as a dict rather than as keyword
        arguments. A subclass that wants a different argument order for
        :meth:`_finding` calls this, and passing ``**extra`` through a
        signature that also has named ``ai`` and ``line`` parameters is exactly
        the sort of shadowing a type checker is right to refuse.
        """
        return Finding(
            rule=self.name,
            severity=severity,
            message=message,
            ai=ai,
            line=line,
            subject=subject,
            context=dict(context or {}),
        )

    def _finding(
        self,
        severity: Severity,
        message: str,
        ai: str | None = None,
        line: int | None = None,
        subject: str | None = None,
        **extra: Any,
    ) -> Finding:
        """The convenient form. Every optional field, then the context."""
        return self._make(
            severity, message, ai=ai, line=line, subject=subject, context=extra
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


def run_rules(
    rules: tuple[Rule[Subject, Context], ...],
    subject: Subject,
    context: Context,
    *,
    skip: set[str] | None = None,
) -> list[Finding]:
    """Run *rules* in order and collect what they found.

    :param skip: rule names not to run. The caller records why somewhere the
        operator will see it, because a rule that was skipped and a rule that
        found nothing look identical in a report otherwise.
    """
    skipped = skip or set()
    findings: list[Finding] = []
    for rule in rules:
        if rule.name in skipped:
            continue
        findings.extend(rule.check(subject, context))
    return findings


def rule_names(rules: tuple[Rule[Any, Any], ...]) -> list[str]:
    """The names in the order they run."""
    return [rule.name for rule in rules]


def describe_rules(rules: tuple[Rule[Any, Any], ...]) -> list[str]:
    """One line per rule, for a help screen. Rules with no title are named only."""
    return [f"{rule.name}: {rule.title}" if rule.title else rule.name for rule in rules]


def duplicate_names(rules: tuple[Rule[Any, Any], ...]) -> list[str]:
    """Names used by more than one rule in a set.

    Two rules sharing a name makes a report ambiguous and makes a waiver hit
    both of them, so every rule set has a test that asserts this is empty.
    """
    seen: set[str] = set()
    repeated: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            repeated.add(rule.name)
        seen.add(rule.name)
    return sorted(repeated)


__all__ = [
    "Rule",
    "describe_rules",
    "duplicate_names",
    "rule_names",
    "run_rules",
]
