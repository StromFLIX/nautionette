"""Isolated native-streaming experiment; no application settings are changed.

Use non-sensitive WAV samples: --print-text explicitly opts into printing transcripts.
Dependencies are deliberately separate from the application's dependency lock.
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import time
from pathlib import Path

from moonshine_voice import ModelArch, Transcriber, get_model_for_language, load_wav_file
from moonshine_voice.transcriber import Error, LineCompleted, LineTextChanged


def measure(transcriber, audio, rate, print_text):
    events, errors = [], []
    started = time.perf_counter()
    fed_seconds = 0
    stopping = False

    def on_event(event):
        if isinstance(event, Error):
            errors.append(event.error)
        if isinstance(event, (LineTextChanged, LineCompleted)):
            events.append(
                {
                    "type": type(event).__name__,
                    "wall_ms": round((time.perf_counter() - started) * 1000),
                    "audio_fed_seconds": round(fed_seconds, 2),
                    "during_stop": stopping,
                    "has_text": bool(event.line.text.strip()),
                    **({"text": event.line.text} if print_text else {}),
                }
            )

    transcriber.remove_all_listeners()
    transcriber.add_listener(on_event)
    transcriber.start()
    compute = 0
    chunk_size = int(0.1 * rate)
    # Real-time replay, not accelerated/offline feeding: every chunk arrives
    # when it would have been captured. If inference falls behind, do not hide it.
    for offset in range(0, len(audio), chunk_size):
        chunk = audio[offset : offset + chunk_size]
        fed_seconds = (offset + len(chunk)) / rate
        time.sleep(max(0, started + fed_seconds - time.perf_counter()))
        before = time.perf_counter()
        transcriber.add_audio(chunk, rate)
        compute += time.perf_counter() - before
    stop_started = time.perf_counter()
    stopping = True
    result = transcriber.stop()
    stop_ms = round((time.perf_counter() - stop_started) * 1000, 1)
    if errors:
        raise RuntimeError("Streaming recognizer failed") from errors[0]
    if result is None or not any(line.text.strip() for line in result.lines):
        raise RuntimeError("Streaming recognizer returned no text")
    return {
        "stop_ms": stop_ms,
        "feed_compute_seconds": round(compute, 3),
        "compute_to_audio_ratio": round(compute / (len(audio) / rate), 3),
        "first_text": next((event for event in events if event["has_text"]), None),
        "text_events_before_stop": sum(e["has_text"] and not e["during_stop"] for e in events),
        "completions_before_stop": sum(e["type"] == "LineCompleted" and not e["during_stop"] for e in events),
        "events": events,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--language", default="en")
    parser.add_argument("--model", choices=["tiny", "small"], default="tiny")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cache", type=Path, default=Path("/workspace/moonshine-models"))
    parser.add_argument("--silence", type=float, default=0, help="Append quiet seconds before Stop")
    parser.add_argument("--print-text", action="store_true")
    args = parser.parse_args()
    if args.runs < 1 or not 0 <= args.silence <= 5:
        parser.error("runs must be positive; silence must be between zero and five seconds")
    audio, rate = load_wav_file(str(args.audio))
    duration = len(audio) / rate
    audio.extend([0.0] * int(args.silence * rate))
    arch = ModelArch.TINY_STREAMING if args.model == "tiny" else ModelArch.SMALL_STREAMING
    model_path, arch = get_model_for_language(
        args.language, arch, cache_root=args.cache, on_progress=lambda *_: None
    )
    model_files = {
        str(path.relative_to(model_path)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(model_path).rglob("*"))
        if path.is_file()
    }
    print(
        json.dumps(
            {
                "package": importlib.metadata.version("moonshine-voice"),
                "platform": platform.platform(),
                "model_arch": arch.name,
                "model_files_sha256": model_files,
                "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
                "audio_seconds": duration,
                "appended_silence_seconds": args.silence,
                "language": args.language,
            }
        ),
        flush=True,
    )
    loaded = time.perf_counter()
    with Transcriber(model_path=model_path, model_arch=arch, update_interval=0.5) as transcriber:
        print(json.dumps({"load_ms": round((time.perf_counter() - loaded) * 1000)}), flush=True)
        for run in range(args.runs):
            print(
                json.dumps({"run": run + 1, **measure(transcriber, audio, rate, args.print_text)}), flush=True
            )


if __name__ == "__main__":
    main()
