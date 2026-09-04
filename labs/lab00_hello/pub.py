#!/usr/bin/env python3
"""Lab 00 publisher: one vessel, one report per second, every QoS left default.

This process creates the smallest useful writer side of a DDS system:

    DomainParticipant -> Topic("VesselReport") -> Publisher -> DataWriter

and writes one VesselReport per second for a single hardcoded vessel. It asks
for no QoS at all, so what it gets is whatever Cyclone considers default. That
resolved default is printed on startup, because "default" is the baseline every
later lab's QoS table is a deviation from, and it is not knowable by reading
this file.

Usage:  pub.py [seconds]     (no argument = run until interrupted)
"""

import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Labs run as loose scripts, not as an installed package, so that `python
# pub.py` works from anywhere. src/ holds the one and only domain ID.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import cyclonedds.idl as idl
import cyclonedds.idl.annotations as annotate
import cyclonedds.idl.types as types
from cyclonedds.core import Listener
from cyclonedds.domain import DomainParticipant
from cyclonedds.pub import DataWriter, Publisher
from cyclonedds.topic import Topic

from vtslab.config import DOMAIN_ID


# ---------------------------------------------------------------------------
# The type, declared inline.
#
# Lab 00 has no build step on purpose, so this is hand-written rather than
# generated from idl/vts.idl. Two things make that safe for Lab 01, which will
# use the generated type against this same topic:
#
#   - typename is stated explicitly as "vts.VesselReport". Left implicit, the
#     binding would name the type after the class alone, and the generated type
#     will be vts::VesselReport. The type name is half of what DDS matches on.
#   - the fields are exactly idl/vts.idl's VesselReport, in order. Same topic
#     name plus same type name plus a different field list is an XTypes
#     assignability failure, which is a miserable thing to debug later.
#
# Keyed on mmsi alone; station_id is NOT a key. See the comment in the IDL.
# ---------------------------------------------------------------------------

class NavStatus(idl.IdlEnum, typename="vts.NavStatus"):
    UNDER_WAY = 0
    AT_ANCHOR = 1
    MOORED = 2
    AGROUND = 3
    NOT_UNDER_COMMAND = 4
    UNKNOWN_STATUS = 5


@dataclass
@annotate.final
class Position(idl.IdlStruct, typename="vts.Position"):
    lat: types.float64
    lon: types.float64


@dataclass
@annotate.final
class VesselReport(idl.IdlStruct, typename="vts.VesselReport"):
    mmsi: types.uint32
    annotate.key("mmsi")
    station_id: types.uint16
    pos: Position
    sog_kn: types.float32
    cog_deg: types.float32
    nav_status: NavStatus
    range_nm: types.float32
    observed_utc_ns: types.int64


# DDS policy ids, as reported in the *_incompatible_qos statuses. The status
# carries a bare integer; without this table "last_policy_id=11" is not an
# answer to anything. From dds_qos_policy_id in dds_public_qosdefs.h.
POLICY_ID_NAMES = {
    0: "INVALID", 1: "USERDATA", 2: "DURABILITY", 3: "PRESENTATION",
    4: "DEADLINE", 5: "LATENCYBUDGET", 6: "OWNERSHIP", 7: "OWNERSHIPSTRENGTH",
    8: "LIVELINESS", 9: "TIMEBASEDFILTER", 10: "PARTITION", 11: "RELIABILITY",
    12: "DESTINATIONORDER", 13: "HISTORY", 14: "RESOURCELIMITS",
    15: "ENTITYFACTORY", 16: "WRITERDATALIFECYCLE", 17: "READERDATALIFECYCLE",
    18: "TOPICDATA", 19: "GROUPDATA", 20: "TRANSPORTPRIORITY", 21: "LIFESPAN",
    22: "DURABILITYSERVICE", 23: "PROPERTY",
    24: "TYPE_CONSISTENCY_ENFORCEMENT", 25: "DATA_REPRESENTATION",
}

# Every policy family the Python binding can express. Used to report which
# policies dds_get_qos() declined to tell us about, so "this is the default"
# stays distinguishable from "nobody said".
POLICY_FAMILIES = (
    "Reliability", "Durability", "History", "ResourceLimits",
    "PresentationAccessScope", "Lifespan", "Deadline", "LatencyBudget",
    "Ownership", "OwnershipStrength", "Liveliness", "TimeBasedFilter",
    "Partition", "TransportPriority", "DestinationOrder",
    "WriterDataLifecycle", "ReaderDataLifecycle", "DurabilityService",
    "IgnoreLocal", "Userdata", "Topicdata", "Groupdata", "Property",
    "BinaryProperty", "TypeConsistency", "DataRepresentation", "EntityName",
)

INFINITY_NS = 9223372036854775807  # DDS_INFINITY, as it appears in durations

START = time.monotonic()


def say(message):
    # Absolute monotonic first: CLOCK_MONOTONIC is system-wide on Linux, so
    # this number is directly comparable against sub.py's log. That is what
    # makes "written before the match" provable rather than plausible.
    print(f"[mono {time.monotonic():12.3f} | t+{time.monotonic() - START:7.3f}] {message}", flush=True)


def policy_family(policy):
    # Policies repr themselves in three shapes, and the family name sits in a
    # different place in each:
    #   "Policy.Durability.Volatile"  (singleton)  -> Durability
    #   "Policy.Deadline(deadline=..)" (dataclass) -> Deadline
    #   "Property(key=..)"          (own __repr__) -> Property
    parts = repr(policy).split("(")[0].split(".")
    return parts[1] if parts[0] == "Policy" else parts[0]


def dump_qos(label, qos):
    say(f"  {label}:")
    present = set()
    for policy in sorted(qos, key=lambda p: (policy_family(p), repr(p))):
        present.add(policy_family(policy))
        say(f"      {policy!r}")
    absent = [f for f in POLICY_FAMILIES if f not in present]
    say(f"      (not reported by dds_get_qos: {', '.join(absent) if absent else 'none'})")


class WriterListener(Listener):
    """Every writer-side status, printed the moment it changes.

    Under default QoS all of these except publication_matched should stay
    silent for the whole run. The silence is a result, and it only counts as
    one because the hooks are demonstrably installed.

    Note: a listener callback consumes the status, which resets the *_change
    fields. total_count is cumulative and survives, which is why the exit
    summary below can still be trusted.
    """

    def on_publication_matched(self, writer, status):
        say(f"STATUS publication_matched: total={status.total_count} "
            f"total_change={status.total_count_change} "
            f"current={status.current_count} current_change={status.current_count_change}")

    def on_offered_incompatible_qos(self, writer, status):
        name = POLICY_ID_NAMES.get(status.last_policy_id, "?")
        say(f"STATUS offered_incompatible_qos: total={status.total_count} "
            f"last_policy_id={status.last_policy_id} ({name})")

    def on_offered_deadline_missed(self, writer, status):
        say(f"STATUS offered_deadline_missed: total={status.total_count} "
            f"change={status.total_count_change}")

    def on_liveliness_lost(self, writer, status):
        say(f"STATUS liveliness_lost: total={status.total_count} "
            f"change={status.total_count_change}")


stop = False


def request_stop(signum, _frame):
    global stop
    stop = True
    say(f"signal {signal.Signals(signum).name} received, stopping after this second")


def main():
    run_seconds = float(sys.argv[1]) if len(sys.argv) > 1 else None

    say("=" * 78)
    say("lab00 pub.py — writes one VesselReport per second for one vessel.")
    say(f"  domain id     : {DOMAIN_ID} (from src/vtslab/config.py)")
    say(f"  topic name    : VesselReport")
    say(f"  type name     : {VesselReport.__idl_typename__}  (key: mmsi)")
    say(f"  qos requested : None — every policy left at the Cyclone default")
    say(f"  duration      : {run_seconds if run_seconds else 'until interrupted'} s")
    say("=" * 78)

    # The entity tree, built literally. No helper, no factory: the four lines
    # below are the whole writer side of DDS and are worth retyping each lab.
    participant = DomainParticipant(DOMAIN_ID)
    topic = Topic(participant, "VesselReport", VesselReport)
    publisher = Publisher(participant)
    writer = DataWriter(publisher, topic, qos=None, listener=WriterListener())

    say(f"created participant guid={participant.get_guid()}")
    say("resolved QoS — what Cyclone filled in when asked for nothing:")
    say(f"  (durations are nanoseconds; {INFINITY_NS} is DDS_INFINITY)")
    dump_qos("participant", participant.get_qos())
    dump_qos("topic", topic.get_qos())
    dump_qos("writer", writer.get_qos())

    say("writing now. Each line below is one dds_write() that has returned.")

    seq = 0
    deadline = START + run_seconds if run_seconds else None
    while not stop and (deadline is None or time.monotonic() < deadline):
        seq += 1
        sample = VesselReport(
            mmsi=316001234,
            station_id=1,
            pos=Position(lat=-33.8523 + seq * 0.0007, lon=151.2108 + seq * 0.0004),
            sog_kn=12.0,
            cog_deg=87.5,
            nav_status=NavStatus.UNDER_WAY,
            range_nm=4.2,
            observed_utc_ns=time.time_ns(),
        )
        writer.write(sample)
        say(f"WRITE seq={seq} mmsi={sample.mmsi} station={sample.station_id} "
            f"observed_utc_ns={sample.observed_utc_ns} "
            f"pos=({sample.pos.lat:.4f},{sample.pos.lon:.4f})")
        time.sleep(1.0)

    matched = writer.get_publication_matched_status()
    say("-" * 78)
    say(f"EXIT samples written : {seq}")
    say(f"EXIT publication_matched: total={matched.total_count} current={matched.current_count}")
    say(f"EXIT offered_incompatible_qos total={writer.get_offered_incompatible_qos_status().total_count}")
    say(f"EXIT offered_deadline_missed  total={writer.get_offered_deadline_missed_status().total_count}")
    say(f"EXIT liveliness_lost          total={writer.get_liveliness_lost_status().total_count}")
    say("-" * 78)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    main()
