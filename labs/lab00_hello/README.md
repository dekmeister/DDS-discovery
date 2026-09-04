# Lab 00 — hello, and is anybody in charge?

## The question

Two questions, both about the shape of the system rather than its data:

1. Under completely default QoS, what does a subscriber receive if it starts
   **after** the publisher has already been writing for ten seconds?
2. Which process is the server?

And one thing to look at rather than ask: what does "default QoS" actually
resolve to? The ICD's QoS table describes deviations from a baseline that is
nowhere written down, so both programs print their fully resolved writer and
reader QoS on startup.

## Prediction

Skipped.

## Claude's prediction, recorded before the code was written

**Q1: zero.** The cause is `Durability`, not `History`. Cyclone's default
durability is `VOLATILE`, and a volatile writer offers nothing to a reader it
had not yet matched — the pre-match samples are not merely un-retransmitted,
they are not candidates for delivery. The trap is that default `History` is
`KEEP_LAST(1)`, so the writer genuinely still holds the most recent sample in
its cache when the reader appears. It does not send it. Under
`TRANSIENT_LOCAL` the answer would be exactly 1, and that one-sample difference
is why the `Zone` topic in the ICD is transient-local. Confidence: high.

**Q1, secondary:** in the reverse order (subscriber first), sample 1 may still
be missing, because the publisher writes immediately after creating its writer
and the match takes some tens of milliseconds. Confidence that sample 1 does
arrive: ~70%.

**Q2: none.** `libddsc` is linked into each process; there is no broker, no
daemon, no registry. Evidence: either process runs alone without error; start
order changes what data arrives but never whether the system works;
`cyclonedds ls` discovers both as a third peer given no address; killing either
takes the other's matched count `1 → 0` with no restart. The honest caveat is
that `TRANSIENT_LOCAL` will later put historical data in the *writer*, which
makes it a data source — still not a server, since it need not exist first.

## Running it

```sh
./run.sh              # the three scenarios
./run.sh --trace      # and first, dump Cyclone's resolved configuration
```

Logs land in `out/<UTC timestamp>/` and are gitignored; quote what matters into
`findings.md`.

`run.sh` reads the domain ID from `src/vtslab/config.py` rather than restating
it, and sets no `CYCLONEDDS_URI` for the scenarios — the lab is about what
happens when nothing is configured.

## What the programs do

`pub.py` and `sub.py` each build their entity tree literally, ask for no QoS,
and print what Cyclone gave them anyway. Both declare `vts::VesselReport`
inline as an `IdlStruct` rather than generating it, so Lab 00 has no build
step; Lab 01 introduces `idlc -l py`. The inline declaration states its
typename explicitly and matches the ICD's field list exactly, so the generated
type in Lab 01 is the same type on the wire rather than a near-miss.

Both install a listener for every status the entity can raise, not just the
interesting one. Under default QoS most stay silent for the whole run, and that
silence is only a result because the hooks are demonstrably installed.

Two things in the output are worth more attention than they first appear:

- **The last `RECV` line of a run** is a sample with no valid data, reporting
  `instance_state=NotAliveDisposed`. The publisher exiting *disposed* the
  instance rather than merely abandoning it, because the writer's resolved QoS
  includes `WriterDataLifecycle(autodispose=True)`. Lab 02 makes a subject of
  the difference between that and `NotAliveNoWriters`.
- **`cyclonedds ls` reports the topic's XTypes Type ID** alongside the type
  name — the thing that actually has to agree between the two processes.

## Environment note

`--trace` reports that Cyclone chose its network interface **arbitrarily** from
the sixteen up, multicast-capable interfaces on this host (mostly Docker
bridges), and picked `wlp2s0` — so lab discovery traffic goes out on the LAN.
Both processes make the same choice, so this is not a problem today. If
`subscription_matched` ever fails to fire, this is the first suspect, and the
fix is a `NetworkInterfaceAddress` in a lab-local `CYCLONEDDS_URI` recorded in
`findings.md` as an environment finding.
