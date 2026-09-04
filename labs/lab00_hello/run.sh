#!/usr/bin/env bash
#
# Lab 00 — three scenarios and an inspection, all with default QoS.
#
#   1. Subscriber first, publisher second. What does an early subscriber miss?
#   2. Publisher first, subscriber second. What does a late subscriber get?
#   3. Both running: look at the system from outside with the cyclonedds CLI.
#
# Optional first step:  run.sh --trace   dumps Cyclone's resolved configuration
# and its network interface choice before the scenarios run.
#
# Nothing here sets CYCLONEDDS_URI for the scenarios themselves. Lab 00 is
# about what happens when nothing is configured.

set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$LAB_DIR/../.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
CYCLONEDDS_CLI="$REPO/.venv/bin/cyclonedds"

TRACE=0
[[ "${1:-}" == "--trace" ]] && TRACE=1

# The domain id is read, never restated. If this line and src/vtslab/config.py
# ever disagree, the lab is lying about which domain it ran on.
DOMAIN_ID="$(PYTHONPATH="$REPO/src" "$PYTHON" -c 'from vtslab.config import DOMAIN_ID; print(DOMAIN_ID)')"

OUT="$LAB_DIR/out/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"

PIDS=()
cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

rule() { printf '\n%s\n' "================================================================================"; }

# First monotonic timestamp on the first line of $1 matching $2.
mono_of() {
    grep -m1 -- "$2" "$1" 2>/dev/null | sed -n 's/^\[mono *\([0-9.]*\).*/\1/p'
}

# The observed_utc_ns of every sample written before monotonic time $2, one
# per line. Each is unique, so it identifies the sample across both logs.
writes_before() {
    sed -n 's/^\[mono *\([0-9.]*\).*WRITE seq=.*observed_utc_ns=\([0-9]*\).*/\1 \2/p' "$1" \
        | awk -v cutoff="$2" '$1 + 0 < cutoff + 0 { print $2 }'
}

# Of the samples listed on stdin (observed_utc_ns per line), how many appear in
# subscriber log $1. A direct intersection of the two logs — no arithmetic on
# counts, which would go wrong the moment one process outlives the other.
delivered_of() {
    local list n
    list="$(cat)"
    [[ -z "$list" ]] && { echo 0; return; }
    # grep -c prints 0 and exits non-zero when nothing matches, so the exit
    # status is swallowed rather than turned into a second line of output.
    n="$(printf '%s\n' "$list" | grep -c -F -f /dev/stdin "$1" 2>/dev/null)" || true
    echo "${n:-0}"
}

count() { grep -c -- "$2" "$1" 2>/dev/null || true; }

echo "lab00_hello — domain $DOMAIN_ID, all QoS at Cyclone defaults"
echo "logs: $OUT"

# ---------------------------------------------------------------------------
if [[ $TRACE -eq 1 ]]; then
    rule
    echo "STEP 0 — what Cyclone resolved for itself (logging only, no QoS change)"
    echo
    echo "Watch for the 'using network interface' line: on a host with many"
    echo "bridges Cyclone says which one it took, and that it took it arbitrarily."
    echo
    CYCLONEDDS_URI="file://$REPO/configs/lab00_trace_config.xml" \
        "$PYTHON" "$LAB_DIR/pub.py" 1 > "$OUT/trace.log" 2>&1 || true
    grep -E "using network interface|Domain|config:" "$OUT/trace.log" | head -20 || true
    echo "(full dump: $OUT/trace.log)"
fi

# ---------------------------------------------------------------------------
rule
echo "RUN 1 — subscriber starts 5 s BEFORE the publisher, publisher runs 10 s."
echo
echo "The subscriber is listening before anything exists to listen to. Watch"
echo "whether it matches before the publisher's first write, because that is"
echo "what decides the fate of sample 1."
echo

"$PYTHON" "$LAB_DIR/sub.py" 17 > "$OUT/run1-sub.log" 2>&1 &
PIDS+=($!)
sleep 5
"$PYTHON" "$LAB_DIR/pub.py" 10 > "$OUT/run1-pub.log" 2>&1 &
PIDS+=($!)
wait

r1_written=$(count "$OUT/run1-pub.log" "WRITE seq=")
r1_received=$(count "$OUT/run1-sub.log" "RECV observed_utc_ns=")
r1_match=$(mono_of "$OUT/run1-sub.log" "STATUS subscription_matched")
r1_first_write=$(mono_of "$OUT/run1-pub.log" "WRITE seq=")
r1_first_ns=$(grep -m1 "RECV observed_utc_ns=" "$OUT/run1-sub.log" | sed -n 's/.*observed_utc_ns=\([0-9]*\).*/\1/p')
r1_first_seq=$(grep -m1 "observed_utc_ns=${r1_first_ns:-none}" "$OUT/run1-pub.log" | sed -n 's/.*WRITE seq=\([0-9]*\).*/\1/p')
r1_pre=$(writes_before "$OUT/run1-pub.log" "${r1_match:-0}")
r1_before=$(printf '%s\n' "$r1_pre" | grep -c . || true)
r1_pre_delivered=$(printf '%s\n' "$r1_pre" | delivered_of "$OUT/run1-sub.log")

echo "  samples written              : $r1_written"
echo "  samples received             : $r1_received"
echo "  subscription_matched at mono : ${r1_match:-never}"
echo "  first write at mono          : ${r1_first_write:-none}"
echo "  first sample received        : seq ${r1_first_seq:-none}"
echo "  written before the match     : $r1_before  (of which received: $r1_pre_delivered)"
echo
echo "  If the match timestamp precedes the first write, nothing could have"
echo "  been missed, and received should equal written. If it does not, the"
echo "  missing samples were written to a writer that had no reader yet."

# ---------------------------------------------------------------------------
rule
echo "RUN 2 — publisher starts 10 s BEFORE the subscriber."
echo
echo "About 10 samples are written before the subscriber exists. The writer"
echo "still holds the most recent one: its History is KEEP_LAST(1) and that"
echo "sample is in its cache. The question is whether the reader is entitled"
echo "to it."
echo

"$PYTHON" "$LAB_DIR/pub.py" 21 > "$OUT/run2-pub.log" 2>&1 &
PIDS+=($!)
sleep 10
"$PYTHON" "$LAB_DIR/sub.py" 10 > "$OUT/run2-sub.log" 2>&1 &
PIDS+=($!)
wait

r2_written=$(count "$OUT/run2-pub.log" "WRITE seq=")
r2_received=$(count "$OUT/run2-sub.log" "RECV observed_utc_ns=")
r2_match=$(mono_of "$OUT/run2-sub.log" "STATUS subscription_matched")
r2_pre=$(writes_before "$OUT/run2-pub.log" "${r2_match:-0}")
r2_before=$(printf '%s\n' "$r2_pre" | grep -c . || true)
r2_pre_delivered=$(printf '%s\n' "$r2_pre" | delivered_of "$OUT/run2-sub.log")
r2_first_ns=$(grep -m1 "RECV observed_utc_ns=" "$OUT/run2-sub.log" | sed -n 's/.*observed_utc_ns=\([0-9]*\).*/\1/p')
r2_first_seq=$(grep -m1 "observed_utc_ns=${r2_first_ns:-none}" "$OUT/run2-pub.log" | sed -n 's/.*WRITE seq=\([0-9]*\).*/\1/p')

echo "  samples written (total)         : $r2_written"
echo "  written BEFORE the match        : $r2_before"
echo "  samples received                : $r2_received"
echo "  first sample received           : seq ${r2_first_seq:-none}"
echo "  of those $r2_before pre-match samples, received: $r2_pre_delivered"
echo
echo "  Durability is VOLATILE, so a writer offers nothing to a reader it had"
echo "  not yet matched. Under TRANSIENT_LOCAL the answer would be 1 — the"
echo "  depth of that KEEP_LAST(1) history. That one-sample difference is the"
echo "  reason the Zone topic in the ICD is TRANSIENT_LOCAL."

# ---------------------------------------------------------------------------
rule
echo "RUN 3 — both processes running; inspect the system from outside."
echo
echo "cyclonedds ls and ps are a THIRD participant. They were given no address"
echo "and no port, and neither pub.py nor sub.py was told they exist."
echo

"$PYTHON" "$LAB_DIR/pub.py" 16 > "$OUT/run3-pub.log" 2>&1 &
PIDS+=($!)
"$PYTHON" "$LAB_DIR/sub.py" 16 > "$OUT/run3-sub.log" 2>&1 &
PIDS+=($!)
sleep 3

echo "--- cyclonedds ls -i $DOMAIN_ID (entities and their QoS) ---"
"$CYCLONEDDS_CLI" ls -i "$DOMAIN_ID" -r 4s --suppress-progress-bar --color none -q \
    | tee "$OUT/run3-ls.log"

echo "--- cyclonedds ps -i $DOMAIN_ID (applications) ---"
"$CYCLONEDDS_CLI" ps -i "$DOMAIN_ID" -r 4s --suppress-progress-bar --color none \
    | tee "$OUT/run3-ps.log"

wait

rule
echo "Logs in $OUT"
echo
echo "Worth reading in full, not just in summary:"
echo "  - the resolved QoS blocks at the top of any pub/sub log: writer"
echo "    RELIABLE against reader BEST_EFFORT is the default pairing, which is"
echo "    compatible but gives the reader no retransmission."
echo "  - the last RECV line of run 1: when the publisher exits, its writer"
echo "    disposes the instance, because WriterDataLifecycle autodispose=True"
echo "    is on in the dumped writer QoS. That is why the instance goes"
echo "    NotAliveDisposed rather than NotAliveNoWriters."
echo
echo "Now write labs/lab00_hello/findings.md."
