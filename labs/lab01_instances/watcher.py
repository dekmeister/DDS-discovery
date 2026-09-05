#!/usr/bin/env python3
"""Lab 01 watcher: prints every sample and every instance-state change it sees.

    DomainParticipant -> Topic("VesselReport") -> Subscriber -> DataReader

The station ends instances; this program is where the ending becomes visible.
Three things are printed for every sample — sample_state, view_state,
instance_state — because those three, not the payload, are what this lab is
about. An instance that ends arrives as a sample with NO valid data: key only,
and a new instance_state. If a program does not handle that, the ending is
simply invisible to it.

Three modes:

    (default)   take() in a loop, printing samples and TRANSITION lines
    --read      go deaf for --probe-delay s, then read() the same cache 5x
    --take      go deaf for --probe-delay s, then take() once and read() after

Usage:  watcher.py [--seconds N] [--read | --take] [--probe-delay S]
                   [--no-deadline]
"""

import signal
import sys
import time
from pathlib import Path

# Labs run as loose scripts, not as an installed package. src/ holds the one
# and only domain ID; src/vtslab/generated holds the types idlc wrote, and it
# has to be on the path in its own right because the generated module imports
# itself by its root package name (`import vts`).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "src" / "vtslab" / "generated"))

from cyclonedds.core import InstanceState, Policy, Qos, ReadCondition, SampleState, ViewState
from cyclonedds.domain import DomainParticipant
from cyclonedds.internal import InvalidSample
from cyclonedds.sub import DataReader, Subscriber
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from vtslab.config import DOMAIN_ID
# Printing, name tables and the status listener: established in Lab 00 and
# unchanged since, so they live in one place rather than being re-typed here.
# Nothing in that module touches the DDS API — the entity tree, the QoS and the
# read conditions below are the parts of this file worth reading, and they stay
# literal. INSTANCE_STATES is imported because this lab compares against it.
from vtslab.report import (INFINITY_NS, INSTANCE_STATES, START, ReaderListener,
                           dump_qos, say, states_of)

# Generated from idl/vts.idl by `make generate`. Same topic and type name as
# Lab 00's hand-written declaration, so the two would still match each other.
from vts import Position, VesselReport  # noqa: F401  (Position is part of the type)


stop = False
last_state = {}   # mmsi -> instance_state name, so a change can be spotted


def request_stop(signum, _frame):
    global stop
    stop = True
    say(f"signal {signal.Signals(signum).name} received, stopping")


def note(sample):
    """Print one sample, and a TRANSITION line if its instance changed state."""
    info = sample.sample_info
    if isinstance(sample, InvalidSample):
        # No payload. The instance changed state and only the key came back.
        # Every one of this lab's three endings arrives looking like this.
        mmsi = sample.key_sample.mmsi
        say(f"RECV (no valid data) mmsi={mmsi} {states_of(info)}")
        valid = False
    else:
        mmsi = sample.mmsi
        say(f"RECV observed_utc_ns={sample.observed_utc_ns} mmsi={mmsi} "
            f"station={sample.station_id} sog={sample.sog_kn:.1f} "
            f"pos=({sample.pos.lat:.4f},{sample.pos.lon:.4f}) {states_of(info)} "
            f"source_ts={info.source_timestamp}")
        valid = True

    now = INSTANCE_STATES.get(info.instance_state, str(info.instance_state))
    was = last_state.get(mmsi)
    if was != now:
        last_state[mmsi] = now
        if was is not None:
            # The line run.sh times. Deliberately unlike RECV at a glance.
            say(f"TRANSITION  mmsi={mmsi}  {was} -> {now}  "
                f"(carried by a sample with{'' if valid else 'out'} valid data)")
    return valid


def read_passes(reader, condition, passes):
    """Read the same cache repeatedly without taking anything from it."""
    for n in range(1, passes + 1):
        samples = reader.read(N=100, condition=condition)
        say(f"PASS {n}: read(N=100) returned {len(samples)} sample(s)")
        for sample in samples:
            note(sample)
        if not samples:
            say(f"PASS {n}: nothing. An empty read is not an error; it means the "
                f"cache holds nothing matching the mask.")


def parse_args(argv):
    seconds, mode, probe_delay, deadline_policy = None, "loop", 6.0, True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--seconds":
            i += 1; seconds = float(argv[i])
        elif arg == "--probe-delay":
            i += 1; probe_delay = float(argv[i])
        elif arg == "--read":
            mode = "read"
        elif arg == "--take":
            mode = "take"
        elif arg == "--no-deadline":
            deadline_policy = False
        else:
            print(__doc__, file=sys.stderr)
            sys.exit(2)
        i += 1
    return seconds, mode, probe_delay, deadline_policy


def main():
    run_seconds, mode, probe_delay, deadline_policy = parse_args(sys.argv[1:])

    # ------------------------------------------------------------------
    # The same VesselReport row of the ICD's QoS table, from the other side.
    # A reader states what it REQUIRES; the writer states what it OFFERS. These
    # have to be compatible or the two never match at all — and for Ownership
    # the kinds must be equal, which is why it appears here even though a
    # reader has no strength of its own.
    # ------------------------------------------------------------------
    reader_policies = [
        # Asking for RELIABLE here against a BEST_EFFORT writer would be a
        # requested_incompatible_qos and total silence. This lab wants the
        # freshest fix, not every fix, so it asks for no retransmission.
        Policy.Reliability.BestEffort,

        # Nothing historical is wanted, and under VOLATILE nothing historical
        # would be offered. What the watcher knows starts when it starts.
        Policy.Durability.Volatile,

        # One current position per vessel. This depth is PER INSTANCE, which is
        # the whole point of scenario 1: three vessels, three retained samples.
        Policy.History.KeepLast(1),

        # Follow one station per vessel rather than averaging two disagreeing
        # fixes. Must match the writer's kind exactly or nothing matches.
        Policy.Ownership.Exclusive,
    ]
    if deadline_policy:
        # If a vessel stops being reported for 3 s, say so rather than leaving
        # a stale symbol on a screen looking healthy. This is the ICD's value
        # and the default here.
        reader_policies.append(Policy.Deadline(duration(seconds=3)))
    # --no-deadline drops the line above and nothing else. It exists because
    # the crash run produced NO instance-state change at all, and one variable
    # had to be isolated to find out why. It is a diagnostic, not a fix: the
    # ICD's QoS table still says DEADLINE 3 s for VesselReport, and every other
    # run in this lab uses it. A reader that omits a deadline it needs is not
    # a better reader, it is a reader that cannot tell a stalled vessel from a
    # moving one.
    reader_qos = Qos(*reader_policies)

    say("=" * 78)
    say("lab01 watcher.py — prints every sample with all three of its state fields.")
    say(f"  domain id     : {DOMAIN_ID} (from src/vtslab/config.py)")
    say(f"  topic name    : VesselReport")
    say(f"  type name     : {VesselReport.__idl_typename__}  (key: mmsi)")
    say(f"  mode          : {mode}")
    say(f"  deadline      : {'3 s (the ICD value)' if deadline_policy else 'NONE — diagnostic run, see the comment in this file'}")
    if mode != "loop":
        say(f"  probe delay   : {probe_delay} s of NOT reading, then the {mode} probe")
    say(f"  duration      : {run_seconds if run_seconds else 'until interrupted'} s")
    say("=" * 78)

    # The entity tree, built literally. No helper, no factory: these four lines
    # are the whole reader side of DDS and are worth retyping each lab.
    participant = DomainParticipant(DOMAIN_ID)
    topic = Topic(participant, "VesselReport", VesselReport)
    subscriber = Subscriber(participant)
    # ReaderListener reports every reader-side status. requested_deadline_missed
    # is a second clock on the same event as a crash: the data stops arriving 3 s
    # after the writer dies, and the writer is not declared gone until much
    # later. Which of those the operator should be told about is a design
    # question this lab only makes visible.
    reader = DataReader(subscriber, topic, qos=reader_qos, listener=ReaderListener())

    # Stated rather than defaulted, because the mask decides what comes back.
    # NotRead: each sample once, the normal way to consume a live feed.
    live = ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any)

    # The probe's mask is deliberately different: SampleState.Any. With NotRead
    # the second read pass would return nothing — not because the cache emptied
    # but because reading marked the samples Read. That is the same lesson as
    # read-vs-take seen from the other side, and hiding it behind the default
    # mask would make read() look like take().
    anything = ReadCondition(reader, SampleState.Any | ViewState.Any | InstanceState.Any)

    say(f"created participant guid={participant.get_guid()}")
    say("resolved QoS — what was asked for above, plus everything Cyclone filled in:")
    say(f"  (durations are nanoseconds; {INFINITY_NS} is DDS_INFINITY)")
    dump_qos("participant", participant.get_qos())
    dump_qos("topic", topic.get_qos())
    dump_qos("reader", reader.get_qos())
    say("  read conditions:")
    say("      live  : SampleState.NotRead | ViewState.Any | InstanceState.Any")
    say("      probe : SampleState.Any     | ViewState.Any | InstanceState.Any")

    deadline = START + run_seconds if run_seconds else None

    if mode in ("read", "take"):
        say(f"NOT READING for {probe_delay} s. Samples are arriving and being kept "
            f"by the reader; this program is simply not asking for them.")
        # A plain sleep, not a read loop. The cache fills behind our back, and
        # KEEP_LAST(1) is deciding what survives while we do nothing.
        time.sleep(probe_delay)
        say(f"probe delay over. The cache now holds whatever KEEP_LAST(1) left.")

    if mode == "read":
        say("PROBE read: five consecutive read(N=100) over the same cache.")
        say("  read() does not remove anything. If the count is stable across")
        say("  all five passes, that is the definition of read() demonstrated.")
        read_passes(reader, anything, 5)

    elif mode == "take":
        say("PROBE take: one take(N=100), then one read(N=100) over what is left.")
        taken = reader.take(N=100, condition=anything)
        say(f"TAKE take(N=100) returned {len(taken)} sample(s)")
        for sample in taken:
            note(sample)
        after = reader.read(N=100, condition=anything)
        say(f"AFTER read(N=100) returned {len(after)} sample(s) "
            f"— the samples are gone; the INSTANCES are not.")
        for sample in after:
            note(sample)

    say("live take loop from here. Nothing below this line means nothing arrived.")

    received = invalid = 0
    while not stop and (deadline is None or time.monotonic() < deadline):
        # take_iter blocks until data arrives, then ends the iteration once the
        # timeout elapses with nothing new; the outer loop re-checks the clock
        # and the stop flag. No retries, no reconnection — there is nothing to
        # reconnect to.
        for sample in reader.take_iter(condition=live, timeout=duration(milliseconds=500)):
            if note(sample):
                received += 1
            else:
                invalid += 1
            if stop:
                break

    matched = reader.get_subscription_matched_status()
    say("-" * 78)
    say(f"EXIT samples received : {received}  (plus {invalid} state-change-only samples)")
    say(f"EXIT final instance states: "
        f"{', '.join(f'{m}={s}' for m, s in sorted(last_state.items())) or 'none seen'}")
    say(f"EXIT subscription_matched: total={matched.total_count} current={matched.current_count}")
    say(f"EXIT requested_incompatible_qos total={reader.get_requested_incompatible_qos_status().total_count}")
    say(f"EXIT requested_deadline_missed  total={reader.get_requested_deadline_missed_status().total_count}")
    say(f"EXIT sample_lost                total={reader.get_sample_lost_status().total_count}")
    say(f"EXIT sample_rejected            total={reader.get_sample_rejected_status().total_count}")
    say("-" * 78)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    main()
