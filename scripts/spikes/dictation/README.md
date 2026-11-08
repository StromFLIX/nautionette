# Historical streaming dictation spike

Findings: [`docs/spikes/streaming-dictation.md`](../../../docs/spikes/streaming-dictation.md).
Current implementation: [`docs/dictation.md`](../../../docs/dictation.md).

The old browser diagnostic scripts asserted redundant WAV uploads. They are retained
in Git at commit `e933edf`, not the current tree: the current app has only native
self-hosted streaming. Run `services/frontend/tests/speech.spec.js` for current
browser regression coverage.

The original native model traces and benchmark remain as reproducible investigation
evidence. Use the appliance's hash-locked dependencies, not a second dependency lock:

```sh
# From repository root; choose another scratch directory outside the repo if needed.
uv venv --python 3.13 /workspace/stt-venv
uv pip sync --python /workspace/stt-venv/bin/python services/stt/requirements.txt
curl --fail --location \
  https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav \
  --output /workspace/jfk.wav
echo '59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e  /workspace/jfk.wav' \
  | sha256sum --check
/workspace/stt-venv/bin/python scripts/spikes/dictation/moonshine_stream.py \
  /workspace/jfk.wav --runs 3
/workspace/stt-venv/bin/python scripts/spikes/dictation/moonshine_stream.py \
  /workspace/jfk.wav --runs 3 --silence 1
```

The benchmark defaults to the historical Tiny Streaming model. `--model small`
selects Small Streaming, used by the current appliance; `--language de` requires
German input. It downloads public model files on first use, not a cloud inference
API. Package pinning does not pin model files; compare the reported model hashes
with the committed traces when repeating the original experiment.

Input is replayed in 100 ms chunks at real-time cadence. Stop time measures native
recognition only, excluding browser/network/rendering, downloads and model load.
Default output omits transcript text. `--print-text` explicitly enables it; use only
non-sensitive samples. These traces use the public sample, not user recordings.

Production-route verification uses `scripts/check_dictation_deployment.py`; staging
runs it automatically and prints only counts/timings, never transcripts or tokens.
