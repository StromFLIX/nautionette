# Project-mounted agent startup / time to first text

Measured on 2026-11-11 against checkout / running app `ddc120a4`, Pi 1.5.2,
Node 24.22.2, model `copilot/gpt-6.1-sol`, Responses API, configured `xhigh`
reasoning. This is a diagnostic spike, **not an end-to-end app benchmark or a
shipped performance improvement**. Raw payload-free samples are in
[`project-chat-ttft-results.json`](project-chat-ttft-results.json).

## What actually ran

1. Tried the real gateway MCP route and the native callable-agent listing tool.
   Both returned HTTP 401. The MCP response reported an expired authentication
   token. System/integration inspection and launching a new app chat through MCP
   were therefore unavailable. No app token, backend control access or Docker
   socket was available in this sandbox. We did not change authentication or
   guess integration configuration to get around this.
2. The **model** route worked. Ran fresh `pi --mode json --no-session` children
   inside this agent container, with the selected repository already mounted at
   `/projects/75877a2836524b4ab436b7e594a360b5`. Used the installed production
   gateway extension and live model, a single local `read` tool, an explicit empty
   federated-tool selection, no history, and the prompt:
   `Reply with exactly READY. Do not call any tools.` Every measured run completed
   successfully and replied `READY`. Each reported 574 input tokens and zero
   cache-read tokens. The mounted repository was not read by this microbenchmark.
3. Repeated with **only `MCP_URL` changed** to a local, immediate JSON-RPC server
   implementing initialize / initialized / tools-list and returning an empty
   catalog. The model still ran through the real gateway. This isolates bridge
   discovery from model generation; it does not simulate a healthy 209-tool app.
4. Separately timed the actual `prepareProjects` function for five new, detached
   worktrees and five preparations of those existing worktrees. A local bare
   shared clone of this 2,513-file repository, scratch worktree roots and the
   configured authorship settings were used. The existing project worktree and
   its commits/files were not changed. Clone time was outside the measurement.

Five serial runs per condition, after an initial live-model smoke run. Filesystem
and module caches were warm; every Pi process and every "new worktree" was new.
Timings use monotonic clocks. This is a small, unpaired sample, not a p95/SLO study.

## Results

| Condition | Median | Range |
| --- | ---: | ---: |
| Fresh Pi process → first text, failing real MCP discovery | **3,063 ms** | 2,836–3,278 ms |
| Fresh Pi process → first text, immediate empty MCP control | **2,184 ms** | 1,761–2,205 ms |
| Pi first JSON event, failing real MCP discovery | 1,549 ms | 1,490–1,573 ms |
| Pi first JSON event, immediate empty MCP control | 592 ms | 563–599 ms |
| Gateway extension factory, failing MCP (`PI_TIMING=1`) | 1,027 ms | 997–1,070 ms |
| Gateway extension factory, immediate empty MCP control | 55 ms | 47–63 ms |
| Model `turn_start` → first text, failing MCP condition | 1,489 ms | 1,260–1,783 ms |
| Model `turn_start` → first text, empty MCP control | 1,593 ms | 1,193–1,609 ms |
| Local new worktree preparation | **37.8 ms** | 36.3–40.8 ms |
| Local existing worktree preparation | **6.2 ms** | 4.8–6.9 ms |

**Measured conclusion:** synchronous MCP discovery added roughly a second of
pre-model startup in this environment, even with no federated tools selected.
The controlled first-text median was 879 ms lower, with provider variation.
Local Git worktree preparation was much smaller. We cannot infer the latency of
Docker mounts, GitHub token issuance, or a full 209-selected-tool prompt from
these results. The actual chat has 209 selected MCP names, much more context and
many native creative tools; this lean 574-token microbenchmark is not equivalent.

## Critical-path research

The current path is:

```text
POST message
  model catalog / capability validation
  accept + persist job
  reconcile prior chat agents / legacy session and attachments
  backend worktree preparation (lock + metadata; no GitHub fetch)
  GitHub repository-scoped token issuance
  broker image readiness / claims / mount resolution
  Docker create / copy private configuration / start
  supervisor config copy / prepareProjects / packages / native session
  spawn Pi / import gateway extension
  MCP initialize → initialized → tools/list → OAuth-tool catalog (when present)
  initial model request / prefill / possible hidden reasoning
  first text_delta → broker → backend SQLite event → SSE → client
```

Code references:

- `services/backend/nautionette_backend/routers/chats.py`: message admission and
  model capability lookup before acceptance. A new chat also starts background
  title generation, which can compete with the main request; not measured here.
- `services/backend/nautionette_backend/conversations.py::_run_turn`: sequential
  reconciliation, local preparation and `credentials.start()` before streaming.
- `services/backend/nautionette_backend/projects.py::agent_credentials`: one new
  `installation_access` GitHub request per selected project, currently serial.
  The existing installation-token cache is not used for these turn-scoped tokens;
  blindly reusing it would break the revoke-on-finish ownership contract.
- `services/docker-broker/nautionette_docker_broker/agent_run.py::_run`: image
  readiness, claims, Docker creation and start. `started` precedes most of this
  work and is **not** a token or proof that Pi is running.
- `images/pi-base/project-git.mjs::prepareProjects`: cached local revision and
  detached worktree checkout. It does **not** fetch the remote each turn.
- `images/agent-sets/default/extensions/nautionette/index.ts`: the async extension
  factory always connects and discovers the full MCP catalog before applying the
  selection filter, including for `tools: []`. The bridge catch allows model work
  to continue after a failure, but the failed network wait has already happened.
- `images/pi-base/agent-run.mjs`: `turn_start` sets the model-wait phase before any
  text exists; the OTLP `gen_ai.chat` span instead starts at assistant
  `message_start`. In these runs that event arrived around the first streamed
  text, so this span alone cannot diagnose pre-first-byte provider waiting.
- `conversations.py::turn_events` polls persisted events every **100 ms**. The
  actual browser posts JSON with `queue: true` and watches `chat_snapshots`, which
  polls every **250 ms** (`services/frontend/src/api.js`). These are alternative
  delivery paths: do not add the two poll periods together. Browser paint and
  networking add further delay. Neither polling path was in the Pi microbenchmark.

## Improvements, in priority order

### 1. Measure the complete path and repair the failing MCP route

Add per-turn monotonic milestones/spans for admission/catalog, reconciliation,
credential issuance, image wait, Docker create/start, worktree/session readiness,
Pi-ready, actual provider-request start, first reasoning delta, first text delta,
and client receipt/paint. Existing activity buckets and model-duration spans do
not provide this breakdown. Keep telemetry payload-free. Distinguish tool-argument
streaming from first prose; hidden reasoning is not an observable first token.

Inspect backend health, integration status and gateway route selection to establish
why MCP got the expired-token response while model requests succeeded. We could
not access those controls, so the underlying auth/routing cause remains unverified.
Do not disable authentication or silently remove tools. Public `/healthz` on both
production and staging was HTTP 200 at `ddc120a4`; that does not prove MCP health.

### 2. Remove unnecessary eager MCP discovery

- **Low-risk first change:** with an explicit empty MCP selection, skip discovery
  after registering the local output/subagent/creative-tool wrappers. Their MCP
  transports can connect when actually used. Test publication grants, empty vs
  null selection, subagent eligibility, and OAuth behavior before shipping.
- For normal tool-rich chats, serve a revision-keyed, permission-scoped catalog
  snapshot from the trusted gateway/backend, or discover only selected targets
  on demand. Preserve OAuth gates and schema/version invalidation. Do not cache
  capability tokens or revive stale permissions from a previous turn.
- Separate slow/unavailable external MCP servers from the first-party startup
  path. A bounded timeout alone is not enough: deferred tools must remain
  discoverable/callable with explicit errors, not disappear silently.

The local empty-catalog experiment suggests about one second of opportunity in
this particular failed-discovery scenario, not a guaranteed production saving.

### 3. Reduce model-side prefill and reasoning only with quality checks

The lean live model still took about **1.5–1.6 s** from `turn_start` to first
text. Benchmark the real selected schemas/system prompt/history next. Compare
`medium`/`high` with the current `xhigh` on representative coding tasks; do not
change every coding chat to low reasoning based on a one-word response.

Defer rarely used tool schemas rather than declaring the entire integration
surface on each request. Preserve a deterministic system/tool prefix and inspect
provider-reported cache-read tokens across resumed turns. Exact caching features,
thresholds and request fields depend on the Copilot route; OpenAI's direct-API
behavior must not be assumed to pass through unchanged. All measured prompts
reported zero cache-read tokens. Do not claim that a smaller output-token cap
alone improves TTFT; it primarily bounds generation length.

### 4. Profile and overlap project credentials/container work

Bounded parallel issuance across **different selected repositories** could replace
the current sum of GitHub RTTs with the slowest small batch. Keep tokens narrow,
turn-owned and revocable; on cancellation/failure, revoke every successfully
issued token. Consider overlapping safe local/container preparation with token
issuance using an explicit readiness barrier before Git access. Measure first:
we have no GitHub-issuance or Docker-start timing from this session.

Avoid wholesale warm-agent pooling as the first fix. This application deliberately
uses disposable containers and private per-chat worktrees/session volumes. Pooling
would need equivalent isolation, stop/credential-revocation and crash-recovery
proofs; a warm broker/image cache is safer than retaining a cross-chat agent.

### 5. Improve event delivery after startup

Replace steady-state SQLite/snapshot polling with an event-driven wake-up while
retaining persisted replay cursors, reconnect and restart recovery. This can
remove a 0–250 ms snapshot wait from the current browser path. It will not fix a
multi-second provider or discovery wait. Don't simply lower polling intervals
and multiply per-client database load.

## Repeatable full-app benchmark

[`scripts/benchmark_chat_ttft.py`](../../scripts/benchmark_chat_ttft.py) creates a
**dedicated** chat, runs real turns through the authenticated HTTP/SSE endpoint,
and retains that chat for inspection. It includes project worktree creation,
GitHub access, broker/container startup, Pi and model latency. It does not measure
the browser snapshot/render path. It requires the existing deployment access
token through the environment; no provider key or new authentication is needed.

```sh
export BASE=https://app.nautionette.stage.stromflix.com
# Set APP_TOKEN through your existing secure environment; never commit it.
uv run python scripts/benchmark_chat_ttft.py \
  --project-id <staging-project-id> --samples 5 > /tmp/ttft-project.json

# No-project control (explicitly overrides an agent's default project selection).
uv run python scripts/benchmark_chat_ttft.py --samples 5 > /tmp/ttft-no-project.json

# Optional controls: same model, lower reasoning; or explicit empty MCP selection.
uv run python scripts/benchmark_chat_ttft.py \
  --project-id <staging-project-id> --model copilot/gpt-6.1-sol \
  --reasoning-effort medium --samples 5 > /tmp/ttft-medium.json
```

Use the first turn for new-worktree/session latency and subsequent turns for
resumed-session latency; every turn still gets a new agent container. For a cold
sample distribution, repeat the command to create independent chats and
interleave controls. Keep model, effort, selected tools, repository revision,
network location and concurrent load fixed. A staging snapshot may have a
preexisting cache but its new benchmark chat gets its own worktree. Inspect the
retained chat if any tool was called despite the no-tools prompt. Delete benchmark
chats through the normal UI/API after inspection, not by removing worktree files.

A successful measurement requires a nonempty streamed text delta, successful
agent result **and** terminal `done`, not just headers/status/`started`/first
reasoning. Failure exits nonzero, retains the evidence and stops only the probe's
exact turn after an incomplete stream. No transcript/tool payloads or credentials
are included in the JSON report. Five samples are enough for a directional smoke
test; gather at least tens of interleaved runs before making percentile claims.

**Not executed against the full app here:** its authenticated access was
unavailable. The script's protocol/error/timing behavior is covered by local
mock-transport tests; those tests are not live TTFT evidence.

## External research

- [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)
  (retrieved 2026-11-11): reduce round trips, parallelize independent work, stream
  responses, and measure the actual bottleneck. Reducing total generated tokens
  is not the same metric as first-token latency.
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
  (retrieved 2026-11-11): prefix/cache behavior and support vary by model/settings;
  verify cache-read usage, stable tool definitions and actual route support.
- Installed Pi CLI docs and `dist/core/timings.js`: `PI_TIMING=1` reports resource
  and extension factory timing. Use JSON `message_update` / `text_delta` receipt
  with a monotonic clock for first text, not the timestamp of a completed native
  session message. Session message timestamps are not per-token arrival times.
