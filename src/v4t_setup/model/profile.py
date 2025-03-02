"""The description of one terminal a bundle is going to be commissioned onto.

A profile is what makes the same bundle resolve to different work on different
machines. The x86 OCX pair goes to ``System32`` on a 32 bit line PC and to
``SysWOW64`` on a 64 bit one, the FPGA bitstream is skipped entirely on a Mini
because there is no controller board in it, and the language DLL set follows
whatever the customer ordered.

Profiles come out of a TOML file on the bench laptop, one per terminal, kept
under revision control by the commissioning group.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from v4t_setup.__about__ import INSTALL_PRODUCT_DIR, INSTALL_VENDOR_DIR
from v4t_setup.model.locale import BASE_LANGUAGE_ID, Language, resolve_all


class Architecture(StrEnum):
    """The bitness of the operating system on the terminal."""

    X86 = "x86"
    X64 = "x64"

    @property
    def has_wow64(self) -> bool:
        """True when 32 bit binaries are redirected away from ``System32``."""
        return self is Architecture.X64


class TerminalModel(StrEnum):
    """Which machine in the range this profile describes."""

    SJET_600 = "s-jet600"
    SJET_600_TIJ = "s-jet600-tij"
    SJET_MINI = "s-jet-mini"

    @property
    def max_heads(self) -> int:
        """How many print heads the controller can drive."""
        return {
            TerminalModel.SJET_600: 4,
            TerminalModel.SJET_600_TIJ: 2,
            TerminalModel.SJET_MINI: 1,
        }[self]

    @property
    def has_fpga(self) -> bool:
        """True when the machine carries the PCDC controller board.

        The Mini drives its single head off the host USB port through the FTDI
        bridge and has no bitstream to load.
        """
        return self is not TerminalModel.SJET_MINI

    @property
    def swath_dots(self) -> int:
        """Vertical dot count of one print head, which bounds template height."""
        return {
            TerminalModel.SJET_600: 384,
            TerminalModel.SJET_600_TIJ: 300,
            TerminalModel.SJET_MINI: 128,
        }[self]


class TerminalProfile(BaseModel):
    """Everything about a terminal that changes what gets installed on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    terminal_id: str = Field(min_length=1, max_length=64)
    """Asset tag on the machine, such as ``LINE-PC-04``. Appears in every log line."""

    machine: TerminalModel = TerminalModel.SJET_600
    architecture: Architecture = Architecture.X64
    os_build: int = Field(default=7601, ge=2600)
    """Windows build number. 7601 is 7 SP1, which most line PCs still run."""

    windows_volume: PureWindowsPath = PureWindowsPath("C:\\")
    windows_directory: PureWindowsPath = PureWindowsPath(r"C:\Windows")
    program_files: PureWindowsPath = PureWindowsPath(r"C:\Program Files")
    desktop_folder: PureWindowsPath = PureWindowsPath(r"C:\Users\Public\Desktop")
    shortcut_folder: PureWindowsPath = PureWindowsPath(
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Sellenis"
    )

    head_count: int = Field(default=1, ge=1, le=4)
    fpga_revision: str | None = None
    """Bitstream revision the controller expects, such as ``PCDC-1.4``."""

    hasp_serial: str | None = None
    """Serial of the dongle plugged into the terminal, when one is fitted."""

    languages: tuple[Language, ...] = ()
    """Resolved on the way in, so a profile always holds table entries."""

    free_space_bytes: int | None = None
    """What the bench measured. None means preflight has to go and look."""

    @field_validator("terminal_id")
    @classmethod
    def _terminal_id_has_no_spaces(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("terminal_id must not be blank")
        if any(ch.isspace() for ch in stripped):
            raise ValueError("terminal_id must not contain whitespace")
        return stripped

    @field_validator("languages", mode="before")
    @classmethod
    def _resolve_languages(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, list | tuple) and not all(
            isinstance(item, Language) for item in value
        ):
            return resolve_all(list(value))  # type: ignore[arg-type]
        return value

    @model_validator(mode="after")
    def _head_count_fits_the_machine(self) -> TerminalProfile:
        if self.head_count > self.machine.max_heads:
            raise ValueError(
                f"{self.machine.value} drives at most {self.machine.max_heads} "
                f"head(s), profile asks for {self.head_count}"
            )
        return self

    @model_validator(mode="after")
    def _fpga_revision_matches_the_machine(self) -> TerminalProfile:
        if self.fpga_revision and not self.machine.has_fpga:
            raise ValueError(f"{self.machine.value} has no FPGA controller to program")
        if self.machine.has_fpga and self.fpga_revision is None:
            raise ValueError(f"{self.machine.value} needs an fpga_revision")
        return self

    @property
    def install_root(self) -> PureWindowsPath:
        """Where the product tree lives, from the AIP directory rows."""
        return self.windows_volume / INSTALL_VENDOR_DIR / INSTALL_PRODUCT_DIR

    @property
    def system_folder(self) -> PureWindowsPath:
        """Where a 32 bit component's ``SystemFolder`` actually lands.

        On x64 the file system redirector sends it to ``SysWOW64``. This is why
        the AIP carries C1Sizer.ocx twice and why ``ocx/reg.bat`` calls
        ``regsvr32`` against ``SysWOW64`` rather than ``System32``.
        """
        leaf = "SysWOW64" if self.architecture.has_wow64 else "System32"
        return self.windows_directory / leaf

    @property
    def system64_folder(self) -> PureWindowsPath:
        """Where a native component lands. Always ``System32``, even on x64."""
        return self.windows_directory / "System32"

    @property
    def language_ids(self) -> tuple[int, ...]:
        """LCIDs in table order, base language included if it was listed."""
        return tuple(entry.lcid for entry in self.languages)

    @property
    def needs_base_language_only(self) -> bool:
        """True when no resource DLL has to be installed at all."""
        return all(entry.lcid == BASE_LANGUAGE_ID for entry in self.languages)

    def with_languages(self, values: list[int | str]) -> TerminalProfile:
        """Return a copy carrying a different language selection."""
        return self.model_copy(update={"languages": resolve_all(values)})

    def describe(self) -> str:
        """One line for a report header."""
        heads = f"{self.head_count} head" + ("s" if self.head_count != 1 else "")
        return f"{self.terminal_id} ({self.machine.value}, {self.architecture.value}, {heads})"


__all__ = ["Architecture", "TerminalModel", "TerminalProfile"]
