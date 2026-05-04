# V4T-Setup

Commissioning toolkit for Sellenis S-Jet 600 coding and marking terminals.

A terminal leaves us as a bare industrial PC. Making it mark product means
putting an FPGA bitstream on the print head controller, staging the FTDI driver
package, planting the VC80 side by side assemblies with their publisher
policies, registering two OCX controls into both the native and the WOW64 hive,
installing the language resource DLLs the customer ordered, dropping the GS1
template set they signed off, and writing the product registry block.

The order matters, most of it is not reversible by hand once a line is running,
and until now the only description of it was `setup.aip`, which you cannot diff
or test.

## What is in here

    setup.aip                  the Advanced Installer project, since 2016
    Matricode/                 the payload that ships with it
    msi-quattro/, ocx/         the built installer and the controls
    src/v4t_setup/             this toolkit
    tests/                     its tests

The 2016 tree is the input, not decoration. The toolkit reads the real project
file and the real templates, and the tests assert against them rather than
against fixtures.

## The flow

    bundle build      setup.aip            -> manifest
    bundle sign       manifest             -> detached signature
    bundle stage      manifest + payload   -> a staged bundle on the laptop
    bundle pack       a staged bundle      -> one file to carry to a plant
    bundle unpack     that file            -> the bundle back, every object hashed
    run plan          bundle + profile     -> an ordered list of steps
    run preflight     plan + terminal      -> whether it is safe to start
    run apply         bundle + terminal    -> a commissioned terminal, journalled
    run verify        bundle + terminal    -> what has drifted since
    run diff          two manifests        -> what a re-commission would do
    run history       the journals         -> what has been done to a terminal
    run audit         an attestation       -> whether it stands up as evidence
    bundle msi        a built .msi         -> what is inside it
    bundle match      an .msi + setup.aip  -> whether one built the other
    fleet status      roster + journals    -> where the plant actually stands
    fleet plan        roster + profiles    -> the order to work the plant in
    fleet next        roster + profiles    -> the terminals to do tonight

`gs1` and `template` are separate groups for the quality side, and neither
needs a terminal. `fleet` does not need one either: it answers what order to
commission a plant in, which is a planning question rather than a bench one.

## The plant roster

A roster says where each terminal sits and what stopping it costs. It lives
beside the profiles and references them by terminal id:

    plant = "Wrexham"

    [line.export]
    criticality = "critical"
    shift_pattern = "continuous"

    [line.export.terminal.LINE-PC-04]
    position = 1
    commissioned = 2024-11-03

    [line.export.terminal.LINE-PC-05]
    position = 2

`criticality` is `critical`, `normal` or `spare`, and it decides how many
machines may be down at once: one, three, eight. `position` is upstream to
downstream, so position 1 feeds position 2.

    v4t-setup fleet plan roster.toml
    v4t-setup fleet next roster.toml --batch 2

The maintenance calendar is its own file, because it changes every month and
the roster does not:

    [[window]]
    label = "monday night"
    start = 2025-06-02T22:00:00Z
    end = 2025-06-03T02:00:00Z

    v4t-setup fleet schedule roster.toml calendar.toml --engineers 2

That exits 1 when a wave fits in no window, which is the answer it is there to
give. A wave never spans two windows: half the steps in a commissioning run
cannot be rolled back, so a machine left part done at the end of a shift is
somebody's morning gone.

A wave never takes two machines off one line and never mixes criticalities,
and the spares go first so the bundle is proved against real hardware before
anything that matters is touched.

## Getting started

    python -m pip install -e ".[dev]"
    v4t-setup --help

Read the shipped project file:

    v4t-setup bundle inspect setup.aip

Check the shipped GS1 data. It has known findings, see below:

    v4t-setup gs1 check Matricode/Quattro/Templates/GS1-1.txt

Plan a terminal without touching anything:

    v4t-setup bundle build setup.aip --out manifest.json --no-digest
    v4t-setup run plan manifest.json --profile LINE-PC-04.toml --aip setup.aip

## Terminal profiles

One TOML file per line, sharing what the machines have in common:

    machine = "s-jet600"
    architecture = "x64"
    fpga_revision = "PCDC-1.4"
    languages = ["pl-PL"]

    [terminals.LINE-PC-04]
    head_count = 2

    [terminals.LINE-PC-09]
    machine = "s-jet-mini"
    clear = ["fpga_revision"]

The profile is what makes one bundle resolve to different work on different
machines. An x86 control goes to `System32` on a 32 bit line PC and to
`SysWOW64` on a 64 bit one. A Mini gets no bitstream because it has no
controller. A terminal only gets the language DLLs it was ordered with.

## Settings

`v4t-setup.toml`, found by walking up from the working directory:

    staging_root = "D:/commissioning/_staging"
    journal_root = "D:/commissioning/_journal"
    trusted_keys = "D:/commissioning/keys"
    require_signature = true

    [waived_checks]
    code-page = "this line runs two languages on purpose"

A bench that is set up properly needs no flags, so the difference between two
runs is the terminal and not what somebody remembered to type.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | pass, or warnings only |
| 1 | findings the operator has to act on |
| 2 | the toolkit could not do the job: a missing file, a bad profile |

A build server that tells 1 and 2 apart can retry the second and page somebody
for the first.

## The MSI in this repository was not built from the project file in it

    v4t-setup bundle match msi-quattro/msi-Jet600.msi setup.aip

says so. Different upgrade code, different product name, and the manufacturer
is still `Default Company Name`. The comparison is by identity and table
membership rather than by digest, because two builds of the same project differ
in every byte and are still the same product.

## Known findings in the shipped payload

These are real and they are why some of the tests look the way they do.

- **`GS1-1.txt` carries a GTIN with the wrong check digit.** `00125682945601`
  appears in all 130 rows; the check digit should be `6`, not `1`. The other
  GTIN in the file, `01001000173250`, is correct. `gs1 check` reports it and
  exits 1, and CI asserts that it still does.
- **32 of those 130 rows stop before the `EAN13` column**, and the 98 that
  carry it hold twelve digits rather than thirteen.
- **`T1.spf` binds a text field to `ERT`**, which that data file has no column
  for, and its barcode block still holds its all zero placeholders.
- **The product registry key is `Software\Matricode\V4T`**, not under Sellenis.
  It predates the rebrand and the front end still reads it by that name.
- **The AIP's source paths climb out of the project directory** into
  `..\Release`. A bundle is carried to a factory, so nothing in it can point
  outside itself, and `bundle build` flattens them under `payload/`.

## Design notes worth knowing before changing anything

**Nothing talks to a machine except `v4t_setup.target`.** There are two
implementations, one that drives Windows and one that keeps everything in a
dictionary. The second is not a mock: it enforces the same invariants,
including case insensitive paths and registry keys, and refusing to register a
control that is not there. A dry run is a real run against it, so there is no
`if dry_run` anywhere in an action.

**Every action is idempotent and every one reports whether it changed
anything.** That is what makes a resumed run correct, because after an
interruption nobody knows how far the last step got.

**The journal is hash chained.** A rollback is driven off it, and a rollback
driven off a journal that has lost records leaves files on the terminal nobody
knows about.

**Two steps cannot be rolled back**, and they say so rather than pretending.
Writing a bitstream overwrites what was there, and the VC80 redistributable
goes into a refcounted store shared with everything else on the PC. A rollback
stops when it reaches one.

## Development

    python -m ruff check src tests
    python -m mypy
    python -m pytest

All three run in CI on Windows and Linux against Python 3.11 and 3.12, along
with a wheel build that installs into a clean environment and runs the CLI.

## Licence

Proprietary. Sellenis Marking Systems.
