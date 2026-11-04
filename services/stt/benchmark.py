"""Manual warm CPU comparison. Run outside busy production hours; never a healthcheck."""

import argparse
import gc
import json
import statistics
import time
from pathlib import Path

from server import MODEL, WhisperModel, recognize

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("audio", type=Path, help="A representative local audio file (max 120 seconds)")
parser.add_argument("--threads", default="1,2,4,8")
parser.add_argument("--runs", type=int, default=3)
parser.add_argument("--cache", default="/models")
parser.add_argument("--language", default="en")
args = parser.parse_args()
if not 1 <= args.runs <= 10:
    parser.error("--runs must be between 1 and 10")
audio = args.audio.read_bytes()
for threads in map(int, args.threads.split(",")):
    if not 1 <= threads <= 64:
        parser.error("thread counts must be between 1 and 64")
    model = WhisperModel(
        MODEL, device="cpu", compute_type="int8", cpu_threads=threads, download_root=args.cache
    )
    recognize(model, audio, args.language)
    times = []
    for _ in range(args.runs):
        start = time.monotonic()
        text = recognize(model, audio, args.language)
        times.append(round(time.monotonic() - start, 3))
    print(
        json.dumps({"threads": threads, "seconds": times, "median": statistics.median(times), "text": text}),
        flush=True,
    )
    del model
    gc.collect()
