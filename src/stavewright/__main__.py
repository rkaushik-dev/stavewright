"""Run the toolkit as ``python -m stavewright``.

The installed console script is ``stavewright`` and that is what a bench laptop
uses. This exists for the two places where the script is not on the path: a
checkout that has not been installed, and a CI job that has just built a wheel
into a virtual environment it does not want to activate.

It also gives ``python -m`` one spelling instead of three. Somebody will type
``python -m stavewright`` before they think of ``python -m stavewright.cli.main``,
and having the short one fail while the long one works is a bad afternoon.
"""

from __future__ import annotations

from stavewright.cli.main import run

if __name__ == "__main__":  # pragma: no cover
    run()
