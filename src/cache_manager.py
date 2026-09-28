"""
Stage 4: Candidate Ranking
Stage 5: Proactive Prefetching
Stage 6: Cache Management
--------------------------------
Implements the predictive cache and three baselines to compare against
(Stage 7 requires: no prefetch, LRU, LFU / popularity, prediction-only).

Latency model (kept simple but realistic for a simulation):
  - cache HIT  -> ~5 ms (local disk read)
  - cache MISS -> pull_time = size_mb / bandwidth_mbps * 1000 (ms) + registry_latency
"""

from collections import OrderedDict, Counter

BANDWIDTH_MBPS = 250       # simulated network bandwidth to registry
REGISTRY_RTT_MS = 40       # fixed round-trip overhead per miss
CACHE_HIT_MS = 5


def pull_time_ms(size_mb):
    return REGISTRY_RTT_MS + (size_mb / BANDWIDTH_MBPS) * 1000


class BaseCache:
    """Shared interface: capacity in MB, tracks contents per node."""

    def __init__(self, capacity_mb):
        self.capacity_mb = capacity_mb
        self.node_caches = {}  # node -> OrderedDict(image -> size_mb)  (order = recency for LRU)
        self.node_freq = {}    # node -> Counter(image -> hits) for LFU

    def _ensure_node(self, node):
        if node not in self.node_caches:
            self.node_caches[node] = OrderedDict()
            self.node_freq[node] = Counter()

    def used_mb(self, node):
        return sum(self.node_caches[node].values())

    def has(self, node, img):
        self._ensure_node(node)
        return img in self.node_caches[node]

    def touch_hit(self, node, img):
        """Record a cache hit (used for LRU ordering / LFU counting)."""
        self._ensure_node(node)
        self.node_caches[node].move_to_end(img)
        self.node_freq[node][img] += 1

    def insert(self, node, img, size_mb, eviction_policy="lru"):
        self._ensure_node(node)
        cache = self.node_caches[node]
        if img in cache:
            cache.move_to_end(img)
            return
        while self.used_mb(node) + size_mb > self.capacity_mb and cache:
            if eviction_policy == "lru":
                victim, _ = cache.popitem(last=False)  # oldest
            else:  # lfu
                victim = min(cache.keys(), key=lambda k: self.node_freq[node][k])
                cache.pop(victim)
            self.node_freq[node].pop(victim, None)
        if size_mb <= self.capacity_mb:
            cache[img] = size_mb
            self.node_freq[node][img] += 1


def rank_candidates(predictions, image_sizes, min_confidence=0.15):
    """
    Stage 4: Candidate Ranking.
    utility = predicted_probability * (expected_latency_saved) / (size_penalty)
    Filters out low-confidence predictions to avoid wasting bandwidth on
    unlikely images (this is the "confidence-aware admission" novelty point).
    """
    ranked = []
    for img, prob in predictions:
        if prob < min_confidence:
            continue
        size = image_sizes[img]
        expected_latency_saved = prob * pull_time_ms(size)
        size_penalty = max(size, 1) ** 0.5  # discourage prefetching huge low-value images
        utility = expected_latency_saved / size_penalty
        ranked.append((img, prob, utility))
    ranked.sort(key=lambda x: x[2], reverse=True)
    return ranked


def simulate(trace, image_sizes, predictor_factory, capacity_mb, strategy, warmup_fraction=0.0):
    """
    Runs one full pass over the trace under a given strategy and returns
    metrics: hit_rate, avg_latency_ms, total_bytes_pulled_mb, false_prefetches.

    strategy: one of "no_prefetch", "lru", "lfu", "popularity", "predictive"

    warmup_fraction: fraction (0.0-1.0) of the trace, in chronological order,
    used purely to warm up the predictor's counts and populate the cache
    BEFORE any metric is recorded. This implements the temporal train/test
    split described in the Experimental Setup: the model "trains" (learns
    transition counts) on the early portion of real history, then is
    evaluated only on its ability to predict/serve the later, unseen
    portion — avoiding the classic mistake of testing on data the online
    model has already memorized.
    """
    cache = BaseCache(capacity_mb)
    predictor = predictor_factory() if strategy in ("predictive",) else None
    global_popularity = Counter()

    split_idx = int(len(trace) * warmup_fraction)

    total_latency = 0.0
    hits = 0
    bytes_pulled = 0
    false_prefetches = 0
    prefetched_not_used = {}  # node -> set of images we speculatively pulled
    scored_events = 0

    for idx, event in enumerate(trace):
        node, img, size = event["node"], event["image"], event["size_mb"]
        global_popularity[img] += 1
        in_test_window = idx >= split_idx

        # --- BEFORE the request: proactive prefetch step for predictive/popularity strategies ---
        if strategy == "predictive" and predictor is not None:
            preds = predictor.predict_next(node, top_k=3)
            for cand_img, cand_prob, _ in rank_candidates(preds, image_sizes):
                if not cache.has(node, cand_img):
                    cache.insert(node, cand_img, image_sizes[cand_img], eviction_policy="lru")
                    if in_test_window:
                        prefetched_not_used.setdefault(node, set()).add(cand_img)
                        bytes_pulled += image_sizes[cand_img]

        elif strategy == "popularity":
            top_imgs = [i for i, _ in global_popularity.most_common(3)]
            for cand_img in top_imgs:
                if not cache.has(node, cand_img):
                    cache.insert(node, cand_img, image_sizes[cand_img], eviction_policy="lfu")
                    if in_test_window:
                        prefetched_not_used.setdefault(node, set()).add(cand_img)
                        bytes_pulled += image_sizes[cand_img]

        # --- The actual deployment request arrives ---
        if strategy == "no_prefetch":
            hit = cache.has(node, img)
            if not hit:
                cache.insert(node, img, size, eviction_policy="lru")
                if in_test_window:
                    bytes_pulled += size
        elif strategy == "lru":
            hit = cache.has(node, img)
            cache.insert(node, img, size, eviction_policy="lru")
            if not hit and in_test_window:
                bytes_pulled += size
        elif strategy == "lfu":
            hit = cache.has(node, img)
            cache.insert(node, img, size, eviction_policy="lfu")
            if not hit and in_test_window:
                bytes_pulled += size
        elif strategy in ("popularity", "predictive"):
            hit = cache.has(node, img)
            if hit:
                if node in prefetched_not_used and img in prefetched_not_used[node]:
                    prefetched_not_used[node].discard(img)  # it was useful
            else:
                cache.insert(node, img, size, eviction_policy="lru")
                if in_test_window:
                    bytes_pulled += size

        if in_test_window:
            scored_events += 1
            if hit:
                hits += 1
                total_latency += CACHE_HIT_MS
            else:
                total_latency += event.get("real_latency_ms", pull_time_ms(size))
        if hit:
            cache.touch_hit(node, img)

        # online learning: update predictor AFTER observing the real event
        # (this runs during warmup too — that's the whole point of warmup)
        if predictor is not None:
            predictor.observe(event)

    # count leftover speculative pulls that were never actually requested = false prefetches
    # (only counted within the test window to match the other metrics)
    for node, imgs in prefetched_not_used.items():
        false_prefetches += len(imgs)

    n = scored_events if scored_events else 1
    return {
        "strategy": strategy,
        "hit_rate": hits / n,
        "avg_latency_ms": total_latency / n,
        "bytes_pulled_mb": bytes_pulled,
        "false_prefetches": false_prefetches,
        "train_events": split_idx,
        "test_events": scored_events,
    }
