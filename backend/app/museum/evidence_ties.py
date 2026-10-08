"""Stable identity order within exactly identical evidence, without rounding logits."""
import hashlib

from .local_reranker import ranked_ids

POLICY = 'identical-evidence-stable-v1'


def stable_evidence_ranking(candidates, scores, passages):
    raw = ranked_ids(candidates, scores)
    if len(passages) != len(candidates) or any(not isinstance(p, str) or not p for p in passages):
        raise ValueError('Invalid evidence passages')
    groups = {}
    by_id = {}
    for candidate, score, passage in zip(candidates, scores, passages, strict=True):
        ident = candidate['source_id']
        groups.setdefault(passage, []).append((ident, float(score)))
        by_id[ident] = passage
    # Keep the group's existing score-ranked slots. Only assign its identities
    # to those slots in incoming retrieval order; never move another group.
    queues = {p: iter(ident for ident, _ in members) for p, members in groups.items()}
    ids = [next(queues[by_id[ident]]) for ident in raw]
    audit = [dict(passage_sha256=hashlib.sha256(p.encode()).hexdigest(),
                  source_ids=[ident for ident, _ in members],
                  score_spread=max(s for _, s in members)-min(s for _, s in members))
             for p, members in groups.items() if len(members) > 1]
    return ids, audit
