"""Work-level BM25F: normalize field TF, combine, then saturate once.

Uses the same positive-smoothed IDF and tokenizer as the existing BM25.
This is not a weighted sum of separately saturated BM25 scores.
"""
from collections import Counter, defaultdict
import math

from .bm25 import tokenize


class BM25FIndex:
    def __init__(self, weights, k1=1.5, b=0.75):
        if not weights or any(not math.isfinite(w) or w <= 0 for w in weights.values()):
            raise ValueError("Field weights must be finite and positive")
        if not math.isfinite(k1) or k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid BM25F parameters")
        self.weights, self.k1, self.b = dict(weights), k1, b
        self.docs = {}
        self.postings = {}
        self.average_lengths = {}

    def index(self, docs):
        docs = list(docs)
        if len({d['_id'] for d in docs}) != len(docs):
            raise ValueError("Duplicate work ID")
        self.docs = {d['_id']: d for d in docs}
        self.postings = defaultdict(dict)
        lengths = {f: {} for f in self.weights}
        for doc in docs:
            for field in self.weights:
                tf = Counter(tokenize(doc['search_fields'].get(field, '')))
                lengths[field][doc['_id']] = sum(tf.values())
                for term, count in tf.items():
                    self.postings[term].setdefault(doc['_id'], {})[field] = count
        # Include absent fields as zero-length: one fixed corpus of works.
        self.average_lengths = {f: sum(v.values()) / max(len(docs), 1) for f, v in lengths.items()}
        self.lengths = lengths

    def search(self, query, top_k=25):
        if top_k <= 0:
            return []
        scores, matched = defaultdict(float), defaultdict(set)
        for term, query_tf in Counter(tokenize(query)).items():
            postings = self.postings.get(term, {})
            if not postings:
                continue
            # A term appearing in several fields counts as one work for DF.
            df, n = len(postings), len(self.docs)
            idf = math.log1p((n - df + 0.5) / (df + 0.5))
            for sid, field_counts in postings.items():
                combined_tf = 0.0
                for field, tf in field_counts.items():
                    norm = 1 - self.b + self.b * self.lengths[field][sid] / self.average_lengths[field]
                    combined_tf += self.weights[field] * tf / norm
                    matched[sid].add(field)
                scores[sid] += query_tf * idf * (self.k1 + 1) * combined_tf / (self.k1 + combined_tf)
        ranked = sorted(scores, key=lambda sid: (-scores[sid], sid))[:top_k]
        return [dict(id=sid, source_id=sid, score=scores[sid],
                     matched_fields=sorted(matched[sid])) for sid in ranked]
