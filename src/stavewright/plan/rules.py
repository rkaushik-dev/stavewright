"""Why one piece of a bundle has to go on before another.

Each rule is a separate object and each edge it produces carries a sentence
explaining itself, because the ordering in a commissioning plan is the part
nobody can reconstruct from first principles two years later. When somebody
reorders a plan by hand and a line stops, the reason string is what tells them
what they broke.

The rules that are not obvious:

The C runtime has to be in the side by side store before an OCX that links
against it will register. ``regsvr32`` on such a control returns 0x8007007E and
the front end then starts and shows an empty grid rather than failing, which is
how this went unnoticed for a fortnight.

A publisher policy goes on after the assembly it redirects. The other way round
the policy points at nothing and the loader falls back to the version in the
manifest, which is usually the wrong one.

The bitstream goes on after the FTDI package. Programming the controller needs
the USB bridge to enumerate, and it will not enumerate without the driver.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from stavewright.model.payload import PayloadItem, PayloadKind

Edge = tuple[str, str, str]
"""before, after, reason."""


@dataclass(frozen=True, slots=True)
class RuleContext:
    """The items a rule set is working over, indexed the ways rules ask for."""

    items: tuple[PayloadItem, ...]

    def of_kind(self, *kinds: PayloadKind) -> list[PayloadItem]:
        wanted = set(kinds)
        return [item for item in self.items if item.kind in wanted]

    def providing(self, tag: str) -> list[PayloadItem]:
        return [item for item in self.items if tag in item.provides]

    def requiring(self, tag: str) -> list[PayloadItem]:
        return [item for item in self.items if tag in item.requires]

    @property
    def all_provided_tags(self) -> set[str]:
        return {tag for item in self.items for tag in item.provides}

    @property
    def all_required_tags(self) -> set[str]:
        return {tag for item in self.items for tag in item.requires}


class OrderingRule:
    """Base class. Subclasses return the edges they want."""

    name = "ordering-rule"

    def edges(self, context: RuleContext) -> list[Edge]:
        raise NotImplementedError


def _cross(
    before: Iterable[PayloadItem],
    after: Iterable[PayloadItem],
    reason: str,
) -> list[Edge]:
    """Every item in *before* precedes every item in *after*."""
    return [
        (first.item_id, second.item_id, reason)
        for first in before
        for second in after
        if first.item_id != second.item_id
    ]


class ExplicitTagRule(OrderingRule):
    """Honour the provides and requires tags an item carries."""

    name = "explicit-tag"

    def edges(self, context: RuleContext) -> list[Edge]:
        result: list[Edge] = []
        for tag in sorted(context.all_required_tags):
            providers = context.providing(tag)
            for consumer in context.requiring(tag):
                result.extend(
                    (provider.item_id, consumer.item_id, f"needs {tag}")
                    for provider in providers
                    if provider.item_id != consumer.item_id
                )
        return result


class RedistributableRule(OrderingRule):
    """The redistributable goes on before anything that links against it."""

    name = "redistributable"

    def edges(self, context: RuleContext) -> list[Edge]:
        redistributables = context.of_kind(PayloadKind.REDISTRIBUTABLE)
        if not redistributables:
            return []
        dependents = context.of_kind(
            PayloadKind.SXS_ASSEMBLY,
            PayloadKind.SXS_POLICY,
            PayloadKind.COM_SERVER,
            PayloadKind.FILE,
        )
        return _cross(
            redistributables,
            dependents,
            "the VC80 redistributable populates the side by side store",
        )


class AssemblyBeforePolicyRule(OrderingRule):
    """A publisher policy goes on after the assembly it redirects."""

    name = "assembly-before-policy"

    def edges(self, context: RuleContext) -> list[Edge]:
        return _cross(
            context.of_kind(PayloadKind.SXS_ASSEMBLY),
            context.of_kind(PayloadKind.SXS_POLICY),
            "a policy that lands first redirects to an assembly that is not there yet",
        )


class AssemblyBeforeComRule(OrderingRule):
    """Side by side assemblies go on before COM registration is attempted."""

    name = "assembly-before-com"

    def edges(self, context: RuleContext) -> list[Edge]:
        return _cross(
            context.of_kind(PayloadKind.SXS_ASSEMBLY, PayloadKind.SXS_POLICY),
            context.of_kind(PayloadKind.COM_SERVER),
            "regsvr32 fails with 0x8007007E when the CRT assembly is not in place",
        )


class DirectoryBeforeContentsRule(OrderingRule):
    """A directory item goes on before anything that lands inside it."""

    name = "directory-before-contents"

    def edges(self, context: RuleContext) -> list[Edge]:
        directories = {
            item.directory: item
            for item in context.of_kind(PayloadKind.DIRECTORY)
            if item.directory
        }
        result: list[Edge] = []
        for item in context.items:
            if item.kind is PayloadKind.DIRECTORY or not item.directory:
                continue
            parent = directories.get(item.directory)
            if parent is not None and parent.item_id != item.item_id:
                result.append(
                    (parent.item_id, item.item_id, f"{item.directory} has to exist first")
                )
        return result


class DriverBeforeFpgaRule(OrderingRule):
    """The bitstream goes on after the driver that talks to the controller."""

    name = "driver-before-fpga"

    def edges(self, context: RuleContext) -> list[Edge]:
        return _cross(
            context.of_kind(PayloadKind.DRIVER_PACKAGE),
            context.of_kind(PayloadKind.FPGA_IMAGE),
            "the controller will not enumerate without the FTDI bridge driver",
        )


class FontBeforeTemplateRule(OrderingRule):
    """Fonts go on before the templates that name them."""

    name = "font-before-template"

    def edges(self, context: RuleContext) -> list[Edge]:
        return _cross(
            context.of_kind(PayloadKind.FONT),
            context.of_kind(PayloadKind.TEMPLATE_SET),
            "a template opened before its font is installed silently substitutes",
        )


class ExecutableBeforeLanguageRule(OrderingRule):
    """Resource DLLs go on after the executable that loads them."""

    name = "executable-before-language"

    def edges(self, context: RuleContext) -> list[Edge]:
        executables = [
            item
            for item in context.of_kind(PayloadKind.FILE)
            if item.name.lower().endswith(".exe")
        ]
        return _cross(
            executables,
            context.of_kind(PayloadKind.LANGUAGE_PACK),
            "a resource DLL beside no executable is not detected as installed",
        )


class ShortcutAfterTargetRule(OrderingRule):
    """A shortcut goes on after the thing it points at."""

    name = "shortcut-after-target"

    def edges(self, context: RuleContext) -> list[Edge]:
        by_name = {item.name: item for item in context.of_kind(PayloadKind.FILE)}
        result: list[Edge] = []
        for shortcut in context.of_kind(PayloadKind.SHORTCUT):
            target = shortcut.detail.get("target")
            item = by_name.get(str(target)) if target else None
            if item is not None:
                result.append(
                    (
                        item.item_id,
                        shortcut.item_id,
                        f"the shortcut points at {item.name}",
                    )
                )
        return result


class RegistryLastRule(OrderingRule):
    """The product registry block goes on once everything else is in place.

    It is what reconcile reads to decide a terminal is commissioned, so writing
    it early means an interrupted run leaves a machine that claims to be
    finished and is not.
    """

    name = "registry-last"

    def edges(self, context: RuleContext) -> list[Edge]:
        registry = context.of_kind(PayloadKind.REGISTRY)
        if not registry:
            return []
        everything_else = [item for item in context.items if item.kind is not PayloadKind.REGISTRY]
        return _cross(
            everything_else,
            registry,
            "an interrupted run must not leave a terminal claiming to be commissioned",
        )


DEFAULT_RULES: tuple[OrderingRule, ...] = (
    ExplicitTagRule(),
    RedistributableRule(),
    AssemblyBeforePolicyRule(),
    AssemblyBeforeComRule(),
    DirectoryBeforeContentsRule(),
    DriverBeforeFpgaRule(),
    FontBeforeTemplateRule(),
    ExecutableBeforeLanguageRule(),
    ShortcutAfterTargetRule(),
    RegistryLastRule(),
)


def collect_edges(
    items: list[PayloadItem],
    rules: tuple[OrderingRule, ...] = DEFAULT_RULES,
) -> list[Edge]:
    """Run every rule and return the edges, in rule order then rule output order."""
    context = RuleContext(items=tuple(items))
    result: list[Edge] = []
    for rule in rules:
        result.extend(rule.edges(context))
    return result


def unsatisfied_requirements(items: list[PayloadItem]) -> dict[str, list[str]]:
    """Tags an item requires that nothing in the bundle provides.

    Not always a bug. A bundle that omits the redistributable because the site
    images it separately will show one of these, which is why the planner
    reports rather than refuses.
    """
    context = RuleContext(items=tuple(items))
    provided = context.all_provided_tags
    missing: dict[str, list[str]] = {}
    for item in items:
        gaps = sorted(tag for tag in item.requires if tag not in provided)
        if gaps:
            missing[item.item_id] = gaps
    return missing


__all__ = [
    "DEFAULT_RULES",
    "AssemblyBeforeComRule",
    "AssemblyBeforePolicyRule",
    "DirectoryBeforeContentsRule",
    "DriverBeforeFpgaRule",
    "Edge",
    "ExecutableBeforeLanguageRule",
    "ExplicitTagRule",
    "FontBeforeTemplateRule",
    "OrderingRule",
    "RedistributableRule",
    "RegistryLastRule",
    "RuleContext",
    "ShortcutAfterTargetRule",
    "collect_edges",
    "unsatisfied_requirements",
]
