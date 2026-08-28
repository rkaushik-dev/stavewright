"""Exception hierarchy.

Every failure the toolkit reports carries a stable code. Bench operators paste
those codes into service tickets and the field engineering group greps for them,
so a code is part of the public surface and does not get renamed once it ships.

Codes are grouped by the stage that raises them:

===========  =========================================================
prefix       stage
===========  =========================================================
``CFG``      configuration loading
``AIP``      reading the Advanced Installer project
``MAN``      manifest construction, serialisation and signing
``DIR``      MSI directory table resolution
``GS1``      element string and dataset validation
``TPL``      ``.spf`` template parsing and linting
``PLN``      plan construction
``PRE``      preflight
``STG``      staging
``APL``      apply and rollback
``VFY``      reconcile
``LIC``      HASP feature gating
===========  =========================================================
"""

from __future__ import annotations

from typing import Any


class StavewrightError(Exception):
    """Base class for everything this package raises deliberately.

    :param message: operator facing description, one sentence, no trailing stop.
    :param code: stable ticket code such as ``DIR0003``.
    :param context: structured detail that the reporters render as a table.
    """

    code = "V4T0000"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.context: dict[str, Any] = dict(context or {})

    def __str__(self) -> str:
        if not self.context:
            return f"[{self.code}] {self.message}"
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"[{self.code}] {self.message} ({detail})"

    def as_dict(self) -> dict[str, Any]:
        """Render for the JSON reporter."""
        return {
            "code": self.code,
            "message": self.message,
            "type": type(self).__name__,
            "context": self.context,
        }


class ConfigError(StavewrightError):
    """A layered configuration file is missing, unreadable or contradictory."""

    code = "CFG0001"


class AipParseError(StavewrightError):
    """``setup.aip`` is not shaped the way the reader expects."""

    code = "AIP0001"


class AipTableMissingError(AipParseError):
    """A table the normaliser depends on is absent from the project."""

    code = "AIP0002"


class ManifestError(StavewrightError):
    """A manifest failed to build, load or serialise."""

    code = "MAN0001"


class ManifestSchemaError(ManifestError):
    """The manifest was written by a toolkit whose schema this one cannot read."""

    code = "MAN0002"


class SignatureError(ManifestError):
    """A detached manifest signature is missing, malformed or does not verify."""

    code = "MAN0003"


class DirectoryResolutionError(StavewrightError):
    """A directory row cannot be turned into an absolute path on the target."""

    code = "DIR0001"


class DirectoryCycleError(DirectoryResolutionError):
    """The directory table has a parent chain that loops."""

    code = "DIR0002"


class Gs1Error(StavewrightError):
    """An element string or a dataset row is not valid GS1."""

    code = "GS10001"


class TemplateError(StavewrightError):
    """A ``.spf`` template will not parse or fails a lint rule."""

    code = "TPL0001"


class PlanError(StavewrightError):
    """The commissioning plan could not be built."""

    code = "PLN0001"


class PlanCycleError(PlanError):
    """Ordering rules produced a dependency cycle."""

    code = "PLN0002"


class PreflightError(StavewrightError):
    """A blocking preflight check failed."""

    code = "PRE0001"


class StageError(StavewrightError):
    """Payload could not be materialised into the staging tree."""

    code = "STG0001"


class ApplyError(StavewrightError):
    """A step failed while being applied to a terminal."""

    code = "APL0001"


class JournalError(ApplyError):
    """The apply journal is unreadable or its hash chain is broken."""

    code = "APL0002"


class RollbackError(ApplyError):
    """Rollback itself failed, which leaves the terminal half commissioned."""

    code = "APL0003"


class VerifyError(StavewrightError):
    """Reconciliation could not run to completion."""

    code = "VFY0001"


class LicenseError(StavewrightError):
    """The attached HASP key does not enable what the plan needs."""

    code = "LIC0001"


__all__ = [
    "AipParseError",
    "AipTableMissingError",
    "ApplyError",
    "ConfigError",
    "DirectoryCycleError",
    "DirectoryResolutionError",
    "Gs1Error",
    "JournalError",
    "LicenseError",
    "ManifestError",
    "ManifestSchemaError",
    "PlanCycleError",
    "PlanError",
    "PreflightError",
    "RollbackError",
    "SignatureError",
    "StageError",
    "StavewrightError",
    "TemplateError",
    "VerifyError",
]
