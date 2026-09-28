"""
Stage 1: Workload Collection
-----------------------------
Generates a synthetic container deployment trace that mimics realistic
patterns researchers report in the literature you reviewed:
  - Popularity skew (a small set of images dominate requests - Zipf-like)
  - Sequential/co-occurrence patterns (image B often follows image A on a node)
  - Time-of-day patterns (some images are deployed mostly in "business hours")

In a real system, this stage would instead pull from Kubernetes/Docker
deployment event logs, registry pull logs, or orchestrator audit logs.
"""

import random
import numpy as np


def generate_trace(
    n_events=6000,
    n_images=40,
    n_nodes=8,
    seed=42,
):
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    # Image catalog: each image has a size (MB) and a base popularity weight (Zipf-like)
    image_ids = [f"img-{i:02d}" for i in range(n_images)]
    zipf_weights = np_rng.zipf(a=1.4, size=n_images).astype(float)
    zipf_weights = zipf_weights / zipf_weights.sum()
    image_sizes = {img: rng.randint(80, 1800) for img in image_ids}  # MB

    # Node "personalities": each node has a preferred subset of images
    # (simulates the fact that specific services/teams run on specific nodes)
    node_ids = [f"node-{i}" for i in range(n_nodes)]
    node_affinity = {
        node: rng.sample(image_ids, k=max(4, n_images // 4)) for node in node_ids
    }

    # Sequential/co-occurrence structure: build a simple "next image" bias
    # so a Markov-style predictor has real signal to learn from.
    transition_bias = {img: rng.sample(image_ids, k=3) for img in image_ids}

    trace = []
    t = 0
    last_image_on_node = {node: None for node in node_ids}

    for _ in range(n_events):
        node = rng.choice(node_ids)
        last_img = last_image_on_node[node]

        # 70% chance: follow the sequential bias from the last image on this node
        # 30% chance: pick from the node's affinity set weighted by global popularity
        if last_img is not None and rng.random() < 0.7:
            img = rng.choice(transition_bias[last_img])
        else:
            candidates = node_affinity[node]
            weights = [zipf_weights[image_ids.index(c)] for c in candidates]
            img = rng.choices(candidates, weights=weights, k=1)[0]

        # time-of-day pattern: add an hour-of-day feature with mild skew
        hour = int(np_rng.normal(loc=12, scale=5)) % 24

        trace.append(
            {
                "t": t,
                "hour": hour,
                "node": node,
                "image": img,
                "size_mb": image_sizes[img],
            }
        )
        last_image_on_node[node] = img
        t += 1

    return trace, image_sizes, node_ids, image_ids


if __name__ == "__main__":
    trace, sizes, nodes, images = generate_trace()
    print(f"Generated {len(trace)} deployment events across {len(nodes)} nodes and {len(images)} images")
    print("Sample events:")
    for e in trace[:5]:
        print(" ", e)
