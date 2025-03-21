"""Product identity constants shared by the toolkit and the packages it writes.

The values here mirror the Advanced Installer project at the repository root.
``setup.aip`` is the authority for what a released S-Jet 600 image contains, so
whenever a constant below is also present in the AIP the loader cross checks it
and refuses to build a manifest when the two disagree.
"""

from __future__ import annotations

from typing import Final

__version__: Final[str] = "0.1.0"
"""Toolkit version. Independent of the product version it installs."""

MANUFACTURER: Final[str] = "Sellenis"
"""Value of the MSI ``Manufacturer`` property."""

PRODUCT_NAME: Final[str] = "S-Jet 600"
"""Value of the MSI ``ProductName`` property."""

PRODUCT_VERSION: Final[str] = "1.0.0"
"""Value of the MSI ``ProductVersion`` property in the shipped AIP."""

UPGRADE_CODE: Final[str] = "{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}"
"""Stable across every S-Jet 600 release. Never regenerate this."""

PRODUCT_LANGUAGE: Final[int] = 1033
"""Base language of the MSI package. Everything else ships as a resource DLL."""

INSTALL_VENDOR_DIR: Final[str] = "Sellenis"
"""First path segment under the Windows volume, from ``Matricode_Dir``."""

INSTALL_PRODUCT_DIR: Final[str] = "S-jet600"
"""Second path segment, from ``Quattro_Dir``. The casing is deliberate."""

TERMINAL_EXECUTABLE: Final[str] = "V4T.exe"
"""Front end binary. Its presence is how a terminal is detected as commissioned."""

MANIFEST_SCHEMA_VERSION: Final[int] = 1
"""Bumped only when a manifest written by an older toolkit stops loading."""

JOURNAL_SCHEMA_VERSION: Final[int] = 1
"""Bumped only when a journal written by an older toolkit stops replaying."""

PRODUCT_CODE: Final[str] = "{BA4D6ED3-C916-4F0E-BB5B-A197DA437380}"
"""Changes with every release. Only the upgrade code is stable across them."""

REGISTRY_PRODUCT_KEY: Final[str] = r"Software\Matricode\V4T"
"""Where ``ProductInformation``, ``Path`` and ``Version`` land.

Not under Sellenis, and not under the product name either. The key predates the
rebrand and the front end still reads it by this name, so it stays put. An
earlier version of this file guessed at ``SOFTWARE\\Sellenis\\S-jet600``, which
made every reconcile report a commissioned terminal as untouched.
"""

REGISTRY_ACCOUNT_KEY: Final[str] = r"Software\Matricode\V4T\account"
"""Holds the operator account value the front end checks at start up."""

DESKTOP_SHORTCUT_NAME: Final[str] = "S-jet 600"
"""Name of the desktop shortcut, from the ``Sjet600`` row."""

START_MENU_SHORTCUT_NAME: Final[str] = "M-JetQuattro2015"
"""Name of the Start menu shortcut. Also predates the rebrand."""

BASE_INSTALL_LEVEL: Final[int] = 100
"""``INSTALLLEVEL`` for a normal commissioning run."""

__all__ = [
    "BASE_INSTALL_LEVEL",
    "DESKTOP_SHORTCUT_NAME",
    "INSTALL_PRODUCT_DIR",
    "INSTALL_VENDOR_DIR",
    "JOURNAL_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "MANUFACTURER",
    "PRODUCT_CODE",
    "PRODUCT_LANGUAGE",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "REGISTRY_ACCOUNT_KEY",
    "REGISTRY_PRODUCT_KEY",
    "START_MENU_SHORTCUT_NAME",
    "TERMINAL_EXECUTABLE",
    "UPGRADE_CODE",
    "__version__",
]
