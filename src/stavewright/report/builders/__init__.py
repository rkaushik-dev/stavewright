"""One builder per thing a command produces, grouped by who reads it.

This was a single module and it imported from twelve subsystems, so anything
that wanted one report pulled in the whole toolkit. Reading a GS1 data file
loaded the compound file reader and the apply executor.

Four modules now, and there is deliberately no re-export here. A caller says
which group it wants and gets that group's imports and no others:

``terminal``
    plan, preflight, apply, drift, stage, plan diff, history.
``quality``
    GS1 data files and print templates.
``installer``
    a built MSI, and an MSI against a project file.
``fleet``
    a plant roster, a rollout, a schedule.

Shared helpers are in :mod:`stavewright.report.shared`.
"""
