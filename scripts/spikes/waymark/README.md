# Waymark replacement spike (isolated, not a migration)

Read [the findings](../../../docs/spikes/waymark.md) first. Waymark 0.30.1 is
**not recommended as Nautionette's production workflow engine**. These programs
exercise it against disposable infrastructure. They never deploy workflows,
change live schedules, call a real model, or alter the application dependency lock.

## What runs

- `run.py`: real Postgres-backed Waymark; retries, timeout/late side effect,
  node SIGKILL recovery, worker-only SIGKILL, schedule CRUD; 150 three-action
  workflows per engine at concurrency 1/10/50 and 20-way fan-out.
- The original `workflows/url_digest.py` runs **unmodified** on a local Temporal
  dev server. The Waymark port uses the existing HTTP/artifact implementations.
  The `agent_call` implementation is deliberately replaced with a deterministic
  fixture. Both engines fetch a real public GitHub README and produce identical
  JSON and Markdown. A local 404 server tests the early-return branch.
- `extra.py`: bare actions do not retry; three sleepers exhaust three resident
  VM slots; adding another node relieves the queue; killed workers can leave
  `/healthz` green while the node can no longer execute actions.
- `tail.py`: with one resident VM slot, a successful action's outstanding timeout
  timer can delay the next workflow even after the first result was delivered.

The result-wait helper uses Waymark's **internal** bridge API with a 10 ms poll.
`run.py` separately measures its public SDK's default blocking call (1 s poll).
Internal interfaces are not a proposed production integration.

## Prerequisites

- Python 3.12 and Linux **glibc >= 2.39**, e.g. Ubuntu 24.04 or Debian trixie.
  Nautionette's current worker image is **bookworm**, so its glibc is too old for
  the 0.30.1 Linux wheel. Installing the source distribution does not install
  the executor/bridge binaries. Do not copy the sandbox's private-libc experiment
  into production; use a compatible image or a controlled source build.
- A dedicated disposable PostgreSQL database with `pgcrypto` available.
- `uv` (or pip), network access for the public README and Temporal dev-server
  download, and free localhost ports 24117–24119, 24218–24219, 9118 and 9218.
- For the optional local database recipe, Docker Compose. Docker was unavailable
  in the evaluation sandbox: this Compose recipe is reviewed but not executed.

## Repeat locally

From the **repository root**, on a compatible Linux machine:

```sh
spike=scripts/spikes/waymark
docker compose -f "$spike/compose.yaml" up -d --wait
uv venv --python 3.12 "$spike/.venv"
uv pip install --python "$spike/.venv/bin/python" -r "$spike/requirements.txt"
export PATH="$PWD/$spike/.venv/bin:$PATH"
command -v waymark-executor waymark-bridge waymark-worker
export SPIKE_DISPOSABLE_DB=1
export WAYMARK_DATABASE_URL=postgresql://spike:local-spike-only@127.0.0.1:25432/spike
export SPIKE_OUTPUT="$PWD/$spike/evidence/main"
python "$spike/run.py"

# Run sequentially: these probes intentionally reuse the same localhost ports.
export WAYMARK_DATABASE_URL=postgresql://spike:local-spike-only@127.0.0.1:25432/spike_capacity
export SPIKE_OUTPUT="$PWD/$spike/evidence/capacity"
python "$spike/extra.py"
python "$spike/tail.py"

docker compose -f "$spike/compose.yaml" down
```

Use **fresh databases** for a fresh measurement. Completed timed workflows can
leave timers behind; reusing their database can confound small capacity tests.
The database-only Compose service uses tmpfs and no durable application volume.
The displayed password is a fixed **local fixture**, never a production secret.
The scripts do not print the database DSN.

The harness starts the executor and a separate bridge, sets two workers with ten
concurrent actions each, then shuts them down in `finally`. Restart experiments
SIGKILL the entire executor process group; do not use this on a shared node.
Each run uses a unique business key and writes only within `SPIKE_OUTPUT`.
The programs record findings including **failed fault probes**; an exit code of
zero means the experiment completed, **not** that Waymark passed every criterion.

`run.py` refuses pytest because Waymark's pytest mode executes in-memory, runs
its actions in the test process, and skips durable sleeps. Passing unit tests in
that mode does not establish persistence or crash recovery.

To inspect the dashboard after a run, start a viewer against the same disposable
DB (the test harness already stopped its nodes):

```sh
export PYTHONPATH="$PWD/$spike:$PWD/services/worker"
export WAYMARK_HTTP_ENABLED=true WAYMARK_HTTP_ADDR=127.0.0.1:24119
export METRICS_ADDR=127.0.0.1:9118
export WAYMARK_WORKER_COUNT=2 WAYMARK_CONCURRENT_PER_WORKER=10
waymark-executor
# Open http://127.0.0.1:24119/workflows or /fleet
# Open http://127.0.0.1:24119/api/docs and /api/openapi.json
```

Neither HTTP endpoint nor metrics has authentication. Bind to loopback for this
experiment; use an authenticated private proxy before any real deployment.

## Sandbox run specifics

The evaluation used Python 3.12.16, PostgreSQL 16.2 from `pgserver` 0.1.4,
`pgcrypto` from the PostgreSQL 16 package, and the actual PyPI 0.30.1 x86_64
binaries. Because the sandbox uses glibc 2.36, the binaries were launched via a
**private** Debian 2.41 loader/library path. PostgreSQL needed an isolated NSS
wrapper because UID 10001 has no passwd entry in this sandbox. Neither workaround
modified the host or application images. `WAYMARK_EXECUTOR_COMMAND` and
`WAYMARK_BRIDGE_COMMAND` permit an operator-supplied launcher for that experiment.
These are trusted local commands, not workflow inputs.

Postgres and the local SQLite fixture ledger lived on tmpfs. `fsync`,
`synchronous_commit` and `full_page_writes` were on, but this is **process-crash**
durability, not a persistent-disk, host-loss or production IOPS benchmark.
The Temporal baseline was its local dev server (server 1.32.2, SDK 1.35.0),
**not** our production Temporal 1.29.7/Postgres 17 deployment. Worker topology and
storage are different: never convert the small benchmark into a production
speedup, cost saving or sizing guarantee.
