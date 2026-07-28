"""Compare controlled visual candidates with a manually reviewed truth set."""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path


def evaluate(predictions: dict, truth: dict) -> dict:
    metrics = {}
    for group in next(iter(truth.values())):
        total = top_hits = passed = passed_hits = 0
        errors = Counter()
        for key, expected_groups in truth.items():
            expected = set(expected_groups[group])
            value = predictions["results"][key]["groups"][group]
            predicted = value["top_label"]
            hit = predicted in expected
            total += 1
            top_hits += int(hit)
            if value["accepted"]:
                passed += 1
                passed_hits += int(hit)
                if not hit:
                    errors[(predicted, "/".join(sorted(expected)))] += 1
        metrics[group] = {
            "samples": total,
            "top_label_accuracy": round(top_hits / total, 4),
            "threshold_coverage": round(passed / total, 4),
            "threshold_precision": round(passed_hits / passed, 4) if passed else None,
            "false_accepts": passed - passed_hits,
            "common_false_accepts": [
                {"predicted": pair[0], "expected": pair[1], "count": count}
                for pair, count in errors.most_common()
            ],
        }
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    predictions = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    missing = sorted(set(truth) - set(predictions["results"]))
    extra = sorted(set(predictions["results"]) - set(truth))
    if missing or extra:
        raise ValueError(f"sample mismatch missing={missing} extra={extra}")
    payload = {"sample_count": len(truth), "metrics": evaluate(predictions, truth)}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
