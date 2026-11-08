# Self-hosted streaming chat dictation

Press the microphone, speak, then press Stop. Text appears while you speak; Stop
flushes the final audio block and waits only for the recognizer's final acknowledgement.
The result is an editable draft. **Nothing is sent as a chat message automatically.**
Cancel restores the draft that existed before recording. A connection failure keeps
already recognized words and releases the microphone; it never reconnects or replays
audio automatically. Navigation also closes the microphone and private session.

## One local path

```text
Browser/WebView AudioWorklet → authenticated backend WebSocket → private stt service
                            ← live transcript revisions / final acknowledgement
```

The appliance uses **Moonshine Small Streaming**, with local English and German
models loaded at startup. It performs incremental inference as audio arrives and
finalizes phrases during pauses. Audio is not repeatedly uploaded as growing WAV
files. Stop sends only the final worklet block and an end marker, never the entire
recording again. When a phrase has already finalized during a pause, Stop can feel
immediate. If the final word is still being recognized, finalization can take longer.

**There are no cloud transcription providers, provider API keys, cleanup LLM calls,
feature flags, or alternative dictation modes.** The old WAV transcription endpoint,
SSE wrapper, overlap/deduplication queue, cloud cleanup UI and gateway edit method
have been removed. `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`, `STT_THREADS` and
`STT_IMAGE` are obsolete and ignored; remove them from existing deployment env lists.
The backend's speech address is fixed to the private Compose service, not an
operator-configurable public API.

### Language

**Settings → Workspace → Dictation language** selects **English** or **German**.
The initial default follows the browser's language (German for `de`, English
otherwise). A saved English/German choice is retained. The old automatic language
choice migrates to that default; old cleanup preferences cannot trigger any network
call. Streaming uses the selected language model, not automatic language detection.
Choose the language you will speak. Mixed-language and technical identifiers still
need review; recognition is not guaranteed to preserve every word.

## Privacy and authentication

Audio and interim transcripts remain in memory on your infrastructure. They are not
written to chat history, the event bus or application logs. Only a draft that you
explicitly send becomes a normal chat message. Cancelling frees recognizer state.

The browser first requests a **30-second, single-use ticket** using the existing
app authentication. It sends that ticket in the first WebSocket frame, not in a URL.
The backend binds it to the HTTP request's Origin and validates it before connecting
to the speech service. No app or provider credential appears in the socket URL.

The speech container has **no public port/domain, Docker socket, or provider
credentials**. Initial model files are downloaded from Moonshine's public model host
and cached in the existing `stt-models` volume. This downloads model files only;
recordings are never sent there. Subsequent cached startup and inference run locally.
The English/German streaming models are MIT-licensed; the appliance includes their
license notice. The upstream Python downloader currently prints an outdated blanket
non-commercial warning for German, but that warning does not describe the selected
streaming model's [license](https://github.com/moonshine-ai/moonshine/blob/main/LICENSE).

## Limits and operations

- Mono PCM16 frames with increasing sequence numbers; actual sample rate 8–48 kHz.
  Moonshine handles resampling within its stream. Worklet blocks are 2,048 samples
  (128 ms at 16 kHz); older webviews use the same protocol via ScriptProcessor.
- Maximum 120 seconds / 12 MiB of audio, 65,540-byte frames, 4,096 frames, 16,000
  transcript characters and 256 recognized segments. Both backend and appliance
  enforce recording/frame limits. Socket input queues and client send buffering
  are bounded; slow connections fail clearly rather than silently losing audio.
- One session at a time. Busy requests fail rather than accumulating recordings or
  inference jobs. Unauthenticated handshakes have a separate bounded budget and
  cannot take the inference slot. Handshake and idle/finalization waits have timeouts.
- Stop waits for the actual worklet flush. A missing flush acknowledgement reports
  interruption rather than claiming an incomplete recording is complete.
- Cancellation waits for already-running native inference before freeing state or
  admitting another session. No background native worker can use freed model memory.
- Compose keeps the service non-root with `no-new-privileges`, a model volume and
  RAM-backed temporary space. It does not gate backend startup. The healthcheck
  becomes ready after both models load and has a ten-minute first-download grace.
- `STT_CPUS` (default `4`) and `STT_MEMORY` (default `3g`) only control container
  resource limits. The appliance fixes ORT inference to one worker per session,
  avoiding dozens of competing worker threads across two resident models.
  Python/native dependencies are version- and hash-locked.

In the implementation smoke test, both models loaded in approximately 14 seconds
with a populated package cache and initial model downloads; their files totalled
approximately 266 MB. An English public sample streamed through the real backend
and appliance produced 15 live updates before Stop and finalized in 406 ms.
With the final fixed CPU policy, appliance-only English finalization measured
253 ms without added quiet and 30 ms after one second of quiet; synthetic German
measured 622 ms and 11 ms respectively. These are sandbox measurements, not
production p95 promises. The synthetic German sample produced live text but had
recognition errors; it is not an accuracy benchmark. Review dictated identifiers
and words before sending.

## Browser and mobile requirements

Recording requires HTTPS (localhost is also secure), microphone permission, Web
Audio and WebSockets. The routing proxy must preserve WebSocket upgrades on
`/api/speech/stream`. Normal typing remains usable on unsupported devices.

Android's Capacitor WebView needs both `RECORD_AUDIO` and `MODIFY_AUDIO_SETTINGS`,
which the Android sync script supplies. Only the former shows a permission prompt;
the latter is granted at install time. If permissions were missing in an older APK,
install a newly built APK; updating the web server cannot repair its manifest. If
permission is denied, check the app/site microphone permission and Android's
system microphone privacy toggle. iOS declares microphone usage during sync.

## Verification

```sh
uv run pytest tests/backend/test_speech.py
cd services/frontend && npm test
npx playwright test tests/speech.spec.js --workers=1
# Back at repository root:
uv venv --python 3.13 /tmp/stt-venv
uv pip sync --python /tmp/stt-venv/bin/python services/stt/requirements.txt
cd services/stt && /tmp/stt-venv/bin/python -m unittest test_server -v
```

The browser tests include actual AudioWorklet capture, single-pass audio transport,
cancellation/navigation, permission failures, disconnects, late-result fencing and
retired cleanup preferences. Appliance tests exercise PCM decoding, budgets,
segment finality, native cancellation and session disposal without model downloads.

Staging deployment automatically runs `scripts/check_dictation_deployment.py`
through the deployed HTTPS/WebSocket route with the public, hash-checked JFK sample.
It reports counts/timings only, never tokens or transcripts. Readiness may retry
before capture; a started recording is never replayed. The historical
[spike](spikes/streaming-dictation.md) records the investigation that led here; its
cloud alternatives and feature-flag/fallback proposals were not implemented.
