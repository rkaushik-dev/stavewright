"""The unit of work a commissioning run performs, and the plan they make up.

A step is deliberately small and deliberately declarative. It says what should
be true afterwards, not how to get there, because the same step has to be
applyable, reversible, checkable and printable, and only the first of those
wants a procedure.

``provides`` and ``requires`` are free text tags rather than step ids. A rule
that says "the OCX registration needs the VC80 CRT" should not have to know
which step id happens to install it in this particular bundle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from v4t_setup.clock import now as utc_now
from v4t_setup.errors import PlanError
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.util.digest import Digest


class StepKind(StrEnum):
    """What a step does. The apply layer has one action class per member."""

    CREATE_DIRECTORY = "create-directory"
    COPY_FILE = "copy-file"
    WRITE_REGISTRY = "write-registry"
    REGISTER_COM = "register-com"
    INSTALL_SXS_ASSEMBLY = "install-sxs-assembly"
    INSTALL_SXS_POLICY = "install-sxs-policy"
    STAGE_DRIVER = "stage-driver"
    PROGRAM_FPGA = "program-fpga"
    INSTALL_FONT = "install-font"
    CREATE_SHORTCUT = "create-shortcut"
    SET_PATH = "set-path"
    INSTALL_TEMPLATE_SET = "install-template-set"
    INSTALL_LANGUAGE_PACK = "install-language-pack"
    RUN_REDISTRIBUTABLE = "run-redistributable"
    REMOVE_OLD_PRODUCT = "remove-old-product"


IRREVERSIBLE_KINDS = frozenset(
    {
        StepKind.PROGRAM_FPGA,
        StepKind.RUN_REDISTRIBUTABLE,
        StepKind.REMOVE_OLD_PRODUCT,
    }
)
"""Steps a rollback cannot undo.

Writing a bitstream to the controller overwrites the previous one and there is
nowhere to keep it. Running the VC80 redistributable installs into the machine
wide side by side store, which is refcounted and shared with anything else on
that PC. Removing an old product is done by its own uninstaller.
"""


@dataclass(frozen=True, slots=True)
class Step:
    """One thing that has to be true on the terminal when the run finishes."""

    step_id: str
    kind: StepKind
    title: str
    component: str | None = None
    target: str | None = None
    source: str | None = None
    digest: Digest | None = None
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    weight: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.step_id:
            raise PlanError("step id must not be empty")
        if not self.title:
            raise PlanError("step must have a title", context={"step": self.step_id})

    @property
    def reversible(self) -> bool:
        """True when rollback can undo this step."""
        return self.kind not in IRREVERSIBLE_KINDS

    def as_dict(self) -> dict[str, Any]:
        """Canonical form, for the plan digest and the journal."""
        payload: dict[str, Any] = {
            "step_id": self.step_id,
            "kind": self.kind.value,
            "title": self.title,
        }
        for name in ("component", "target", "source"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.digest is not None:
            payload["digest"] = str(self.digest)
        if self.requires:
            payload["requires"] = list(self.requires)
        if self.provides:
            payload["provides"] = list(self.provides)
        if self.weight:
            payload["weight"] = self.weight
        if self.detail:
            payload["detail"] = self.detail
        return payload

    def describe(self) -> str:
        """One line for a plan listing."""
        where = f" -> {self.target}" if self.target else ""
        return f"{self.kind.value}: {self.title}{where}"


@dataclass
class Plan:
    """An ordered list of steps for one terminal."""

    profile: TerminalProfile
    steps: list[Step] = field(default_factory=list)
    created_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.steps)

    def __getitem__(self, position: int) -> Step:
        return self.steps[position]

    def __contains__(self, step_id: str) -> bool:
        return any(step.step_id == step_id for step in self.steps)

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps)

    def of_kind(self, kind: StepKind) -> list[Step]:
        """Every step of *kind*, in plan order."""
        return [step for step in self.steps if step.kind is kind]

    def get(self, step_id: str) -> Step | None:
        """One step by id, or None."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def require(self, step_id: str) -> Step:
        """One step by id.

        :raises PlanError: when the plan has no such step.
        """
        step = self.get(step_id)
        if step is None:
            raise PlanError("plan has no such step", context={"step": step_id})
        return step

    def position_of(self, step_id: str) -> int:
        """Where *step_id* sits in the plan.

        :raises PlanError: when the plan has no such step.
        """
        for position, step in enumerate(self.steps):
            if step.step_id == step_id:
                return position
        raise PlanError("plan has no such step", context={"step": step_id})

    @property
    def irreversible_steps(self) -> list[Step]:
        """Steps a rollback would have to leave in place."""
        return [step for step in self.steps if not step.reversible]

    def counts(self) -> dict[str, int]:
        """How many steps of each kind, in plan order of first appearance."""
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.kind.value] = counts.get(step.kind.value, 0) + 1
        return counts

    def canonical(self) -> str:
        """The bytes the plan digest is taken over.

        The profile's terminal id is deliberately left out. Two terminals with
        the same model, architecture and language set get the same plan digest
        and so share a staging tree, which is the point of having one.
        """
        payload = {
            "machine": self.profile.machine.value,
            "architecture": self.profile.architecture.value,
            "head_count": self.profile.head_count,
            "fpga_revision": self.profile.fpga_revision,
            "languages": list(self.profile.language_ids),
            "steps": [step.as_dict() for step in self.steps],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def digest(self) -> Digest:
        """A stable identifier for this plan's content."""
        return Digest("sha256", hashlib.sha256(self.canonical().encode("utf-8")).hexdigest())

    def describe(self) -> str:
        """One line for a report header."""
        stamp = self.created_at.isoformat() if self.created_at else "unstamped"
        return (
            f"{self.profile.describe()}: {len(self.steps)} steps, "
            f"plan {self.digest().short}, {stamp}"
        )


__all__ = ["IRREVERSIBLE_KINDS", "Plan", "Step", "StepKind", "utc_now"]
