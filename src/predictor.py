"""
Stage 2: Data Preprocessing
Stage 3: Prediction
----------------------------
Builds a baseline predictor as your doc specifies:
"Build a baseline predictor first (frequency/recency or Markov-style model)"

We implement a per-node Markov-chain predictor combined with global
frequency/recency, which is the natural first model before moving to
a learned ML predictor (e.g. LSTM / gradient boosting) in later reviews.
"""

from collections import defaultdict, Counter


class DemandPredictor:
    def __init__(self, decay=0.98):
        # transition_counts[node][last_image][next_image] = count
        self.transition_counts = defaultdict(lambda: defaultdict(Counter))
        # global frequency with recency decay (Stage 2 feature: frequency + recency)
        self.freq_score = defaultdict(float)
        self.last_image_per_node = {}
        self.decay = decay

    def observe(self, event):
        """Update the model with one real deployment event (online learning)."""
        node, img = event["node"], event["image"]

        # recency-decayed frequency: every observation ages out old counts a bit
        for k in self.freq_score:
            self.freq_score[k] *= self.decay
        self.freq_score[img] += 1.0

        last_img = self.last_image_per_node.get(node)
        if last_img is not None:
            self.transition_counts[node][last_img][img] += 1
        self.last_image_per_node[node] = img

    def predict_next(self, node, top_k=3):
        """
        Returns a list of (image, probability) predicting what this node
        is likely to request next, blending:
          - Markov transition probability (sequential pattern)
          - global frequency/recency score (fallback when little history)
        """
        last_img = self.last_image_per_node.get(node)
        scores = Counter()

        if last_img is not None and last_img in self.transition_counts[node]:
            trans = self.transition_counts[node][last_img]
            total = sum(trans.values())
            for img, cnt in trans.items():
                scores[img] += 0.75 * (cnt / total)

        # blend in global popularity (normalized) as a fallback signal
        total_freq = sum(self.freq_score.values()) or 1.0
        for img, f in self.freq_score.items():
            scores[img] += 0.25 * (f / total_freq)

        if not scores:
            return []

        ranked = scores.most_common(top_k)
        # normalize the returned scores to look like confidence probabilities
        s = sum(v for _, v in ranked) or 1.0
        return [(img, v / s) for img, v in ranked]
