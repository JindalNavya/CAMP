# Outputs

Files here are produced by the code; regenerate them with `--save-dir outputs`:

```bash
python src/run_evaluation_real.py /path/to/stage-dal09-sample --limit 100 --warmup 0.8 --save-dir outputs
python src/run_evaluation_real.py /path/to/stage-dal09-sample --limit 100 --warmup 0.7 --save-dir outputs
```

| File | Contents |
|---|---|
| `run_warmup_<w>.txt` | Full console output: cleaning report, split sizes, results table |
| `results_warmup_<w>.csv` | The results table as CSV (hit rate, latency, data pulled, improvement, false prefetches) |
| `results_summary_graph.png` | Bar chart of hit rate and average latency per strategy, drawn from the 80/20 results in the top-level README |

The `.txt` and `.csv` files only exist after you run the commands above on the real sample.
