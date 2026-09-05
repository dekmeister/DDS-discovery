"""How a lab reports what it sees. Nothing in here touches the DDS API.

From Lab 01 onwards, anything a previous lab already established and that only
*observes* lives here instead of being copied into each lab: the monotonic
print helper, the QoS dump, the lookup tables that turn DDS integers into
names, and the status listeners.

The boundary is absolute and worth restating at the top of the file that
enforces it: **if it constructs or configures a DDS entity, it does not belong
here.** No participant, topic, publisher, subscriber, reader, writer, `Qos` or
`ReadCondition` is built in this module, and none ever should be. Those appear
literally in the lab that uses them, every time, because the entity tree and
the QoS are what each lab is about. What is left in here is printing.

Lab 00 predates this module and keeps its own copies of everything below. It is
not updated to import from here — a committed lab stays runnable as committed.
"""

import time

from cyclonedds.core import InstanceState, Listener, SampleState, ViewState


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

# The three per-sample state fields.
#   sample_state   have I already seen THIS SAMPLE?      (read/take changes it)
#   view_state     have I already seen THIS INSTANCE?    (first sight only)
#   instance_state is the instance alive, and if not, how did it end?
SAMPLE_STATES = {SampleState.Read: "Read", SampleState.NotRead: "NotRead"}
VIEW_STATES = {ViewState.New: "New", ViewState.Old: "Old"}
INSTANCE_STATES = {
    InstanceState.Alive: "Alive",
    InstanceState.NotAliveDisposed: "NotAliveDisposed",
    InstanceState.NotAliveNoWriters: "NotAliveNoWriters",
}

INFINITY_NS = 9223372036854775807  # DDS_INFINITY, as it appears in durations

# Set when this module is first imported, which is process startup for every
# lab program. t+ is relative to that.
START = time.monotonic()


def say(message):
    # Absolute monotonic first: CLOCK_MONOTONIC is system-wide on Linux, so
    # this number is directly comparable across every process in a lab and
    # against a stamp taken by run.sh. Every latency any lab reports is a
    # subtraction between two of these numbers, which is why the raw value is
    # printed and not just the elapsed time.
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
    """Print a fully resolved QoS, and name the policies it did not report."""
    say(f"  {label}:")
    present = set()
    for policy in sorted(qos, key=lambda p: (policy_family(p), repr(p))):
        present.add(policy_family(policy))
        say(f"      {policy!r}")
    absent = [f for f in POLICY_FAMILIES if f not in present]
    say(f"      (not reported by dds_get_qos: {', '.join(absent) if absent else 'none'})")


def states_of(info):
    """The three state fields of one sample, formatted for a log line."""
    return (f"sample_state={SAMPLE_STATES.get(info.sample_state, info.sample_state)} "
            f"view_state={VIEW_STATES.get(info.view_state, info.view_state)} "
            f"instance_state={INSTANCE_STATES.get(info.instance_state, info.instance_state)}")


class WriterListener(Listener):
    """Every writer-side status, printed the moment it changes.

    All of them, not just the interesting one: a lab's silence is only a result
    if the hooks that would have broken it are demonstrably installed.

    A listener callback consumes the status, which resets the *_change fields.
    total_count is cumulative and survives, which is why an exit summary read
    from get_*_status() can still be trusted.

    A lab that needs to react to a status, rather than report it, writes its
    own listener in its own file — that is experiment, not scaffolding.
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


class ReaderListener(Listener):
    """Every reader-side status, printed the moment it changes.

    Same reasoning as WriterListener: the ones that stay quiet are carrying as
    much information as the ones that fire.
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
