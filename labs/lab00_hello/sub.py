#!/usr/bin/env python3
"""Lab 00 subscriber: same topic, same defaults, prints everything it receives.

    DomainParticipant -> Topic("VesselReport") -> Subscriber -> DataReader

It asks for no QoS, prints the QoS it was given anyway, prints every sample
with its sample/view/instance state, and prints subscription_matched every time
it changes. The matched line carries a monotonic timestamp so its position
relative to pub.py's writes is a fact in the log rather than an inference.

Usage:  sub.py [seconds]     (no argument = run until interrupted)
"""

import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Labs run as loose scripts, not as an installed package, so that `python
# sub.py` works from anywhere. src/ holds the one and only domain ID.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import cyclonedds.idl as idl
import cyclonedds.idl.annotations as annotate
import cyclonedds.idl.types as types
from cyclonedds.core import InstanceState, Listener, ReadCondition, SampleState, ViewState
from cyclonedds.domain import DomainParticipant
from cyclonedds.internal import InvalidSample
from cyclonedds.sub import DataReader, Subscriber
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from vtslab.config import DOMAIN_ID


# ---------------------------------------------------------------------------
# The type, declared inline — deliberately identical to the copy in pub.py.
#
# This repetition is the lab. DDS matches a reader to a writer on topic name
# and type, and nothing here is shared between the two processes at runtime:
# they agree because both were written to the same ICD, not because they
# import the same module. Change one field's type here and the two stop
# matching, which is Lab 01's territory.
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

# The three per-sample state fields, printed for every sample. Lab 00 does not
# exercise the lifecycle, but the writer going away at the end of a run does
# produce a NotAliveNoWriters instance with no valid data, and that is worth
# seeing once here before Lab 02 makes a subject of it.
SAMPLE_STATES = {SampleState.Read: "Read", SampleState.NotRead: "NotRead"}
VIEW_STATES = {ViewState.New: "New", ViewState.Old: "Old"}
INSTANCE_STATES = {
    InstanceState.Alive: "Alive",
    InstanceState.NotAliveDisposed: "NotAliveDisposed",
    InstanceState.NotAliveNoWriters: "NotAliveNoWriters",
}

INFINITY_NS = 9223372036854775807  # DDS_INFINITY, as it appears in durations

START = time.monotonic()


def say(message):
    # Absolute monotonic first: CLOCK_MONOTONIC is system-wide on Linux, so
    # this number is directly comparable against pub.py's log. That is what
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


class ReaderListener(Listener):
    """Every reader-side status, printed the moment it changes.

    Under default QoS everything except subscription_matched and
    liveliness_changed should stay silent for the whole run. The silence is a
    result, and it only counts as one because the hooks are demonstrably
    installed.

    Note: a listener callback consumes the status, which resets the *_change
    fields. total_count is cumulative and survives, which is why the exit
    summary below can still be trusted.
    """

    def on_subscription_matched(self, reader, status):
        say(f"STATUS subscription_matched: total={status.total_count} "
            f"total_change={status.total_count_change} "
            f"current={status.current_count} current_change={status.current_count_change}")

    def on_requested_incompatible_qos(self, reader, status):
        name = POLICY_ID_NAMES.get(status.last_policy_id, "?")
        say(f"STATUS requested_incompatible_qos: total={status.total_count} "
            f"last_policy_id={status.last_policy_id} ({name})")

    def on_requested_deadline_missed(self, reader, status):
        say(f"STATUS requested_deadline_missed: total={status.total_count} "
            f"change={status.total_count_change}")

    def on_liveliness_changed(self, reader, status):
        say(f"STATUS liveliness_changed: alive={status.alive_count} "
            f"not_alive={status.not_alive_count} "
            f"alive_change={status.alive_count_change} "
            f"not_alive_change={status.not_alive_count_change}")

    def on_sample_lost(self, reader, status):
        say(f"STATUS sample_lost: total={status.total_count} "
            f"change={status.total_count_change}")

    def on_sample_rejected(self, reader, status):
        say(f"STATUS sample_rejected: total={status.total_count} "
            f"change={status.total_count_change} reason={status.last_reason}")


stop = False


def request_stop(signum, _frame):
    global stop
    stop = True
    say(f"signal {signal.Signals(signum).name} received, stopping")


def main():
    run_seconds = float(sys.argv[1]) if len(sys.argv) > 1 else None

    say("=" * 78)
    say("lab00 sub.py — reads VesselReport and prints every sample it is given.")
    say(f"  domain id     : {DOMAIN_ID} (from src/vtslab/config.py)")
    say(f"  topic name    : VesselReport")
    say(f"  type name     : {VesselReport.__idl_typename__}  (key: mmsi)")
    say(f"  qos requested : None — every policy left at the Cyclone default")
    say(f"  duration      : {run_seconds if run_seconds else 'until interrupted'} s")
    say("=" * 78)

    # The entity tree, built literally. No helper, no factory: the four lines
    # below are the whole reader side of DDS and are worth retyping each lab.
    participant = DomainParticipant(DOMAIN_ID)
    topic = Topic(participant, "VesselReport", VesselReport)
    subscriber = Subscriber(participant)
    reader = DataReader(subscriber, topic, qos=None, listener=ReaderListener())

    # Stated rather than defaulted, so the mask that decides what take() hands
    # back is visible in this file: unread samples only, any view state, any
    # instance state — including instances whose writer has gone away.
    condition = ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any)

    say(f"created participant guid={participant.get_guid()}")
    say("resolved QoS — what Cyclone filled in when asked for nothing:")
    say(f"  (durations are nanoseconds; {INFINITY_NS} is DDS_INFINITY)")
    dump_qos("participant", participant.get_qos())
    dump_qos("topic", topic.get_qos())
    dump_qos("reader", reader.get_qos())

    say("reading now. Nothing below this line means nothing was delivered.")

    received = 0
    invalid = 0
    deadline = START + run_seconds if run_seconds else None
    while not stop and (deadline is None or time.monotonic() < deadline):
        # take_iter blocks until data arrives, then ends the iteration once the
        # timeout elapses with nothing new; the outer loop re-checks the clock
        # and the stop flag. No retries, no reconnection — there is nothing to
        # reconnect to.
        for sample in reader.take_iter(condition=condition, timeout=duration(milliseconds=500)):
            info = sample.sample_info
            states = (f"sample_state={SAMPLE_STATES.get(info.sample_state, info.sample_state)} "
                      f"view_state={VIEW_STATES.get(info.view_state, info.view_state)} "
                      f"instance_state={INSTANCE_STATES.get(info.instance_state, info.instance_state)}")
            if isinstance(sample, InvalidSample):
                # No payload: the instance changed state (here, the writer left)
                # and only the key came back.
                invalid += 1
                say(f"RECV (no valid data) mmsi={sample.key_sample.mmsi} {states}")
            else:
                received += 1
                say(f"RECV observed_utc_ns={sample.observed_utc_ns} mmsi={sample.mmsi} "
                    f"station={sample.station_id} sog={sample.sog_kn:.1f} "
                    f"pos=({sample.pos.lat:.4f},{sample.pos.lon:.4f}) {states} "
                    f"source_ts={info.source_timestamp}")
            if stop:
                break

    matched = reader.get_subscription_matched_status()
    say("-" * 78)
    say(f"EXIT samples received : {received}  (plus {invalid} state-change-only samples)")
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
