"""Experimental equal-weight RRF over works rather than individual chunks."""

VERSION = 'work-rrf-v1'


def fuse_works(lexical_hits, vector_hits, *, top_k=25, rrf_k=60):
    """Keep the best occurrence of each source per lane before ranking works.

    Each lane gets one vote per work. Repeated chunks affect diagnostics only,
    never the score. The two upstream chunk candidate pools remain unchanged.
    The caller must still backfill and validate authoritative source records.
    Equal-score ties preserve first appearance, lexical lane first.
    """
    if top_k < 1 or rrf_k < 0:
        raise ValueError('Invalid fusion bounds')
    combined = {}
    for lane, hits in [('lexical', lexical_hits), ('dense', vector_hits)]:
        works = {}
        for chunk_rank, hit in enumerate(hits, 1):
            source_id = hit.get('source_id')
            if not isinstance(source_id, str) or not source_id:
                raise ValueError('Every candidate must identify its source')
            if source_id not in works:
                works[source_id] = dict(work_rank=len(works)+1, best_chunk_rank=chunk_rank,
                    best_chunk_id=hit['id'], best_score=hit.get('score'),
                    best_content=hit.get('content', ''), chunk_ids=[])
            if hit['id'] not in works[source_id]['chunk_ids']:
                works[source_id]['chunk_ids'].append(hit['id'])
        for source_id, evidence in works.items():
            row = combined.setdefault(source_id, dict(id=source_id, source_id=source_id, _rrf=0.0, lanes={}))
            row['_rrf'] += 1.0 / (rrf_k + evidence['work_rank'])
            row['lanes'][lane] = dict(evidence, match_count=len(evidence['chunk_ids']))
    return sorted(combined.values(), key=lambda row: row['_rrf'], reverse=True)[:top_k]
