"""
Real Trace Loader — IBM/Virginia Tech Docker Registry Trace (FAST 2018)
-------------------------------------------------------------------------
Parses the actual anonymized production registry logs from
https://dssl.cs.vt.edu/drtp/ into the same event schema our
predictor.py / cache_manager.py pipeline already expects, replacing
trace_generator.py's synthetic data with real production behavior.

Real log record format (one JSON array per file):
{
  "host": "<anonymized backend/server id>",
  "http.request.duration": <seconds, float>,
  "http.request.method": "GET" | "HEAD" | ...,
  "http.request.remoteaddr": "<anonymized CLIENT machine>",
  "http.request.uri": "v2/<repo-part-1>/<repo-part-2>/manifests/<ref>"
                     | "v2/<repo-part-1>/<repo-part-2>/blobs/<digest>",
  "http.response.status": <int>,
  "http.response.written": <bytes>,
  "timestamp": "<ISO8601>"
}

Interpretation decisions (write these into your Review 2 Methodology
section — they are the assumptions that turn raw HTTP logs into
"deployment events"):

  - IMAGE identity   = the two repo-path hash segments after 'v2/' joined
                        together. This is the anonymized image/repo name.
  - NODE identity    = http.request.remoteaddr (the client machine pulling
                        the image — the "worker" in your OS project).
  - DEPLOYMENT EVENT = a manifest GET. Docker always fetches the manifest
                        first when starting a container, so manifest-GETs
                        are the natural analogue of a deployment request.
  - Blob GETs within SESSION_WINDOW_SECONDS of a manifest GET for the same
    (node, image) are folded into that deployment's size/latency, since
    they represent the layer pulls belonging to that same image pull.
  - HEAD requests are existence/freshness checks (near-zero payload), not
    actual data pulls — excluded from demand modeling.
"""

import json
import re
import glob
from datetime import datetime
from collections import defaultdict

URI_RE = re.compile(r"v2/([0-9a-f]+)/([0-9a-f]+)/(manifests|blobs)/([0-9a-f]+)")

SESSION_WINDOW_SECONDS = 5.0


def _parse_uri(uri):
    m = URI_RE.search(uri)
    if not m:
        return None
    repo1, repo2, kind, ref = m.groups()
    return f"{repo1}-{repo2}", kind, ref


def _load_records_from_file(path):
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError, UnicodeDecodeError) as e:
        print(f"  skipping {path}: {e}")
        return []


def clean_records(records):
    """
    Stage 2: Data Preprocessing / Cleaning.
    -----------------------------------------
    Real logs are not analysis-ready. This applies explicit, documented
    cleaning rules and returns (clean_records, report) so every dropped
    record is accounted for rather than silently vanishing.

    Cleaning rules applied, in order:
      1. Drop non-pull HTTP methods (PUT/POST/DELETE = pushes/deletes,
         not deployments — these are registry write operations).
      2. Drop non-2xx responses (failed pulls: 404 not found, 5xx server
         errors). A failed request didn't actually deliver an image.
      3. Deduplicate by the log's own "id" field (log shippers like
         Logstash can double-ship entries on retry).
      4. Drop records missing required fields (uri, remoteaddr, timestamp)
         or with a URI that doesn't match the registry v2 pull pattern
         (catalog listing, tag listing, and other non-pull endpoints).
      5. Drop duration outliers using a simple IQR-based cutoff, so one
         network-hiccup request doesn't distort average latency figures.
    """
    report = {
        "total_raw": len(records),
        "dropped_non_pull_method": 0,
        "dropped_bad_status": 0,
        "dropped_duplicate_id": 0,
        "dropped_missing_or_unmatched_uri": 0,
        "dropped_duration_outlier": 0,
        "kept": 0,
    }

    seen_ids = set()
    stage1 = []
    for r in records:
        method = r.get("http.request.method")
        if method not in ("GET", "HEAD"):
            report["dropped_non_pull_method"] += 1
            continue

        status = r.get("http.response.status")
        if status is not None and not (200 <= int(status) < 300):
            report["dropped_bad_status"] += 1
            continue

        rid = r.get("id")
        if rid is not None:
            if rid in seen_ids:
                report["dropped_duplicate_id"] += 1
                continue
            seen_ids.add(rid)

        uri = r.get("http.request.uri", "")
        node = r.get("http.request.remoteaddr")
        ts = r.get("timestamp")
        if not uri or not node or not ts or _parse_uri(uri) is None:
            report["dropped_missing_or_unmatched_uri"] += 1
            continue

        stage1.append(r)

    # duration outlier filter (IQR method) applied only to GET requests,
    # since those carry real transfer time; HEAD durations are near-zero
    # by nature and shouldn't be judged against the same distribution
    durations = sorted(
        r["http.request.duration"]
        for r in stage1
        if r.get("http.request.method") == "GET" and r.get("http.request.duration") is not None
    )
    clean = stage1
    if len(durations) >= 10:
        q1 = durations[int(0.25 * len(durations))]
        q3 = durations[int(0.75 * len(durations))]
        iqr = q3 - q1
        upper_bound = q3 + 3 * iqr  # generous cutoff — only drop extreme hiccups
        clean = []
        for r in stage1:
            d = r.get("http.request.duration")
            if r.get("http.request.method") == "GET" and d is not None and d > upper_bound:
                report["dropped_duration_outlier"] += 1
                continue
            clean.append(r)

    report["kept"] = len(clean)
    return clean, report


def load_raw_records(path_or_glob):
    """Load a single file or a glob pattern of real trace JSON files."""
    files = glob.glob(path_or_glob) if any(c in path_or_glob for c in "*?[") else [path_or_glob]
    records = []
    for f in sorted(files):
        records.extend(_load_records_from_file(f))
    return records


def build_events(records, max_events=None):
    """
    Converts raw registry log records into deployment events compatible
    with trace_generator.py's schema:
        {"t": int, "hour": int, "node": str, "image": str, "size_mb": float}
    plus a real "real_latency_ms" field (ground-truth observed pull
    duration, useful for validating the simulated latency model).
    Returns: (events, image_sizes, nodes, images)
    """
    parsed = []
    for r in records:
        uri_info = _parse_uri(r.get("http.request.uri", ""))
        if uri_info is None:
            continue
        image_id, kind, ref = uri_info
        node = r.get("http.request.remoteaddr")
        method = r.get("http.request.method")
        if method not in ("GET", "HEAD") or node is None:
            continue
        try:
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        parsed.append(
            {
                "ts": ts,
                "node": node,
                "image": image_id,
                "kind": kind,
                "method": method,
                "bytes": r.get("http.response.written", 0) or 0,
                "duration_s": r.get("http.request.duration", 0.0) or 0.0,
            }
        )

    parsed.sort(key=lambda e: e["ts"])

    manifest_events = [e for e in parsed if e["kind"] == "manifests" and e["method"] == "GET"]
    blob_events = [e for e in parsed if e["kind"] == "blobs" and e["method"] == "GET"]

    blobs_by_key = defaultdict(list)
    for b in blob_events:
        blobs_by_key[(b["node"], b["image"])].append(b)

    events = []
    for idx, m in enumerate(manifest_events):
        if max_events is not None and idx >= max_events:
            break
        key = (m["node"], m["image"])
        total_bytes = m["bytes"]
        total_latency = m["duration_s"]
        for b in blobs_by_key.get(key, []):
            if abs((b["ts"] - m["ts"]).total_seconds()) <= SESSION_WINDOW_SECONDS:
                total_bytes += b["bytes"]
                total_latency += b["duration_s"]

        events.append(
            {
                "t": idx,
                "hour": m["ts"].hour,
                "node": m["node"],
                "image": m["image"],
                "size_mb": max(total_bytes / (1024 * 1024), 0.001),
                "real_latency_ms": total_latency * 1000,
                "timestamp": m["ts"].isoformat(),
            }
        )

    image_sizes = {}
    for e in events:
        image_sizes.setdefault(e["image"], []).append(e["size_mb"])
    image_sizes = {img: sum(v) / len(v) for img, v in image_sizes.items()}

    nodes = sorted({e["node"] for e in events})
    images = sorted(image_sizes.keys())
    return events, image_sizes, nodes, images


def load_trace_files(files, max_events=None, verbose=True):
    """Convenience wrapper: list of file paths -> (events, image_sizes, nodes, images)."""
    raw = []
    for f in files:
        raw.extend(_load_records_from_file(f))
    clean, report = clean_records(raw)
    if verbose:
        _print_cleaning_report(report)
    return build_events(clean, max_events=max_events)


def _print_cleaning_report(report):
    print("\n--- Data Cleaning Report (Stage 2) ---")
    print(f"  Raw records loaded:                 {report['total_raw']:>8}")
    print(f"  Dropped - non-pull method (PUT/etc):{report['dropped_non_pull_method']:>8}")
    print(f"  Dropped - failed response (non-2xx):{report['dropped_bad_status']:>8}")
    print(f"  Dropped - duplicate log id:          {report['dropped_duplicate_id']:>8}")
    print(f"  Dropped - missing/unmatched URI:     {report['dropped_missing_or_unmatched_uri']:>8}")
    print(f"  Dropped - duration outlier (IQR):    {report['dropped_duration_outlier']:>8}")
    print(f"  Kept (clean records):                {report['kept']:>8}")
    pct = 100 * report["kept"] / report["total_raw"] if report["total_raw"] else 0
    print(f"  Retention rate:                      {pct:>7.1f}%")
    print("---------------------------------------\n")


if __name__ == "__main__":
    records = load_raw_records("sample_data/sample.json")
    print(f"Loaded {len(records)} raw log records")
    clean, report = clean_records(records)
    _print_cleaning_report(report)
    events, image_sizes, nodes, images = build_events(clean)
    print(f"Derived {len(events)} deployment events across {len(nodes)} nodes, {len(images)} images\n")
    for e in events:
        print(" ", e)
    print("\nImage sizes (avg MB):", image_sizes)
