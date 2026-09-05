# DDS Learning Lab — Vessel Traffic Service

A teaching repository for understanding DDS, built around a simplified Vessel
Traffic Service. It is not a product and not reusable code. The output is
understanding, recorded in each lab's `findings.md`.

## Layout

```
idl/vts.idl          the interface definition (the ICD)
src/vtslab/          the actor processes; config.py holds the domain ID
labs/labNN_name/     README.md (question + prediction), run.sh, findings.md
configs/             Cyclone DDS XML variants
captures/            pcaps, kept with the lab that produced them
```

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running a lab

Each lab is self-contained and explains itself on stdout.

```sh
./labs/lab00_hello/run.sh
```

Read `CLAUDE.md` for the QoS contract and the rules the code is written under.

## Labs

| Lab | Question |
|---|---|
| `lab00_hello` | What does a late subscriber receive under default QoS, and which process is the server? |
| `lab01_instances` | What is an instance, and can a watcher tell a vessel leaving coverage from a spurious report from a crashed station? |
