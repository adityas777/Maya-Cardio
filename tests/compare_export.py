"""
compare_export.py — Compare an export file against ground truth (§9).

Usage:
    python tests/compare_export.py --export export.jsonl --mode ramp [--session ID]
    python tests/compare_export.py --export export.jsonl --mode ground_truth --truth truth.jsonl

In ramp mode: validates every sample line against the ramp formula.
In ground_truth mode: checks the export against an official ground-truth file.
"""

from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tests'))
from reference import ramp_s1, ramp_s2


def compare_ramp(export_path: str) -> bool:
    """Validate each sample line against the ramp formula. Returns True if PASS."""
    errors = 0
    total_samples = 0
    total_losses  = 0

    with open(export_path, 'r') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  LINE {line_no}: JSON parse error: {exc}")
                errors += 1
                continue

            if "index" in obj:
                # Sample line
                sensor = obj["sensor"]
                idx    = obj["index"]
                vals   = obj["values"]
                total_samples += 1

                if sensor == 1:
                    expected = [ramp_s1(idx, c) for c in range(3)]
                elif sensor == 2:
                    expected = [ramp_s2(idx, c) for c in range(2)]
                else:
                    print(f"  LINE {line_no}: unknown sensor {sensor}")
                    errors += 1
                    continue

                if vals != expected:
                    print(f"  LINE {line_no}: sensor={sensor} idx={idx} GOT={vals} EXPECTED={expected}")
                    errors += 1
            else:
                # Loss line
                total_losses += 1

    if errors == 0:
        print(f"PASS – {total_samples} samples correct, {total_losses} loss lines")
        return True
    else:
        print(f"FAIL – {errors} mismatches in {total_samples} samples")
        return False


def compare_ground_truth(export_path: str, truth_path: str) -> bool:
    """
    Compare export to an official ground-truth file (same JSON lines format).
    Returns True if PASS.
    """
    def _load(path):
        samples = {}
        losses  = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "index" in obj:
                    key = (obj["sensor"], obj["index"])
                    samples[key] = obj["values"]
                else:
                    losses.append(obj)
        return samples, losses

    exp_samples, exp_losses = _load(export_path)
    tru_samples, tru_losses = _load(truth_path)

    errors = 0
    # Check every truth sample present in export with correct values
    for (sensor, idx), tru_vals in tru_samples.items():
        if (sensor, idx) not in exp_samples:
            print(f"  MISSING sensor={sensor} idx={idx} from export")
            errors += 1
        elif exp_samples[(sensor, idx)] != tru_vals:
            print(f"  WRONG VALUES sensor={sensor} idx={idx} export={exp_samples[(sensor,idx)]} truth={tru_vals}")
            errors += 1

    # Check no extra samples in export that aren't in truth
    for (sensor, idx) in exp_samples:
        if (sensor, idx) not in tru_samples:
            print(f"  EXTRA sample sensor={sensor} idx={idx} in export (not in truth)")
            errors += 1

    if errors == 0:
        print(f"PASS – {len(tru_samples)} samples match, {len(tru_losses)} loss lines")
        return True
    else:
        print(f"FAIL – {errors} mismatches")
        return False


def main():
    parser = argparse.ArgumentParser(description="Compare export against ground truth")
    parser.add_argument("--export", required=True, help="Export JSONL file")
    parser.add_argument("--mode", required=True, choices=["ramp", "ground_truth"])
    parser.add_argument("--truth", help="Ground truth JSONL (required for ground_truth mode)")
    args = parser.parse_args()

    if args.mode == "ramp":
        ok = compare_ramp(args.export)
    else:
        if not args.truth:
            print("--truth required for ground_truth mode")
            sys.exit(1)
        ok = compare_ground_truth(args.export, args.truth)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
