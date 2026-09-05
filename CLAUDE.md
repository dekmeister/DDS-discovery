# DDS Learning Lab — Vessel Traffic Service

## What this is

A teaching repository for understanding DDS. It is NOT a product, NOT a
framework, and NOT intended to be reusable. Its output is understanding,
recorded in `labs/*/findings.md`.

If a change makes the code cleaner but makes DDS behaviour less visible, it
is the wrong change.

## The domain

A simplified Vessel Traffic Service. Six actors, five topics, one domain.

| Actor | Publishes | Subscribes |
|---|---|---|
| `shore_station.py` (run 2+ instances) | `VesselReport`, `StationHealth` | `Zone` |
| `fusion.py` | `Track` | `VesselReport`, `OperatorCommand` |
| `zone_service.py` | `Zone` | — |
| `console.py` | `OperatorCommand` | `Track`, `Zone`, `StationHealth` |
| `monitor.py` | — | built-in discovery topics only |
| `sim.py` | (library, not a process) | — |

`sim.py` generates plausible vessel motion. It contains no DDS code.

## Topic QoS — the delivery contract

This table is the contract. It belongs in every design discussion, and
changing a row is a design decision that must be recorded in the lab's
`findings.md`, not made silently to get something working.

| Topic | Key | Reliability | Durability | History | Deadline | Ownership |
|---|---|---|---|---|---|---|
| `VesselReport` | `mmsi` | BEST_EFFORT | VOLATILE | KEEP_LAST(1) | 3 s | EXCLUSIVE + strength |
| `Track` | `track_id` | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST(1) | 5 s | SHARED |
| `Zone` | `zone_id` | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST(1) | — | SHARED |
| `OperatorCommand` | none | RELIABLE | VOLATILE | KEEP_ALL | — | SHARED |
| `StationHealth` | `station_id` | BEST_EFFORT | VOLATILE | KEEP_LAST(1) | 2 s + LIVELINESS | SHARED |

## Keying — do not change without asking

`VesselReport` is keyed on `mmsi` **alone**. `station_id` is a non-key
field. Two stations with overlapping coverage therefore write the *same*
instance, which is what makes per-instance OWNERSHIP arbitration possible.

Keying on `(mmsi, station_id)` would produce two independent instances, no
contention, and would silently delete the entire point of Lab 04. If this
looks like a modelling error to you, it is not — say so and stop, do not
"correct" it.

`OperatorCommand` is deliberately unkeyed. See the comment in the IDL.

Cyclone's default `WriterDataLifecycle(autodispose=True)` silently converts
`unregister` into `dispose`, erasing exactly the "one station looked away, the
other still sees her" distinction this keying exists for (measured in Lab 01);
set it False on any topic more than one station writes.

## Hard constraints

- Python 3.13, `cyclonedds` (Eclipse Cyclone DDS binding), standard library.
  No other runtime dependencies without asking first. `paho-mqtt` and
  `pyzmq` are permitted in Lab 05 only.
- **No abstraction over the DDS API.** No `DDSHelper`, no `PubSubBase`, no
  factory functions that hide entity creation. Every `DomainParticipant`,
  `Topic`, `DataWriter`, `DataReader` and `Qos` construction appears
  literally in the file that uses it, even when that means repetition across
  files. Repetition is the point: I need to see the entity tree every time.
- **Reporting scaffolding is shared, in `src/vtslab/report.py`.** From Lab 01
  on, anything an earlier lab already established and that only *observes* is
  imported from there rather than re-typed: the monotonic `say()`, `dump_qos`,
  the policy-id and sample/view/instance state name tables, `states_of`, and
  the `WriterListener`/`ReaderListener` that print every status.
  The boundary is absolute and runs in one place: **if it constructs or
  configures a DDS entity, it stays in the lab file.** Entity trees, `Qos(...)`
  with its why-comments, and `ReadCondition` masks are never imported from
  anywhere. The test to apply: moving a print helper out still leaves the lab
  file showing the whole experiment; moving a writer's QoS out does not.
  A lab that needs to *react* to a status rather than report it writes its own
  listener in its own file — that is experiment, not scaffolding.
- **Every QoS policy carries a comment saying why**, in terms of the data,
  not the policy name. `# stale positions are worse than missing ones` is
  useful. `# set reliability to best effort` is not.
- No async framework. Threads and blocking calls only.
- No web UI, no dashboard, no TUI. `print()` to stdout is the interface.
- Types come from `idl/vts.idl` via `idlc -l py`. Do not hand-write the
  generated dataclasses, and do not edit generated output.

## Anti-patterns — do not do these

- Do not add retry loops, reconnection logic, or `try/except` around DDS
  calls. That masks the exact behaviour being studied.
- Do not "fix" a lab that produces no data. **Several labs are designed to
  produce nothing.** A QoS mismatch producing silence is a successful
  result. Report it; do not repair it.
- Do not tune QoS to make something work. If you believe a QoS value is
  wrong, stop and say so before changing it.
- Do not add logging frameworks. Plain prints with monotonic timestamps.
- Do not rework a lab once it is committed. Lab 00 keeps its own copies of
  everything now in `src/vtslab/report.py` and is not to be updated to import
  them; a committed lab stays runnable exactly as committed. New shared
  scaffolding is adopted going forward only.
- Do not duplicate the reporting scaffolding above. Labs may still duplicate
  each other freely in everything else, and must, for anything DDS-facing.
- Do not add tests unless the lab explicitly asks for a harness.
- Do not add type hints beyond what aids reading, and never add a typing
  layer over the generated IDL types.

## Working method

1. **Explain before coding.** When asked "what will happen if...", answer
   and state your confidence BEFORE writing any code. I am using the gap
   between your prediction and the observed result as a learning signal, so
   a confident wrong answer is more useful to me than a hedge.
2. **My prediction goes first, always.** Create the lab README with an empty
   "Prediction" section and stop there. Do not state your own prediction —
   not in the file, not in conversation — until I have written mine down.
   Once mine is recorded, add yours in a separate section below it. A
   prediction I write after reading yours measures nothing, and "before
   writing any code" does not mean before I have had my turn.
3. One lab per session. Do not start the next one, and do not scaffold
   ahead.
4. Every script prints, on startup, what it is about to do and the full QoS
   it is using. Reading stdout alone should explain the experiment.
5. Always read and print the relevant status conditions:
   `subscription_matched`, `publication_matched`,
   `requested_incompatible_qos` (including the *policy id* that failed),
   `offered_incompatible_qos`, `requested_deadline_missed`,
   `liveliness_changed`, `sample_lost`, `sample_rejected`. Silent success
   and silent failure must be distinguishable from stdout.
6. Print `sample_state`, `view_state` and `instance_state` alongside every
   sample in any lab that touches lifecycle.
7. Each lab is a separate commit. This enables each to be self-contained for
   easy review and for others to follow along.

## Repository layout

```
idl/vts.idl          the interface definition (the ICD)
src/vtslab/          the actor processes; config.py holds the domain ID
src/vtslab/report.py printing and status listeners shared from Lab 01 on
labs/labNN_name/     README.md (question + my prediction), run.sh, findings.md
configs/             Cyclone DDS XML variants
captures/            pcaps, kept with the lab that produced them
```

## Files you must not touch

- `labs/*/findings.md` — my observations, written by hand.
- The "Prediction" section of any `labs/*/README.md`.
- Anything under `captures/`.

## Environment notes

- Pin `cyclonedds` in `requirements.txt`. The binding trails Python
  releases; verify the installed version actually supports the interpreter in
  use rather than assuming it. Checked for 11.0.1: it publishes a cp313
  manylinux wheel bundling libddsc, an IDL compiler and the `cyclonedds` CLI.
  0.10.5 stops at cp310, which is what "trails Python releases" looks like.
- **The wheel's bundled `idlc` does not run on this host** (measured in Lab 01,
  correcting an earlier note that assumed it did). It segfaults on every
  invocation, `--version` included: its ELF headers are auditwheel/patchelf
  rewritten and the glibc loader dies on them before `main`. The wheel's shared
  libraries are unaffected. `idlc` 11.0.1 is therefore built from upstream
  source into gitignored `.tools/` by `make tools`, and used only for code
  generation — the labs still link the wheel's `libddsc`. `idlc -l py` loads
  its backend by running `python3 -m cyclonedds.__idlc__`, so `.venv/bin` must
  be on `PATH`; its `-o` flag is ignored by that backend, so `make generate`
  cds into the output directory instead.
- Use a project-specific domain ID (not 0) set in exactly one place. Domain
  0 is shared with anything else on the segment, including other people's
  test rigs.
- Never assume Cyclone's default SPDP interval or lease duration. Read them
  from configuration or measure them. Lab 03 measures them.
- `CYCLONEDDS_URI` points at the XML config. Labs that need a non-default
  config set it in their own `run.sh`, never globally.
