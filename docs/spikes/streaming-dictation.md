# Spike: near-instant Stop for chat dictation

**Historical investigation, superseded by the [self-hosted implementation](../dictation.md).**
The implemented path is always-on local Moonshine Small Streaming. Cloud cleanup,
external STT configuration, feature flags and the proposed Whisper fallback were
removed rather than implemented. Baseline browser diagnostics are available at
commit `e933edf`; they were removed from current source because they assert retired
WAV-upload behavior. The native benchmark and original traces remain as evidence.

Investigated 2026-11-08 against application commit `5954ce6`. This is an investigation
and isolated executable prototype, **not a rollout of a new chat recording mode**.
No runtime setting, provider credential, application dependency, or production
speech endpoint was changed.

## Conclusion

**Yes: stream and recognize audio during recording, finalize speech during pauses,
and make Stop flush only the unprocessed tail.** Most of the work can finish
before the click. A real local streaming model demonstrated this here: Stop took
92–132 ms on continuous input, or 23–25 ms when given one second of trailing quiet.
These are native model timings in the sandbox, not production browser timings.

Claude.ai appearing instantaneous is consistent with this design, but does not
prove which model, transport, or endpointing algorithm Anthropic uses. We did not
inspect a Claude recording session and should not claim its implementation is known.
Zero perceived delay is plausible; zero computation/network delay is not guaranteed.

Nautionette **already does live transcription**, but repeated file transcription
is not the same as a native stateful streaming recognizer. Both a transport adapter
and a recognizer/protocol change are needed for the larger improvement. Just putting
the current WAV requests onto a WebSocket does not remove their inference cost.

## What the existing implementation actually does

1. `services/frontend/src/speech-worklet.js` emits mono Float32 PCM in 4,096-sample
   blocks: approximately **256 ms at 16 kHz**, not every audio rendering quantum.
   Stop flushes its partial block; the recorder has a 500 ms fallback timeout.
2. `services/frontend/src/speech.js` makes complete PCM16 WAV snapshots every two
   seconds. Each interim includes the **entire current window again**. A 700 ms
   RMS-based quiet boundary finalizes windows of at least two seconds. Hard windows
   are twelve seconds with one-second overlap.
3. `services/frontend/src/components/VoiceControl.vue` serializes requests. Pending
   partials are coalesced, but final windows are retained. An already-running partial
   is not replaced by the final request. Stop always snapshots any nonempty window,
   even if its last partial contained byte-identical audio or the new tail is quiet.
4. `services/backend/nautionette_backend/routers/speech.py` reads the **whole request**,
   sends a multipart file to the STT service, waits for JSON transcription, and only
   then returns SSE. SSE here does **not** mean recognition is incremental: without
   cleanup, `transcript`, `delta`, and `done` contain the already-computed text.
5. `services/stt/server.py` uses CPU faster-whisper small INT8, beam size five,
   VAD enabled, four configured threads and one inference slot. It consumes the
   segment generator to completion before returning `{"text": ...}`. A generator
   of file segments is not a live input stream.
6. If enabled, **LLM cleanup starts once after Stop and final STT**. Its output is
   genuinely streamed, but adds another model operation to the critical path.
   Cleanup is off by default; a device may have enabled it in Workspace settings.

Approximate current critical path:

```text
first partial = capture window + network/upload + file recognition
Stop readiness = worklet flush + residual in-flight/queued work
                 + final-window file recognition + optional LLM cleanup
```

Multiple finalized windows can wait on slow hardware. The per-recorder coalescing
is useful, but is not a global concurrency solution: the backend and appliance
reject concurrent work with 429 instead of allocating per-user live sessions.

## Browser reproduction: actual recorder and composer

The independent Playwright spike drives the real application composer, recorder,
and queue. Only microphone input and STT replies are mocked. All four cases passed:

| Case | Uploaded WAV durations | Finding |
| --- | --- | --- |
| Stop after completed two-second partial | 2, 2 seconds | Second request is **byte-identical** to the completed first one |
| Two-second speech, finalized quiet, another quiet tail, Stop | 2, 2.75, 0.25 seconds | Third request contains **only zeros**, despite the phrase already being finalized |
| Stop while partial is held, with new audio tail | 2, 2.25 seconds | Must wait for the partial **and** a fresh final decode; last words appear only in final |
| Continuous 11.75 seconds, fast STT | 2, 4, 6, 8, 10, 11.75 seconds | **41.75 seconds uploaded** for 11.75 unique seconds: 3.55× audio duplication |

In the queue experiment, an explicitly injected 300 ms residual partial delay plus
300 ms final-response delay gave an observed 1,104 ms readiness wait, including test
polling and browser overhead. **Not a Whisper benchmark or a production latency
measurement.** Uploaded seconds also do not imply the same CPU-cost multiplier;
Whisper's fixed/window/decoder costs are not simply proportional to bytes.

These diagnostic tests intentionally assert today's behavior. They live outside
the routine frontend suite; revise them when implementing fixes rather than
turning redundant uploads into a permanent product contract.

## Real recognizer experiment: Moonshine native streaming

An isolated Python 3.13 environment used **moonshine-voice 0.1.6**, English
**Tiny Streaming**, 100 ms input chunks and a 500 ms transcript-update floor.
Audio was replayed at real-time cadence, not fed all at once. Inference ran locally
on CPU; no audio was sent to a cloud provider. The published `jfk.wav` sample is
11 seconds, SHA-256
`59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e`.

The sandbox exposed eight CPUs, no cgroup CPU quota, and a 2 GiB memory ceiling.
The downloaded English tiny model files occupied approximately 47 MB; native model
load took 240 ms in the unpaused experiment, **excluded from warm Stop timings**.
This differs from the bundled STT's four-CPU deployment quota.

| Real-time replay | Stop ms, three runs | Median Stop | Text-bearing events before Stop | Completed segments before Stop |
| --- | --- | --- | --- | --- |
| Sample, no appended quiet | 132.0, 92.1, 102.5 | **102.5 ms** | 18, 20, 21 | 3 per run |
| Sample + one second of zeros | 22.8, 24.1, 24.8 | **24.1 ms** | 19, 21, 22 | 4 per run |

First nonempty text arrived approximately 0.76–1.32 seconds into playback. This is
**not stable/correct-word latency**: early hypotheses included wrong words and
were revised. Feed-call compute time was roughly 19–24% of playback duration on
this sample. Recognition and endpointing continued during playback; `stop()`
completed the remaining line without replaying the entire recording.

Raw event traces and downloaded model hashes are committed alongside the scripts:
[unpaused](../../scripts/spikes/dictation/moonshine-unpaused.jsonl),
[paused](../../scripts/spikes/dictation/moonshine-paused.jsonl).
They contain only the public sample's transcript; private recordings are not used.

### Accuracy and reproducibility limits

This proves feasibility, **not selection of a production model**. Tiny finalized
“my fellow America” rather than “my fellow Americans”, and had several unstable
interims. We did not compute corpus WER, test German/code-switching/technical
identifiers, test phone hardware, or compare against today's Whisper on identical
hardware. The old Whisper CPU benchmark in `docs/dictation.md` at commit `e933edf` is useful
context but is **not** a controlled head-to-head comparison with these numbers.

Moonshine's current model table lists German small/tiny streaming and MIT licensing
for streaming checkpoints. German was **not downloaded or evaluated here**. The
installed Python downloader still prints a blanket non-commercial warning for
non-English languages; verify the exact model artifact's license before deployment
instead of trusting either that warning or an unpinned README in isolation.

Package dependencies are separately hash-locked. The model catalog downloads its
own artifacts; the script reports their hashes but does not enforce them. Compare
those hashes with the committed traces when repeating the experiment. First-use
download/load, network transit, process scheduling, session concurrency and browser
rendering are outside the Stop measurement. These are six runs of one sample,
not p95 or an appliance-level latency guarantee.

## Alternatives checked against current upstream documentation

| Option | What it provides | Assessment |
| --- | --- | --- |
| Keep faster-whisper, improve rolling windows | Exact-snapshot reuse, better endpointing, bounded scheduling; optional greedy/interim tuning | Smallest first step, no new provider. Cannot make arbitrary unfinished utterances instant; inference still repeats |
| Moonshine streaming, private server adapter | Stateful streaming CPU inference, provisional line updates and completed lines; current English/German models | **Promising self-hosted next candidate** based on real local proof. Evaluate Small Streaming accuracy and four-CPU/concurrency budget before adopting |
| OpenAI Realtime transcription | Live PCM append, deltas, explicit audio-turn commit and final completion | Strong optional managed-provider candidate; requires separate STT authorization, privacy consent and gateway/proxy validation. Not tested with a provider account |
| Deepgram live transcription | Interim results, finalized segments and configurable quiet endpointing | Credible managed alternative; separate billing/credentials and language/identifier evaluation. Documentation reviewed, not benchmarked |
| Soniox real-time | Final/nonfinal tokens and semantic endpoint detection | Another managed candidate; same privacy/cost/access caveats. Documentation reviewed, not benchmarked |
| Whisper-Streaming / SimulStreaming | Streaming policies around multilingual Whisper models | More adaptation/runtime work. Original Whisper-Streaming reports 3.3 s research latency and recommends its successor. That figure is not a universal limit or a measurement here |
| Browser SpeechRecognition or on-device WASM | Potentially low network latency / no server audio | Not a reliable cross-browser/Capacitor default; browser recognizers may themselves send audio to a vendor. WASM needs separate memory, battery, licensing and device tests |

**Current OpenAI detail matters:** documentation accessed on this date recommends
`gpt-live-transcribe` for incremental input. It uses
`wss://api.openai.com/v1/realtime?intent=transcription`, 24 kHz PCM, session updates,
`input_audio_buffer.append`, explicit `input_audio_buffer.commit`, and transcription
`delta`/`completed` events. This model's documented turn detection is omitted/null,
with **client-side VAD**, not `server_vad`/`semantic_vad`. Completion events for
separate turns can arrive out of order; assemble using item IDs, not arrival order.
Older examples/model names should not be copied blindly. Our existing
OpenAI-compatible file endpoint and a chat-model OAuth connection do not establish
access to this separate streaming service.

## Recommended implementation path

### 1. Surgical local improvements first

- Tag snapshots with an exact captured-sample revision. When Stop has **no new
  samples**, promote the matching completed/in-flight partial to final instead of
  scheduling another identical decode. Include window ID and session generation;
  never reuse a result for newly captured speech or a previous recording.
- Track endpointed speech and quiet-only tails. Skip unnecessary tails only with
  trustworthy speech-state information; an RMS threshold alone can miss quiet
  speech. Preserve the worklet flush and last word. Never equate “last text looks
  complete” with “all captured samples were processed”.
- Expose raw-transcript readiness independently of cleanup. Prefer an explicit
  **Polish** action after dictation or apply cleanup only while the draft is
  unchanged. Automatically streaming edits into a now-editable draft would race
  the user's typing. Existing cleanup can remain available without auto-sending.
- Measure first text, last-audio-to-final, click-to-editable, duplicate decoded
  samples and queue depth using numeric metadata only. Do not record audio/text.

These changes remove avoidable work without replacing STT. They are recommendations,
not production fixes included in this spike.

### 2. Add a provider-neutral persistent streaming adapter

```text
AudioWorklet → authenticated same-origin WebSocket → private STT session
             ← replaceable partials / finalized segments / session done
```

- Keep WAV POST as a compatibility fallback; advertise streaming capabilities
  separately. Use 100–250 ms binary PCM frames with sequence numbers, actual sample
  rate and explicit end/cancel controls. Stateful resampling is required when a
  provider needs 24 kHz and a webview captures at another rate.
- Do not expose provider credentials to the browser. Browser WebSockets cannot
  set our usual bearer header: mint a short-lived single-use session ticket through
  authenticated HTTP, validate Origin, and authenticate the socket before allocating
  STT/model resources. The current HTTP `require_user` is not automatically a
  WebSocket authentication implementation. Never put the long-lived app/provider
  token into a URL or logs.
- Separate committed text from replaceable partials, keyed by session + segment
  ID/revision. Stop flushes the capture buffer, finishes input, and awaits final
  acknowledgement. If speech already ended and all segments are final, readiness
  should be nearly immediate. If a word is still being recognized, show that honestly.
- Bound session duration, bytes, pending frames, inference concurrency and idle time.
  Slow consumers must produce a clear error, not unlimited buffering or silent
  frame drops. Cancel/navigation closes upstream and releases model state.
- Reconnection must not automatically replay audio. Preserve the acknowledged draft,
  report interruption and allow a fresh recording; fence late results by generation.
- Verify WebSocket upgrades/timeouts through the backend proxy, Coolify routing,
  native webviews and any agentgateway/provider route. Do not assume the existing
  HTTP speech path provides a working duplex route.

### 3. Promotion gates

Suggested goals, **not measured promises**: warm first useful text ≤1 second;
pause-completed Stop-to-editable p95 ≤150 ms; active-utterance Stop p95 ≤500 ms.
Measure p50/p95 against a consented English/German coding-prompt corpus with
technical terms, mixed language, quiet voices, noise and last-word cutoffs. Compare
exact identifiers as well as WER. Test under four-CPU limits, concurrent sessions,
cold model load, offline/429/disconnect, cancellation, navigation and cleanup races.
Keep the current local recognizer default until accuracy, privacy, resource use
and browser/mobile compatibility meet the gates.

## Verification and sources

Local validation: four diagnostic browser cases, twelve existing browser speech
cases, 198 frontend unit tests, fourteen backend speech tests, frontend build and
Python spike lint/format. Native experiment: three real-time runs each with/without
appended quiet. Reproduction instructions: [scripts README](../../scripts/spikes/dictation/README.md).

Documentation read on 2026-11-08:

- [OpenAI Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)
- [Deepgram endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results)
- [Soniox endpoint detection](https://docs.soniox.com/stt/rt/endpoint-detection)
- [Moonshine models](https://github.com/moonshine-ai/moonshine/blob/main/docs/models/available-models.md)
- [Moonshine streaming API](https://github.com/moonshine-ai/moonshine/blob/main/docs/using/transcription.md)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Whisper-Streaming and successor comparison](https://github.com/ufal/whisper_streaming)
