"""Experimental bounded pool; no gold labels or special work IDs permitted."""
import re

VERSION='protected-pool-v1'


def protected_pool(query,candidates,sources,fallback_ids,limit):
    if limit not in (8,12,16,25):raise ValueError('Invalid pool bound')
    ids=[c['source_id'] for c in candidates]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate candidates')
    protected=set(ids)&set(fallback_ids[:5])
    reasons={sid:['fallback_top5'] for sid in protected}
    records={r['_id']:r for r in sources}
    normalized=lambda text:' '.join(re.findall(r'[^\W_]+',text.casefold()))
    q=normalized(query)
    for candidate in candidates:
        sid=candidate['source_id']
        for lane,details in candidate.get('lanes',{}).items():
            if details.get('work_rank')==1:
                protected.add(sid);reasons.setdefault(sid,[]).append(lane+'_first')
        title=normalized(records[sid]['title'])
        if len(title)>2 and (' '+title+' ') in (' '+q+' '):
            protected.add(sid);reasons.setdefault(sid,[]).append('literal_title')
    # Never discard a protected item just to meet a budget. An overflow must
    # be measured; deterministic identifier/author/date routes run upstream.
    selected=set(protected)
    for sid in ids:
        if len(selected)>=limit:break
        selected.add(sid)
    result=[c for c in candidates if c['source_id'] in selected]
    return result,dict(version=VERSION,limit=limit,protected=reasons,
                       overflow=len(protected)>limit,dropped=[sid for sid in ids if sid not in selected])
