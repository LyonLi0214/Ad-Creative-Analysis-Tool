"""Report adjacent-sample change scores for scene detector calibration."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from .extractor import _histogram


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    for raw in args.paths:
        path = Path(raw)
        cap = cv2.VideoCapture(str(path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, round(fps * args.interval))
        samples = []
        for index in range(0, frames, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok: break
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            samples.append((index / fps, _histogram(frame), gray))
        cap.release()
        scores=[]
        for current, previous in zip(samples[1:], samples[:-1]):
            hist = float(cv2.compareHist(previous[1], current[1], cv2.HISTCMP_BHATTACHARYYA))
            pixel = float(np.mean(cv2.absdiff(previous[2], current[2])) / 255.0)
            scores.append((max(hist, pixel * 2.0), current[0], hist, pixel))
        print(f"\n{path.name} samples={len(samples)}")
        for combined, timestamp, hist, pixel in sorted(scores, reverse=True)[:12]:
            print(f"t={timestamp:6.2f} combined={combined:.3f} hist={hist:.3f} pixel={pixel:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
