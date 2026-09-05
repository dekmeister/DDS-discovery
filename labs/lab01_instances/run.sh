#!/usr/bin/env bash
#
# Lab 01 — the instance model, in four scenarios.
#
#   1. Three vessels, three instances, one topic. What does KEEP_LAST(1) keep?
#   2. read() vs take() over the same cache.
#   3. The three endings — leave, bogus, kill -9 — each timed.
#   4. leave again with WriterDataLifecycle(autodispose=False).
#
# Nothing here sets CYCLONEDDS_URI. Discovery timing matters in scenario 3, and
# it should be Cyclone's own, not something this lab arranged.
#
# Requires generated types: run `make generate` first.

set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$LAB_DIR/../.." && pwd)"
PYTHON="$REPO/.venv/bin/python"

# The domain id is read, never restated. If this line and src/vtslab/config.py
# ever disagree, the lab is lying about which domain it ran on.
DOMAIN_ID="$(PYTHONPATH="$REPO/src" "$PYTHON" -c 'from vtslab.config import DOMAIN_ID; print(DOMAIN_ID)')"

if [[ ! -f "$REPO/src/vtslab/generated/vts/__init__.py" ]]; then
    echo "no generated types — run 'make generate' from $REPO first" >&2
    exit 1
fi

OUT="$LAB_DIR/out/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"

VESSELS=(316001234 316005678 316009999)
SUBJECT=316001234          # the vessel whose ending is measured

PIDS=()
cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

rule() { printf '\n%s\n' "================================================================================"; }

# CLOCK_MONOTONIC is system-wide on Linux, so a stamp taken here is directly
# comparable with the [mono ...] prefix in either program's log. This is the
# clock every latency in the summary is measured against.
mono_now() { "$PYTHON" -c 'import time; print(time.monotonic())'; }

# The first line of file $2 matching regex $3 whose [mono ...] stamp is at or
# after $1. Every event this lab times has to be filtered this way: a run's log
# can contain matches from before the ending was issued — the previous run's
# station is still shutting down when the next watcher starts, and it produces
# a perfectly real subscription_matched current_change=-1 of its own.
#
# Prints nothing if there is no such line, and prints it without failing:
# absence is a legitimate result here (a run with no requested_deadline_missed
# is a finding, not an error) and must not trip `set -e`.
first_after() {
    awk -v t="$1" -v pat="$3" '
        $0 ~ pat {
            m = $0
            sub(/^\[mono */, "", m)
            sub(/ .*/, "", m)
            if (m + 0 >= t + 0) { print; exit }
        }' "$2" 2>/dev/null || true
}

# The [mono ...] stamp of a line produced by first_after, or empty.
mono_of_line() {
    printf '%s\n' "${1:-}" | sed -n 's/^\[mono *\([0-9.]*\).*/\1/p'
}

# $2 - $1, to milliseconds, or "-" if either is missing.
delta() {
    [[ -z "${1:-}" || -z "${2:-}" ]] && { echo "-"; return; }
    awk -v a="$1" -v b="$2" 'BEGIN { printf "%.3f", b - a }'
}

count() { grep -c -- "$2" "$1" 2>/dev/null || true; }

SUMMARY=()

echo "lab01_instances — domain $DOMAIN_ID, VesselReport QoS from the ICD table"
echo "logs: $OUT"

# ---------------------------------------------------------------------------
rule
echo "RUN 1 — three vessels, three instances, one topic."
echo
echo "One station writes three MMSIs at 1 Hz. The watcher does not read for 6 s"
echo "— about 18 samples are written in that window — and then reads five times."
echo "The question is what KEEP_LAST(1) left behind: 1 sample, 3, or 18?"
echo

"$PYTHON" "$LAB_DIR/watcher.py" --read --probe-delay 6 --seconds 14 > "$OUT/run1-watcher.log" 2>&1 &
PIDS+=($!)
sleep 1
"$PYTHON" "$LAB_DIR/station.py" 1 "${VESSELS[@]}" --seconds 12 < /dev/null > "$OUT/run1-station.log" 2>&1 &
PIDS+=($!)
wait

r1_written=$(count "$OUT/run1-station.log" "WRITE tick=")
echo "  samples written during the run : $r1_written"
grep -E "^\[mono.*PASS [0-9]+: read" "$OUT/run1-watcher.log" | sed 's/^\[[^]]*\] /  /'
echo
echo "  KEEP_LAST(1) is a depth of one PER INSTANCE, not one per reader. The"
echo "  five passes read the same cache without emptying it: the sample_state"
echo "  goes NotRead -> Read after pass 1, and view_state New -> Old, but the"
echo "  count does not move. Full detail in run1-watcher.log."

# ---------------------------------------------------------------------------
rule
echo "RUN 2 — read() vs take() over that same cache."
echo
echo "Identical setup. One take(N=100), then one read(N=100) over what is left."
echo

"$PYTHON" "$LAB_DIR/watcher.py" --take --probe-delay 6 --seconds 14 > "$OUT/run2-watcher.log" 2>&1 &
PIDS+=($!)
sleep 1
"$PYTHON" "$LAB_DIR/station.py" 1 "${VESSELS[@]}" --seconds 12 < /dev/null > "$OUT/run2-station.log" 2>&1 &
PIDS+=($!)
wait

grep -E "^\[mono.*(TAKE take|AFTER read)" "$OUT/run2-watcher.log" | sed 's/^\[[^]]*\] /  /'
echo
echo "  take() removed the samples. The instances survive them — the watcher"
echo "  still knows those three vessels exist, and will still be told when one"
echo "  of them ends. Samples and instances are not the same thing."

# ---------------------------------------------------------------------------
# The three endings.
#
# One shell function rather than three copies, and for once that is the honest
# choice: the comparison between the endings is only worth anything if the
# procedure around them is identical. What differs between the runs is a single
# argument, which is exactly the claim being tested.
#
#   $1 label  $2 ending (leave|bogus|kill)  $3 seconds  $4 station args  $5 watcher args
# ---------------------------------------------------------------------------
ending_run() {
    local label="$1" ending="$2" secs="$3" extra="${4:-}" wextra="${5:-}"
    local tag; tag="$(echo "$label" | tr ' ' '-')"
    local wlog="$OUT/$tag-watcher.log" slog="$OUT/$tag-station.log"
    local fifo="$OUT/$tag.cmd"

    mkfifo "$fifo"
    # shellcheck disable=SC2086
    "$PYTHON" "$LAB_DIR/watcher.py" --seconds "$secs" $wextra > "$wlog" 2>&1 &
    local wpid=$!; PIDS+=($wpid)
    sleep 1
    # shellcheck disable=SC2086
    "$PYTHON" "$LAB_DIR/station.py" 1 "${VESSELS[@]}" --seconds "$secs" $extra < "$fifo" > "$slog" 2>&1 &
    local spid=$!; PIDS+=($spid)
    exec 3> "$fifo"      # opening the write end releases the station's stdin

    # Let discovery finish and a few reports land, so the instance is
    # unambiguously Alive at the watcher before anything ends it.
    sleep 6

    local t0; t0="$(mono_now)"
    case "$ending" in
        leave|bogus) echo "$ending $SUBJECT" >&3 ;;
        kill)        kill -9 "$spid" ;;
    esac
    exec 3>&-

    wait "$wpid" 2>/dev/null || true
    kill "$spid" 2>/dev/null || true
    wait "$spid" 2>/dev/null || true   # so it is not still matching the NEXT run's watcher
    rm -f "$fifo"

    local trans_line state t_trans t_action
    trans_line="$(first_after "$t0" "$wlog" "TRANSITION  mmsi=$SUBJECT")"
    state="$(printf '%s\n' "$trans_line" | sed -n 's/.*-> \([A-Za-z]*\).*/\1/p')"
    t_trans="$(mono_of_line "$trans_line")"
    t_action="$(mono_of_line "$(first_after "$t0" "$slog" "ACTION $ending mmsi=$SUBJECT")")"

    echo "  ending issued at mono          : $t0"
    [[ -n "$t_action" ]] && \
    echo "  writer call entered at mono    : $t_action"
    echo "  watcher TRANSITION at mono     : ${t_trans:-never}"
    echo "  resulting instance_state       : ${state:-none}"
    echo "  seconds from issue to observed : $(delta "$t0" "${t_trans:-}")"
    [[ -n "$t_action" ]] && \
    echo "  seconds from writer call       : $(delta "$t_action" "${t_trans:-}")"
    # The writer's disappearance and the instance's state change are two
    # different events. Printing both means a run where only one of them
    # happens still reports a number rather than a blank.
    local t_gone
    t_gone="$(mono_of_line "$(first_after "$t0" "$wlog" "subscription_matched.*current_change=-1")")"
    echo "  writer declared gone at mono   : ${t_gone:-never}  (+$(delta "$t0" "${t_gone:-}") s)"

    # A second clock on the same event, and the only one that fires in the
    # crash run before the lease does.
    local t_deadline dm_total
    t_deadline="$(mono_of_line "$(first_after "$t0" "$wlog" "STATUS requested_deadline_missed")")"
    dm_total="$({ grep -o 'EXIT requested_deadline_missed  total=[0-9]*' "$wlog" | tail -1 | sed 's/.*=//'; } || true)"
    echo "  first deadline missed at mono  : ${t_deadline:-never}  (+$(delta "$t0" "${t_deadline:-}") s)"
    echo "  requested_deadline_missed total: ${dm_total:-0}"

    SUMMARY+=("$(printf '%-31s %-20s %8s s' "$label" "${state:-none}" "$(delta "$t0" "${t_trans:-}")")")
}

# ---------------------------------------------------------------------------
rule
echo "RUN 3a — the vessel left coverage:  writer.unregister_instance()"
echo
echo "Six seconds of normal reporting, then 'leave $SUBJECT' on the station's"
echo "stdin. The same writer that created the instance ends it."
echo
ending_run "3a leave (unregister)" leave 25

# ---------------------------------------------------------------------------
rule
echo "RUN 3b — the report was spurious:  writer.dispose()"
echo
echo "Identical, except the command is 'bogus $SUBJECT'. This means something"
echo "different from 3a — she was never there — and the interesting question is"
echo "whether the watcher can tell the two apart."
echo
ending_run "3b bogus (dispose)" bogus 25

# ---------------------------------------------------------------------------
rule
echo "RUN 3c — the station crashed:  kill -9, no writer call at all"
echo
echo "Nothing is sent. The watcher has to work out on its own that the writer"
echo "is gone, and it can only do that by waiting out the discovery lease. The"
echo "watcher runs 60 s so it outlasts that wait."
echo
echo "Watch for TWO events at different times: requested_deadline_missed fires"
echo "when the DATA stops (3 s, from the QoS table), and the TRANSITION fires"
echo "when the WRITER is declared gone. Those are different questions."
echo
ending_run "3c kill -9 (crash)" kill 60

# ---------------------------------------------------------------------------
rule
echo "RUN 4 — leave again, with WriterDataLifecycle(autodispose=False)."
echo
echo "Everything is identical to 3a but one policy on the writer. If 3a and 3b"
echo "came out the same, this is the run that says why."
echo
ending_run "4 leave, no autodispose" leave 25 --no-autodispose

# ---------------------------------------------------------------------------
rule
echo "RUN 5 — the crash again, with the reader's DEADLINE removed."
echo
echo "Run 3c produced no instance-state change at all. This run changes exactly"
echo "one thing to find out why: the watcher drops Policy.Deadline and keeps"
echo "everything else, including the station's 3 s deadline. Nothing else in"
echo "this lab omits it, and the ICD's QoS table is unchanged — a reader with no"
echo "deadline cannot tell a stalled vessel from a moving one, which is why the"
echo "contract asks for one."
echo
ending_run "5 kill -9, reader deadline off" kill 40 "" --no-deadline

# ---------------------------------------------------------------------------
rule
echo "SUMMARY — one line per ending"
echo
printf '  %-31s %-20s %10s\n' "ending" "instance_state" "seconds"
printf '  %-31s %-20s %10s\n' "-------------------------------" "--------------------" "----------"
for line in "${SUMMARY[@]}"; do echo "  $line"; done
echo
echo "Two of those numbers are a message crossing a network. One is a timer"
echo "expiring. One of them is not there at all — and run 5 says which single"
echo "policy decided that, which is the finding this lab did not set out to make."
echo
echo "Logs in $OUT"
echo "Now write labs/lab01_instances/findings.md."
