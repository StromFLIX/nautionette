# Live chat dictation

Type as before, or use the microphone in the joined mic/send control. The mic
becomes a stop button with a live input-level display, timer and cancel control
to its left. **Text appears while you speak**, not just after stopping. Stop
finalizes the last audio window and optionally streams an LLM edit into the
**editable draft**. Nothing sends a chat message automatically.

The attachment **plus** and chat preferences sit together at the left. The model
picker lives only inside chat preferences alongside the agent configuration.
Reported context usage is now a circular indicator in the top-right chat header;
unknown usage remains unknown, not a fabricated zero.

## Live transcription protocol

The browser captures mono PCM through AudioWorklet (ScriptProcessor compatibility
fallback for older webviews). It sends independently decodable WAV snapshots
approximately every two seconds, while recording continues. Unlike chopping a
MediaRecorder WebM stream, every snapshot contains its own complete audio header.

- Pauses (700 ms quiet after a two-second window) finalize phrases.
- Windows are capped at twelve seconds, with a one-second overlap at forced
  boundaries. Overlapping recognized words are deduplicated.
- One request is in flight per recorder. Pending interim snapshots are coalesced
  to the newest one; finalized windows are never dropped.
- Partial text may be revised as more of its window is heard. Finalized phrases
  are retained while the next phrase is transcribed.
- The editor is gated during dictation/finalization to prevent incoming partials
  overwriting edits. Stop to edit, or cancel to restore the original typed draft.
- Navigation stops the microphone and aborts client requests. A late permission
  grant releases its tracks immediately instead of opening an orphan recording.

This is **rolling-window live transcription over a standard STT API**, not a
native token-by-token streaming model. First text takes the window interval
**plus inference and network time**. Slow hardware can produce less frequent
updates; coalescing prevents an unbounded queue of obsolete partial results.
At Stop, only the remaining window needs finalization, not the whole recording.
The current implementation can still submit a byte-identical final snapshot or a
quiet-only tail, and Stop waits behind any in-flight request. See the
[streaming dictation spike](spikes/streaming-dictation.md) for reproduced cases,
real native-streaming latency measurements, and the proposed improvement path.

## Default appliance

`docker-compose.yaml` includes `stt`, a private CPU-only faster-whisper service:

- Whisper **small**, INT8; four CPU threads, 4 CPU quota, 3 GiB memory ceiling.
- One inference at a time; overload returns 429 rather than building a queue.
- Browser recordings stop at 120 seconds. Each request is limited to 12 MiB and
  the appliance independently checks the decoded audio duration (120 seconds).
- OpenAI-compatible `POST /v1/audio/transcriptions` multipart API, JSON response.
- No host port, no public domain, no Docker socket or provider credentials.
- Non-root user; model downloads persist in the stack's `stt-models` volume.
- Temporary multipart spooling is on `/tmp` tmpfs, not persistent disk. Audio
  and transcripts are not saved to chat history, events, or application logs.
- Dependency versions and hashes are locked in `services/stt/requirements.txt`.

First startup downloads the model (approximately 0.5 GB) from Hugging Face and
can take several minutes. Readiness is available only after loading; the
healthcheck has a ten-minute startup grace period. A model download failure
restarts only this service: it never prevents the main application starting.
The backend reports a retryable dictation error while the service is unavailable.

Recording requires HTTPS (localhost is also a secure context), microphone
permission and Web Audio. Unsupported devices retain normal typing. Android's
Capacitor WebView uses the same capture pipeline; the generated manifest gets
`RECORD_AUDIO` and `MODIFY_AUDIO_SETTINGS` from `scripts/sync-android.mjs`.
Capacitor requests both and rejects capture if either is missing, even when
microphone access was allowed. Only `RECORD_AUDIO` displays a runtime prompt;
`MODIFY_AUDIO_SETTINGS` is granted at install time. This manifest fix requires
installing a newly built APK; updating the web server alone cannot fix an old APK.
If access is actually denied, allow Microphone in the phone's Nautionette app
settings and ensure Android's system-wide microphone privacy toggle is on, then
retry. Browser users should check the site's microphone permission instead.

## CPU benchmark and configuration

Warm Whisper small INT8, public whisper.cpp `jfk.wav` sample (about 11 seconds),
three measured runs after warmup, eight available sandbox CPUs:

| Threads | Median seconds | Relative to two threads |
| --- | ---: | ---: |
| 1 | 7.743 | 72% slower |
| 2 | 4.508 | baseline |
| 4 | 2.618 | 42% less time |
| 8 | 3.487 | 23% less time |

All runs produced the same sample transcript. This is a latency comparison,
**not an accuracy evaluation or a production-host benchmark**. Four threads were
faster than eight here; blindly allocating more CPUs did not help. Benchmark
representative English/German coding prompts on the actual host before increasing
further. Set both thread count **and** container quota, not just one.
`services/stt/benchmark.py /path/to/sample.wav --threads 1,2,4,8 --cache /models`
repeats the warm comparison in the appliance environment. Mount the sample read-only
and temporarily allow at least eight CPUs when comparing eight threads. This manual
command prints its sample transcript; use non-sensitive benchmark audio.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `STT_BASE_URL` | `http://stt:8000/v1` | Backend-only OpenAI-compatible API base URL; explicitly empty disables dictation |
| `STT_MODEL` | `small` | Transcription model sent to the endpoint; also the bundled appliance's loaded model |
| `STT_API_KEY` | empty | Optional backend-only bearer credential for a replacement endpoint |
| `STT_THREADS` | `4` | Bundled appliance's inference thread count |
| `STT_CPUS` | `4` | Container CPU quota |
| `STT_MEMORY` | `3g` | Container memory ceiling |
| `STT_IMAGE` | stack-specific local image | Image override; Compose builds the bundled appliance by default |

## Replacing the service

For another faster-whisper/whisper.cpp/GPU service, point `STT_BASE_URL` at its
OpenAI-compatible `/v1` API and set its model name. The browser and backend API
need no changes. The replacement must accept multipart `file`, `model`, optional
`language`, and `response_format=json`, and return `{"text":"..."}`. Audio upload
and request limits still apply in Nautionette. Replacements must enforce their
own decoded-duration and inference limits.

To stop building/running the bundled service, use a Compose override with
`services.stt.profiles: [local-stt]` and configure the replacement URL. No backend
`depends_on` references the appliance. To run it later, enable that profile.
Merely changing `STT_BASE_URL` does not stop the original container.

For a custom prebuilt image, an override can set `services.stt.build: !reset null`
and `services.stt.image: your-pinned-image`; adjust its command, model volume and
healthcheck to that implementation. The backend API boundary stays unchanged.
A native streaming model would require an additional adapter; merely swapping
the HTTP URL does not change this client's rolling-window protocol.

## Optional LLM cleanup

**Settings → Workspace** offers **Clean up dictation**, off by default, plus
automatic/English/German language selection. These per-device preferences live
only in Settings, not in the chat preferences panel. The selected chat model is
used when recording ends.

With cleanup off, live recognized text is simply finalized. With cleanup on, the
completed raw transcript is retained in memory and the LLM's edited text streams
into the draft through the existing authenticated model gateway. Cleanup runs
**once at Stop**, not on each partial. No coding-agent container, tools or chat
turn is started. Copilot Responses and Chat Completions routes are supported.
Editing instructions preserve meaning, language, names, paths and technical
identifiers rather than answering the text. Review the draft: model editing is
never guaranteed to preserve every detail.

A successful edit offers **Use original transcript**, available while that draft
remains unchanged. If cleanup fails, the original transcript replaces partial
cleanup automatically. STT failure retains the existing typed draft and text
already recognized. Cancel explicitly discards all dictated additions.

Self-hosted STT keeps audio on your infrastructure. **Cloud LLM cleanup sends the
transcript to the selected provider**, even though audio stays local. A replacement
cloud STT endpoint receives the audio. Neither step auto-sends a chat message.

## Tests

- Backend: `uv run pytest tests/backend/test_speech.py`
- UI: `cd services/frontend && npx playwright test tests/speech.spec.js --workers=1`
  includes actual browser AudioWorklet capture with a synthetic microphone device.
- Appliance (real audio decode, no model download): install its locked requirements
  in an isolated Python 3.13 environment, then `python -m unittest discover -s services/stt`.
- Update dependency lock: `uv pip compile services/stt/requirements.in --python-version 3.13 --generate-hashes -o services/stt/requirements.txt`.
