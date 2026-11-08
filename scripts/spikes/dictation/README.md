# Streaming dictation spike

Findings and implementation recommendation:
[`docs/spikes/streaming-dictation.md`](../../../docs/spikes/streaming-dictation.md).
These experiments do not enable/change chat dictation or contact a cloud STT API.
Run commands from the repository root. `/workspace` paths are scratch examples;
choose another writable directory outside the repository on a developer machine.

## Current chat pipeline diagnostic

Uses the real composer, `createRecorder`, WAV encoding and request queue with
synthetic microphone PCM and stubbed STT replies. The synthetic delays are not
model benchmarks. No production services, microphone permissions, credentials or
models are needed.

```sh
npm ci --prefix services/frontend
cd services/frontend && npx playwright install chromium && cd ../..
node services/frontend/node_modules/@playwright/test/cli.js test \
  --config scripts/spikes/dictation/playwright.config.mjs
```

In the agent sandbox, Chromium is preinstalled; skip the browser installation and
prefix the final command with `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers`.
Port 9012 must be free. Results go to `/workspace/dictation-spike-results`.

The four tests print audio upload durations and detect identical Stop uploads,
quiet-only tails, sequential partial/final delay and growing-window duplication.
They intentionally describe baseline behavior and are outside the regular test
suite. They use Vue dev-only setup bindings to wait for the actual queue to idle;
run against the configured Vite dev server, not a production build. When improving
the pipeline, update these diagnostics and add ordinary regression tests for the
new behavior instead of preserving the old redundant work.

## Actual local streaming recognizer

Install separately from the application environment. The spike dependencies are
version/hash-locked for Python 3.13; application dependency locks are untouched.
The first run downloads a model from Moonshine's public model host. Later runs use
the cache. No API key is required. Native Linux library/system compatibility is
required; this was tested on x86_64 Linux with glibc 2.36.

```sh
uv venv --python 3.13 /workspace/moonshine-venv
uv pip sync --python /workspace/moonshine-venv/bin/python \
  scripts/spikes/dictation/requirements.txt
curl --fail --location \
  https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav \
  --output /workspace/jfk.wav
echo '59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e  /workspace/jfk.wav' \
  | sha256sum --check
/workspace/moonshine-venv/bin/python scripts/spikes/dictation/moonshine_stream.py \
  /workspace/jfk.wav --runs 3
/workspace/moonshine-venv/bin/python scripts/spikes/dictation/moonshine_stream.py \
  /workspace/jfk.wav --runs 3 --silence 1
```

- Replays mono WAV in 100 ms chunks at real-time cadence and uses a 500 ms
  transcript-update floor. Stop measures native finalization only, without browser
  network/rendering time. Downloads and model initialization are excluded.
- Prints input/model hashes, first-text events, streaming completions, feed-call
  compute time and Stop time. It never treats an empty transcript/error as success.
- Default output omits transcript text. `--print-text` explicitly enables it;
  only use non-sensitive audio. The committed unpaused trace used the public sample
  with this option to document recognition errors.
- `--model small` evaluates Small Streaming instead of Tiny Streaming.
  `--language de` selects the German streaming model; **not evaluated in this spike**.
  Check the exact checkpoint license before using a new language/model.
- Package pinning does not pin remote model contents: compare printed hashes with
  `moonshine-unpaused.jsonl` / `moonshine-paused.jsonl`. Downloaded model files and
  audio are deliberately not committed.
- This sample demonstrates incremental processing, not accuracy on dictation,
  German/English code-switching, phone compatibility or production p95 latency.

Update the isolated dependency lock deliberately:

```sh
uv pip compile scripts/spikes/dictation/requirements.in --python-version 3.13 \
  --generate-hashes -o scripts/spikes/dictation/requirements.txt
```
