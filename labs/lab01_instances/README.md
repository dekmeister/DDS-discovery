# Lab 01 — meet the instance model

## The question

Lab 00 was about the shape of the system. This lab is about the shape of the
*data*: what a DDS **instance** is, and what the distinct ways of ending one
look like from the far side.

`VesselReport` is keyed on `mmsi`. One MMSI is one instance, and the reader
keeps state per instance whether or not any sample is in flight. So:

1. Three vessels written by one station on one topic — how many instances does
   the reader see, and what does `KEEP_LAST(1)` actually leave in its cache?
2. `read()` and `take()` differ in one word of the spec. What does that word do
   to the same five samples read five times?
3. **An instance can end in three ways. Are they distinguishable?**

The third is the point of the lab. Three endings, and what causes each:

| ending | how it is triggered | writer call |
|---|---|---|
| the vessel left coverage | `leave <mmsi>` on the station's stdin | `unregister_instance()` |
| the report was spurious | `bogus <mmsi>` on the station's stdin | `dispose()` |
| the station crashed | `kill -9` from outside | none — the writer stops existing |

What gets recorded for each is the resulting `instance_state` **and how long it
took to appear**. Two of these are a message on the wire; one of them is an
absence being noticed, and an absence takes as long as the discovery lease says
it takes. That number comes back in Lab 03.

A fourth run repeats `leave` with `WriterDataLifecycle(autodispose=False)`, so
that policy is isolated as a variable rather than assumed to be irrelevant.

## Prediction

1. Each vessel is a separate instance regardless of the origin, so the reader carries three instances and KEEP_LAST(1) has one (the most recent) copy of the data for each instance (so three in total).
2. read() does not remove the sample from the cache, while take() does. This means that acting five times on the sample for read() returns the same data five times (provided nothing else is received to supersede it), while take() only reads once and then there is nothing in the cache to consume.
3. The instance can end with leave, bogus or kill. They are distinguishable as leave and bogus require something to tell the reader that this has happened (something on the stdin). Kill I assume is just the other cases.

## Claude's prediction, recorded before the code was written

**1. Three instances, three retained samples.** The reader sees three
instances, and after ~18 writes with nobody reading, the first `read()` pass
returns **3 samples, not 1 and not 18**. `KEEP_LAST(1)` is a depth of one *per
instance*, not one per reader — that is the whole reason the depth is written
next to a key in the QoS table. Confidence: high.

The five passes should read `3, 3, 3, 3, 3`, with pass 1 showing
`sample_state=NotRead view_state=New` and passes 2-5 showing
`sample_state=Read view_state=Old`. `view_state=New` means "this instance is
new to you", so it flips on first sight of the instance and never returns.
BEST_EFFORT is doing something invisible here too: the ~15 samples that did not
survive were not lost in the network, they were overwritten in the reader's
cache by their own successors, and `sample_lost` should stay at 0 because
nothing was lost in the sense DDS means by that word. Confidence: high, less so
on `sample_lost` staying exactly 0.

**2. take() empties, read() does not** — as you have it. `take(N=100)` returns
3, the following `read(N=100)` returns 0. The instances still exist afterwards;
it is the samples that are gone. Confidence: high.

**3. The three endings are NOT three distinct states.** This is where I expect
your third point to come apart, and it is the reason for the fourth run:

| ending | `instance_state` | latency |
|---|---|---|
| `leave` (unregister) | `NotAliveDisposed` | milliseconds |
| `bogus` (dispose) | `NotAliveDisposed` | milliseconds |
| `kill -9` | `NotAliveNoWriters` | seconds — the lease |
| `leave`, `--no-autodispose` | `NotAliveNoWriters` | milliseconds |

Two claims in that table are worth arguing about:

- **`leave` and `bogus` should be indistinguishable by state**, both landing on
  `NotAliveDisposed`, even though they mean opposite things ("she sailed out of
  range" vs "she was never there"). Cyclone's default
  `WriterDataLifecycle(autodispose_unregistered_instances=True)` turns the
  unregister into a dispose on the way out. The semantic distinction the
  application cares about is erased by a policy nobody set. Confidence: high —
  and if it holds, row 4 is the proof, because turning autodispose off should
  move `leave` alone to `NotAliveNoWriters` while leaving its timing unchanged.
- **The kill is distinguishable by both state and latency**, and the latency is
  the interesting number. Nothing goes on the wire; the reader concludes it
  from the writer's *silence* once the participant lease expires. I expect
  order **10 s**, and I am least sure of this of anything here — call it 60%
  that it lands in 5-20 s, and it is exactly the number CLAUDE.md forbids
  assuming, which is why Lab 03 measures it properly.

**The second clock.** `requested_deadline_missed` should fire ~3 s after the
last write in the kill run — the DEADLINE from the QoS table — so the reader
knows the data stopped *long before* it knows the writer is gone. Two different
questions with two different answers, on the same event. Confidence: high.

**All three endings arrive as samples with no valid data**, key only, which is
why the watcher has to handle `InvalidSample` to see any of this at all.
Confidence: high for `leave`/`bogus`, moderate for the kill.

**Where I disagree with you:** your point 3 says the endings are distinguishable
because two announce themselves and one does not. The announcement *is* the
right axis — but I predict it does not buy you two distinct states, only one
distinct state plus a delay. `leave` and `bogus` both announce, and both
announce the same thing.

## Running it

```sh
make generate                     # once, from the repo root — see below
./labs/lab01_instances/run.sh
```

Logs land in `out/<UTC timestamp>/` and are gitignored; quote what matters into
`findings.md`. The whole script takes about four minutes, most of it spent
waiting out a lease in run 3c.

`run.sh` reads the domain ID from `src/vtslab/config.py` rather than restating
it, and sets no `CYCLONEDDS_URI` — discovery timing is the subject of run 3c,
so it had better be Cyclone's own and not something this lab arranged.

## What the programs do

Both programs import their printing, name tables and status listeners from
`src/vtslab/report.py` — established in Lab 00, unchanged since, and nothing in
it touches the DDS API. What is left in each lab file is the entity tree, the
QoS with its why-comments, the read conditions and the experiment itself, which
is the part worth reading. Lab 00 keeps its own copies and is not revisited.

`station.py` writes N vessels as N instances of one topic from one writer, with
the `VesselReport` row of the ICD's QoS table stated literally. It reads
`leave <mmsi>` and `bogus <mmsi>` from its own stdin — so the writer that ends
an instance is the writer that created it — and prints an `ACTION` line with a
monotonic stamp on either side of the call.

`watcher.py` prints every sample with all three state fields, and prints a
`TRANSITION` line whenever an instance's state changes. Those `TRANSITION`
lines are what `run.sh` times, against a monotonic clock it stamps itself at
the instant it issues each ending. `CLOCK_MONOTONIC` is system-wide, so the
subtraction between two processes' logs is a measurement rather than a guess.

Every ending arrives as a sample with **no valid data** — key only. A program
that does not handle `InvalidSample` cannot see any of this lab's subject
matter at all.

Two runs exist to isolate a single policy, and each changes exactly one thing:

- **run 4** repeats `leave` with `WriterDataLifecycle(autodispose=False)`.
- **run 5** repeats the crash with the *reader's* `Deadline` removed, because
  run 3c produced no instance-state change at all and one variable had to be
  moved to find out why. It is a diagnostic, not a correction: the ICD's QoS
  table is unchanged and every other run in this lab uses the 3 s deadline.

## Environment note — the bundled `idlc` does not run on this host

Lab 01 is the first lab to use generated types, so it is the first to need
`idlc`. The one inside the `cyclonedds` 11.0.1 wheel segfaults here on every
invocation, including `--version`: its ELF headers are auditwheel/patchelf
rewritten (non-PIE `EXEC` plus an extra RW `PT_LOAD` at `0x3ff000` carrying the
program headers) and the glibc loader dies on them before `main`. Re-running
`patchelf` over a copy does not repair it. The wheel's shared libraries are
fine — Lab 00 ran against them, and so does this lab.

So `idlc` 11.0.1 is built from upstream source into gitignored `.tools/`, and
only *code generation* uses it. The DDS runtime is still the wheel's `libddsc`.

```sh
make tools      # clone and build idlc 11.0.1 into .tools/   (needs network)
make generate   # idl/vts.idl -> src/vtslab/generated/vts/
```

Two details worth knowing before trusting the output:

- `idlc -l py` does not link its Python backend. It shells out to
  `python3 -m cyclonedds.__idlc__` and loads the path that prints — so the
  backend actually used is the *wheel's* `_idlpy`, and `.venv/bin` has to be on
  `PATH` or the only symptom is `cannot load generator py`.
- `idlc`'s `-o` is accepted and then ignored by that backend, which writes its
  package into the current working directory. `make generate` cds instead.

Nothing under `src/vtslab/generated/` is hand-edited, and it is gitignored:
`idl/vts.idl` is the ICD and the only part of it worth reviewing.
