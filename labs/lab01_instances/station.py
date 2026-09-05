#!/usr/bin/env python3
"""Lab 01 station: writes several vessels on one topic and can end an instance.

    DomainParticipant -> Topic("VesselReport") -> Publisher -> DataWriter

One writer, one topic, N vessels. Because VesselReport is keyed on mmsi, those
N vessels are N *instances* of the same topic, written by the same writer. This
process exists to end them, on command, in the two ways a writer can:

    leave <mmsi>    the vessel sailed out of coverage   -> unregister_instance()
    bogus <mmsi>    the report was never real           -> dispose()

Commands are read from this process's own stdin, so the writer that ends the
instance is the same writer that created it. The third ending has no command,
because it is not something the writer does: kill -9 this process from outside
and the writer simply stops existing.

Usage:
    station.py <station_id> <mmsi> [<mmsi> ...]
               [--seconds N] [--rate HZ] [--strength N] [--no-autodispose]
"""

import os
import select
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

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.pub import DataWriter, Publisher
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from vtslab.config import DOMAIN_ID
# Printing, name tables and the status listener: established in Lab 00 and
# unchanged since, so they live in one place rather than being re-typed here.
# Nothing in that module touches the DDS API — the entity tree and the QoS
# below are the parts of this file worth reading, and they stay literal.
from vtslab.report import INFINITY_NS, START, WriterListener, dump_qos, say

# Lab 00 declared this type inline. From here on it is generated from
# idl/vts.idl by `make generate` — same topic, same type name, same fields, so
# a Lab 00 subscriber and a Lab 01 station would still match each other.
from vts import NavStatus, Position, VesselReport


stop = False


def request_stop(signum, _frame):
    global stop
    stop = True
    say(f"signal {signal.Signals(signum).name} received, stopping after this tick")


def parse_args(argv):
    station_id, mmsis = None, []
    seconds, rate, strength, autodispose = None, 1.0, 10, True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--seconds":
            i += 1; seconds = float(argv[i])
        elif arg == "--rate":
            i += 1; rate = float(argv[i])
        elif arg == "--strength":
            i += 1; strength = int(argv[i])
        elif arg == "--no-autodispose":
            autodispose = False
        elif station_id is None:
            station_id = int(arg)
        else:
            mmsis.append(int(arg))
        i += 1
    if station_id is None or not mmsis:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    return station_id, mmsis, seconds, rate, strength, autodispose


def main():
    global stop
    station_id, mmsis, run_seconds, rate, strength, autodispose = parse_args(sys.argv[1:])

    # ------------------------------------------------------------------
    # The VesselReport row of the ICD's QoS table, written out literally.
    # Every policy says why in terms of the vessels, not the policy name.
    # ------------------------------------------------------------------
    writer_policies = [
        # A position report that arrives late is worse than one that never
        # arrives: the next one is already truer. Nothing is retransmitted.
        Policy.Reliability.BestEffort,

        # A station has nothing to tell a watcher about where a vessel was
        # before the watcher existed. Only the live picture is worth having.
        Policy.Durability.Volatile,

        # Per instance, exactly one position is current. An older position for
        # the same vessel is not history, it is a wrong answer.
        Policy.History.KeepLast(1),

        # A vessel under way is expected to be re-reported every 3 s. Silence
        # longer than that is itself information, and both sides are told to
        # raise it rather than wait quietly.
        Policy.Deadline(duration(seconds=3)),

        # Two stations with overlapping coverage report the same vessel. The
        # watcher should follow one of them at a time rather than average two
        # disagreeing fixes, so the instance has a single owner...
        Policy.Ownership.Exclusive,

        # ...and this is the number that decides which. Not used in Lab 01
        # (one station), stated because the topic's contract carries it and a
        # reader will refuse to match if the kinds disagree.
        Policy.OwnershipStrength(strength),
    ]
    if not autodispose:
        # Default is autodispose=True, which converts "she left my coverage"
        # into "she does not exist" on the way out. Off, the two stay distinct.
        # This is the only difference the --no-autodispose run introduces.
        writer_policies.append(Policy.WriterDataLifecycle(autodispose=False))
    writer_qos = Qos(*writer_policies)

    say("=" * 78)
    say(f"lab01 station.py — station {station_id} reporting {len(mmsis)} vessel(s) "
        f"as {len(mmsis)} instance(s) of one topic.")
    say(f"  domain id     : {DOMAIN_ID} (from src/vtslab/config.py)")
    say(f"  topic name    : VesselReport")
    say(f"  type name     : {VesselReport.__idl_typename__}  (key: mmsi — station_id is NOT a key)")
    say(f"  vessels       : {', '.join(str(m) for m in mmsis)}")
    say(f"  rate          : {rate} Hz per vessel")
    say(f"  strength      : {strength}")
    say(f"  autodispose   : {autodispose}"
        f"{'' if autodispose else '  (WriterDataLifecycle(autodispose=False) requested)'}")
    say(f"  duration      : {run_seconds if run_seconds else 'until interrupted'} s")
    say("  commands on stdin:")
    say("     leave <mmsi>   -> writer.unregister_instance()  'she sailed out of coverage'")
    say("     bogus <mmsi>   -> writer.dispose()              'she was never there'")
    say("     (the third ending has no command: kill -9 this pid)")
    say("=" * 78)

    # The entity tree, built literally. No helper, no factory: these four lines
    # are the whole writer side of DDS and are worth retyping each lab.
    participant = DomainParticipant(DOMAIN_ID)
    topic = Topic(participant, "VesselReport", VesselReport)
    publisher = Publisher(participant)
    # WriterListener reports every writer-side status. The one to watch in this
    # lab is offered_deadline_missed: the writer promised a report every 3 s per
    # instance, and ending an instance is one of the ways it stops keeping that
    # promise. Whether that counts as a promise broken or a promise withdrawn is
    # exactly the question.
    writer = DataWriter(publisher, topic, qos=writer_qos, listener=WriterListener())

    say(f"created participant guid={participant.get_guid()}  pid={os.getpid()}")
    say("resolved QoS — what was asked for above, plus everything Cyclone filled in:")
    say(f"  (durations are nanoseconds; {INFINITY_NS} is DDS_INFINITY)")
    dump_qos("participant", participant.get_qos())
    dump_qos("topic", topic.get_qos())
    dump_qos("writer", writer.get_qos())
    say("  ^ WriterDataLifecycle above is the policy the fourth run varies. Read it.")

    say("writing now. Each WRITE line is one dds_write() that has returned.")

    # Inline synthetic motion. sim.py is an actor-library file, not a lab file,
    # and three lines of arithmetic keep this program readable on its own.
    active = list(mmsis)
    lat = {m: -33.8523 + 0.01 * i for i, m in enumerate(mmsis)}
    lon = {m: 151.2108 + 0.01 * i for i, m in enumerate(mmsis)}

    def report(mmsi, tick):
        return VesselReport(
            mmsi=mmsi,
            station_id=station_id,
            pos=Position(lat=lat[mmsi] + tick * 0.0007, lon=lon[mmsi] + tick * 0.0004),
            sog_kn=12.0,
            cog_deg=87.5,
            nav_status=NavStatus.UNDER_WAY,
            range_nm=4.2,
            observed_utc_ns=time.time_ns(),
        )

    def end_instance(verb, mmsi, tick):
        # The sample carries a full payload but only its key is used: both
        # calls serialize the key alone. It is the instance being ended, not
        # this sample.
        sample = report(mmsi, tick)
        say(f"ACTION {verb} mmsi={mmsi} — calling now")
        if verb == "leave":
            writer.unregister_instance(sample)
        else:
            writer.dispose(sample)
        say(f"ACTION {verb} mmsi={mmsi} — returned")
        if mmsi in active:
            active.remove(mmsi)
        # Nothing stops this writer writing mmsi again; that would resurrect
        # the instance and take it back to Alive. Dropping it from the active
        # list is a decision of this program, not a rule of DDS.
        say(f"ACTION mmsi={mmsi} dropped from the active list — no further writes "
            f"(a write would resurrect the instance)")

    tick = 0
    period = 1.0 / rate
    deadline = START + run_seconds if run_seconds else None
    while not stop and (deadline is None or time.monotonic() < deadline):
        tick += 1
        for mmsi in list(active):
            sample = report(mmsi, tick)
            writer.write(sample)
            say(f"WRITE tick={tick} mmsi={sample.mmsi} station={sample.station_id} "
                f"observed_utc_ns={sample.observed_utc_ns} "
                f"pos=({sample.pos.lat:.4f},{sample.pos.lon:.4f})")

        # Spend the rest of the period watching stdin. select() with the
        # remaining sleep as its timeout is the whole of the command channel:
        # no thread, no async, no queue. A command is acted on the moment it
        # arrives, which is what makes the ACTION timestamp meaningful.
        next_tick = START + tick * period
        while not stop:
            remaining = next_tick - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([sys.stdin], [], [], remaining)
            if not ready:
                break
            line = sys.stdin.readline()
            if not line:
                # stdin closed. Nothing more can be commanded, but the vessels
                # still need reporting, so keep writing rather than exiting.
                say("stdin closed — no more commands will arrive")
                time.sleep(max(0.0, next_tick - time.monotonic()))
                break
            parts = line.split()
            if not parts:
                continue
            verb = parts[0].lower()
            if verb in ("leave", "bogus") and len(parts) == 2:
                end_instance(verb, int(parts[1]), tick)
            elif verb == "quit":
                say("ACTION quit — leaving the loop")
                stop = True
            else:
                say(f"ACTION unrecognised command: {line.strip()!r}")

    matched = writer.get_publication_matched_status()
    say("-" * 78)
    say(f"EXIT ticks written : {tick}   still-active vessels: {active}")
    say(f"EXIT publication_matched: total={matched.total_count} current={matched.current_count}")
    say(f"EXIT offered_incompatible_qos total={writer.get_offered_incompatible_qos_status().total_count}")
    say(f"EXIT offered_deadline_missed  total={writer.get_offered_deadline_missed_status().total_count}")
    say(f"EXIT liveliness_lost          total={writer.get_liveliness_lost_status().total_count}")
    say("EXIT the process is about to end normally. Any instance still active")
    say("EXIT is about to be ended by that fact alone — which is a fourth path")
    say("EXIT to the same question, and the reason the kill run uses kill -9.")
    say("-" * 78)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    main()
