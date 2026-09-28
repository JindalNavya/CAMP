"""
Stage 7 Evaluation — driven by the REAL IBM/VT Docker registry trace.
------------------------------------------------------------------------
Usage:
    python src/run_evaluation_real.py "/path/to/data_centers/stage-dal09-sample" --limit 100 --warmup 0.8 --save-dir outputs

This will:
  1. Load every *.json file in the given folder (one datacenter's logs)
  2. Extract real manifest-pull (image request) events, ordered by real timestamp
  3. Run the same predictor + cache simulation used on synthetic data
  4. Print the same comparison table (no_prefetch / lru / lfu / popularity / predictive)

Start small: point this at ONE datacenter folder and use --limit to cap the
number of files (each day can be several MB to 13+ MB, and there are ~75
days x multiple shards per datacenter x 4 datacenters — loading everything
at once is unnecessary for a first pass and will be slow).
"""

import os
import sys
import csv
import glob
import argparse

from real_trace_loader import load_trace_files
from predictor import DemandPredictor
from cache_manager import simulate

CACHE_CAPACITY_MB = 2000  # tune this once you see real image size distributions


class _Tee:
    """Write everything printed to the console into a log file as well."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, text):
        for st in self.streams:
            st.write(text)
    def flush(self):
        for st in self.streams:
            st.flush()


def main():
    parser = argparse.ArgumentParser(description="Run predictive caching evaluation on real registry trace data")
    parser.add_argument("folder", help="Path to a datacenter folder containing *.json log files")
    parser.add_argument("--limit", type=int, default=10, help="Max number of JSON files to load (default 10)")
    parser.add_argument("--max-events", type=int, default=None, help="Optional cap on number of parsed events")
    parser.add_argument("--warmup", type=float, default=0.7,
                         help="Fraction of trace (chronological) used to train/warm up the predictor before evaluation (default 0.7 = 70%% train / 30%% test)")
    parser.add_argument("--save-dir", default=None,
                         help="If given, also save the console output and a results CSV into this folder")
    args = parser.parse_args()

    log_file = None
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        log_file = open(os.path.join(args.save_dir, f"run_warmup_{args.warmup}.txt"), "w", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, log_file)

    files = sorted(glob.glob(f"{args.folder}/*.json"))
    if not files:
        print(f"No .json files found in {args.folder}")
        sys.exit(1)

    files = files[: args.limit]
    print(f"Loading {len(files)} file(s) from {args.folder} ...")

    trace, image_sizes, nodes, images = load_trace_files(files, max_events=args.max_events)

    if len(trace) < 50:
        print("\nWarning: very few events parsed. Results below will be noisy/unreliable.")
        print("Try increasing --limit to load more files.\n")

    print(f"\n{len(trace)} real deployment events | {len(nodes)} nodes | {len(images)} images")
    split_idx = int(len(trace) * args.warmup)
    print(f"Temporal split: {split_idx} train events (warmup) | {len(trace) - split_idx} test events (evaluated)\n")

    strategies = ["no_prefetch", "lru", "lfu", "popularity", "predictive"]
    results = []
    for strat in strategies:
        res = simulate(
            trace,
            image_sizes,
            predictor_factory=DemandPredictor,
            capacity_mb=CACHE_CAPACITY_MB,
            strategy=strat,
            warmup_fraction=args.warmup,
        )
        results.append(res)

    baseline_latency = results[0]["avg_latency_ms"]

    print(f"{'Strategy':<14}{'Hit Rate':>10}{'Avg Latency(ms)':>18}{'Data Pulled(MB)':>18}{'Improvement':>14}{'False Prefetch':>16}")
    print("-" * 92)
    rows = []
    for r in results:
        improvement = 100 * (baseline_latency - r["avg_latency_ms"]) / baseline_latency if baseline_latency else 0
        print(
            f"{r['strategy']:<14}{r['hit_rate']*100:>9.1f}%{r['avg_latency_ms']:>18.2f}"
            f"{r['bytes_pulled_mb']:>18,.0f}{improvement:>13.1f}%{r['false_prefetches']:>16}"
        )
        rows.append([r["strategy"], round(r["hit_rate"] * 100, 2), round(r["avg_latency_ms"], 2),
                     round(r["bytes_pulled_mb"]), round(improvement, 2), r["false_prefetches"]])

    if args.save_dir:
        csv_path = os.path.join(args.save_dir, f"results_warmup_{args.warmup}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["strategy", "hit_rate_pct", "avg_latency_ms", "data_pulled_mb",
                        "latency_improvement_pct", "false_prefetches"])
            w.writerows(rows)
        print(f"\nSaved: {csv_path}")
        sys.stdout = sys.__stdout__
        log_file.close()


if __name__ == "__main__":
    main()
