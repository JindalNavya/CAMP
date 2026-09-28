"""
Stage 7: Evaluation
---------------------
Runs the full pipeline across all baselines specified in your Review 1 doc:
no prefetching, LRU, LFU, popularity caching, and predictive caching.

Reports: cache hit rate, average deployment latency, bandwidth pulled,
and false-prefetch count (predicted+cached but never actually requested).
"""

from trace_generator import generate_trace
from predictor import DemandPredictor
from cache_manager import simulate

CACHE_CAPACITY_MB = 4000  # per-node cache budget


def main():
    trace, image_sizes, nodes, images = generate_trace(n_events=8000, n_images=40, n_nodes=8)

    strategies = ["no_prefetch", "lru", "lfu", "popularity", "predictive"]
    results = []
    for strat in strategies:
        res = simulate(
            trace,
            image_sizes,
            predictor_factory=DemandPredictor,
            capacity_mb=CACHE_CAPACITY_MB,
            strategy=strat,
        )
        results.append(res)

    baseline_latency = results[0]["avg_latency_ms"]

    print(f"{'Strategy':<14}{'Hit Rate':>10}{'Avg Latency(ms)':>18}{'Data Pulled(MB)':>18}{'Improvement':>14}{'False Prefetch':>16}")
    print("-" * 92)
    for r in results:
        improvement = 100 * (baseline_latency - r["avg_latency_ms"]) / baseline_latency
        print(
            f"{r['strategy']:<14}{r['hit_rate']*100:>9.1f}%{r['avg_latency_ms']:>18.2f}"
            f"{r['bytes_pulled_mb']:>18,.0f}{improvement:>13.1f}%{r['false_prefetches']:>16}"
        )


if __name__ == "__main__":
    main()
