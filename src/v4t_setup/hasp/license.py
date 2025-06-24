"""What a dongle enables, and whether that covers the plan.

The check that matters is not "is there a key". It is "does this key cover what
this terminal is about to be asked to do". A four head machine behind a two
head key commissions perfectly and then refuses to start the third head, which
is discovered on the line rather than on the bench.

Keys are described in a compact text form so they can go on a build sheet and
be typed back in::

    HASP-0042/SJ6+TIJ/PRINT+VERIFY+MULTIHEAD/4/2026-12-31

Serial, the machines it covers, the features, the head count and the expiry.
The parser is strict about the shape because a mistyped key that parses into a
different key is worse than one that does not parse at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntFlag

from v4t_setup.errors import LicenseError
from v4t_setup.finding import Finding, Severity
from v4t_setup.model.profile import TerminalModel, TerminalProfile

FIELD_SEPARATOR = "/"
SET_SEPARATOR = "+"
UNLIMITED = "*"
NEVER_EXPIRES = "-"


class Feature(IntFlag):
    """What a key switches on."""

    NONE = 0
    PRINT = 1
    VERIFY = 2
    MULTI_HEAD = 4
    GS1_DATAMATRIX = 8
    DATABASE = 16
    REMOTE = 32
    OCR = 64
    AUDIT_TRAIL = 128


FEATURE_NAMES: dict[str, Feature] = {
    "PRINT": Feature.PRINT,
    "VERIFY": Feature.VERIFY,
    "MULTIHEAD": Feature.MULTI_HEAD,
    "GS1DM": Feature.GS1_DATAMATRIX,
    "DATABASE": Feature.DATABASE,
    "REMOTE": Feature.REMOTE,
    "OCR": Feature.OCR,
    "AUDIT": Feature.AUDIT_TRAIL,
}
"""Short names, because these go on a printed build sheet."""

NAME_BY_FEATURE = {feature: name for name, feature in FEATURE_NAMES.items()}

MODEL_NAMES: dict[str, TerminalModel] = {
    "SJ6": TerminalModel.SJET_600,
    "TIJ": TerminalModel.SJET_600_TIJ,
    "MINI": TerminalModel.SJET_MINI,
}

NAME_BY_MODEL = {model: name for name, model in MODEL_NAMES.items()}

EXPIRY_WARNING_DAYS = 30
"""How far ahead to warn that a key is running out."""


@dataclass(frozen=True, slots=True)
class HaspKey:
    """What a dongle says it enables."""

    serial: str
    models: frozenset[TerminalModel]
    features: Feature
    max_heads: int | None = None
    expires: date | None = None

    def __post_init__(self) -> None:
        if not self.serial:
            raise LicenseError("licence key has no serial")
        if self.max_heads is not None and self.max_heads < 1:
            raise LicenseError(
                "licence key allows fewer than one head",
                context={"serial": self.serial, "heads": self.max_heads},
            )

    def has(self, feature: Feature) -> bool:
        """True when *feature* is enabled."""
        return bool(self.features & feature)

    def covers_model(self, model: TerminalModel) -> bool:
        """True when this key licenses *model*."""
        return not self.models or model in self.models

    def covers_heads(self, count: int) -> bool:
        """True when this key allows driving *count* heads."""
        return self.max_heads is None or count <= self.max_heads

    def is_expired(self, today: date | None = None) -> bool:
        """True when the key has run out.

        A key expiring today is still good. That matches how the front end
        reads it and it means a shift that started this morning can finish.
        """
        if self.expires is None:
            return False
        return self.expires < (today or date.today())

    def days_left(self, today: date | None = None) -> int | None:
        """Days until expiry, or None when it never expires."""
        if self.expires is None:
            return None
        return (self.expires - (today or date.today())).days

    @property
    def feature_names(self) -> tuple[str, ...]:
        """The enabled features by short name, in table order."""
        return tuple(
            name for name, feature in FEATURE_NAMES.items() if self.features & feature
        )

    @property
    def model_names(self) -> tuple[str, ...]:
        """The licensed machines by short name, in table order."""
        return tuple(name for name, model in MODEL_NAMES.items() if model in self.models)

    def format(self) -> str:
        """The build sheet form. Round trips through :func:`parse_key`."""
        models = SET_SEPARATOR.join(self.model_names) if self.models else UNLIMITED
        features = SET_SEPARATOR.join(self.feature_names) or NEVER_EXPIRES
        heads = UNLIMITED if self.max_heads is None else str(self.max_heads)
        expiry = NEVER_EXPIRES if self.expires is None else self.expires.isoformat()
        return FIELD_SEPARATOR.join([self.serial, models, features, heads, expiry])

    def describe(self) -> str:
        """One line for a report."""
        heads = "any" if self.max_heads is None else str(self.max_heads)
        expiry = "no expiry" if self.expires is None else f"expires {self.expires.isoformat()}"
        return f"{self.serial}: {len(self.feature_names)} features, {heads} heads, {expiry}"


def parse_key(text: str) -> HaspKey:
    """Parse the build sheet form.

    :raises LicenseError: on the wrong number of fields, an unknown model or
        feature name, a bad head count or an unparseable date.
    """
    parts = text.strip().split(FIELD_SEPARATOR)
    if len(parts) != 5:
        raise LicenseError(
            "licence key does not have five fields",
            context={"value": text, "found": len(parts)},
        )
    serial, model_text, feature_text, head_text, expiry_text = (part.strip() for part in parts)

    models: set[TerminalModel] = set()
    if model_text != UNLIMITED:
        for name in model_text.split(SET_SEPARATOR):
            if name not in MODEL_NAMES:
                raise LicenseError(
                    "licence key names a machine this build does not know",
                    context={"value": name, "known": sorted(MODEL_NAMES)},
                )
            models.add(MODEL_NAMES[name])

    features = Feature.NONE
    if feature_text not in (NEVER_EXPIRES, ""):
        for name in feature_text.split(SET_SEPARATOR):
            if name not in FEATURE_NAMES:
                raise LicenseError(
                    "licence key names a feature this build does not know",
                    context={"value": name, "known": sorted(FEATURE_NAMES)},
                )
            features |= FEATURE_NAMES[name]

    if head_text == UNLIMITED:
        heads = None
    elif head_text.isdigit():
        heads = int(head_text)
    else:
        raise LicenseError(
            "licence key head count is not a number",
            context={"value": head_text},
        )

    if expiry_text == NEVER_EXPIRES:
        expires = None
    else:
        try:
            expires = date.fromisoformat(expiry_text)
        except ValueError as exc:
            raise LicenseError(
                "licence key expiry is not an ISO date",
                context={"value": expiry_text},
            ) from exc

    return HaspKey(
        serial=serial,
        models=frozenset(models),
        features=features,
        max_heads=heads,
        expires=expires,
    )


def required_features(profile: TerminalProfile, *, verification: bool = False) -> Feature:
    """The features a terminal with this profile needs.

    Marking is always required. More than one head needs the multi head
    feature. Verification is a separate line item the customer either bought or
    did not, so the caller says.
    """
    needed = Feature.PRINT
    if profile.head_count > 1:
        needed |= Feature.MULTI_HEAD
    if verification:
        needed |= Feature.VERIFY
    return needed


def check_license(
    key: HaspKey | None,
    profile: TerminalProfile,
    *,
    needed: Feature | None = None,
    today: date | None = None,
) -> list[Finding]:
    """Whether *key* covers what *profile* is about to be asked to do."""
    rule = "licence"
    if key is None:
        return [
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="no licence key is attached to this terminal",
                subject=profile.terminal_id,
            )
        ]

    findings: list[Finding] = []
    wanted = needed if needed is not None else required_features(profile)

    if profile.hasp_serial and key.serial != profile.hasp_serial:
        findings.append(
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="the attached key is not the one this terminal is licensed with",
                subject=profile.terminal_id,
                context={"wanted": profile.hasp_serial, "found": key.serial},
            )
        )

    if key.is_expired(today):
        findings.append(
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="the licence key has expired",
                subject=key.serial,
                context={"expired": key.expires.isoformat() if key.expires else ""},
            )
        )
    else:
        remaining = key.days_left(today)
        if remaining is not None and remaining <= EXPIRY_WARNING_DAYS:
            findings.append(
                Finding(
                    rule=rule,
                    severity=Severity.WARNING,
                    message="the licence key is close to expiring",
                    subject=key.serial,
                    context={"days_left": remaining},
                )
            )

    if not key.covers_model(profile.machine):
        findings.append(
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="the licence key does not cover this machine",
                subject=key.serial,
                context={"machine": profile.machine.value, "licensed": list(key.model_names)},
            )
        )

    if not key.covers_heads(profile.head_count):
        findings.append(
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="the licence key allows fewer heads than this terminal has",
                subject=key.serial,
                context={"heads": profile.head_count, "licensed": key.max_heads},
            )
        )

    missing = [
        name
        for name, feature in FEATURE_NAMES.items()
        if (wanted & feature) and not key.has(feature)
    ]
    if missing:
        findings.append(
            Finding(
                rule=rule,
                severity=Severity.ERROR,
                message="the licence key is missing a feature this terminal needs",
                subject=key.serial,
                context={"missing": missing},
            )
        )

    return findings


__all__ = [
    "EXPIRY_WARNING_DAYS",
    "FEATURE_NAMES",
    "MODEL_NAMES",
    "NAME_BY_FEATURE",
    "NAME_BY_MODEL",
    "Feature",
    "HaspKey",
    "check_license",
    "parse_key",
    "required_features",
]
