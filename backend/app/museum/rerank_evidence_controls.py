"""Offline evidence controls; neither experiment is enabled in live retrieval.

Material preference handles only an explicit, single-material browse query.
It promotes original-field matches, never declares nonmatches absent, and
never drops unknown candidates or modifies model scores/source records.
"""
import re

VERSION = 'repeated-title-and-material-v1'
MATERIALS = {
    '大理石': 'marble', '青铜': 'bronze', '石膏': 'plaster',
    '陶土': 'terracotta', '瓷器': 'porcelain', '瓷': 'porcelain',
    '木制': 'wood', '木质': 'wood',
}
MATERIAL_QUERY = re.compile(
    r'(?:找|寻找|看看|搜索|我想找|帮我找)?(?:用)?('
    + '|'.join(sorted(MATERIALS, key=len, reverse=True))
    + r')(?:制作|制成|材质)?的?(?:作品|藏品|雕塑|器物|花瓶)?[？?。！!]?'
)
LABELS = {'materialsAndTechniques', 'Materials and techniques', '材质 Medium'}
UNCERTAIN = re.compile(
    r'\b(?:base|stand|mount|pedestal|frame|support|original|effect|imitation|faux|'
    r'simulat\w*|imitat\w*|resembl\w*|after|not|no|without|formerly|removed|lost)\b', re.I
)


def remove_repeated_title(passage):
    """Remove only schema title lines exactly repeating the existing header.

    Preserve every other byte/line; do not spend freed tokens on new excerpts.
    The original packed evidence remains available in the experiment input.
    """
    lines = passage.split('\n')
    title = lines[0]
    return '\n'.join([title] + [line for line in lines[1:]
        if line not in ('Title: ' + title, '名称 Title: ' + title)])


def prioritize_material(query, ordered_ids, records):
    match = MATERIAL_QUERY.fullmatch(query.strip())
    audit = dict(version=VERSION, status='not_applicable', matched_ids=[], evidence={})
    if not match:
        return list(ordered_ids), audit
    material = MATERIALS[match.group(1)]
    audit.update(status='explicit_material', material=material)
    if len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError('Duplicate source')
    for sid in ordered_ids:
        record = records[sid]
        if record['_id'] != sid or record['status'] != 'active':
            raise ValueError('Invalid or inactive source')
        values = [line.split(':', 1)[1].strip() for line in record['content'].splitlines()
                  if ':' in line and line.split(':', 1)[0].strip() in LABELS]
        # Multiple authoritative fields must agree. Uncertainty stays unknown.
        if values and all(re.search(r'\b' + material + r'\b', value, re.I)
                          and not UNCERTAIN.search(value) for value in values):
            audit['matched_ids'].append(sid)
            audit['evidence'][sid] = '\n'.join(values)
    matched = set(audit['matched_ids'])
    return audit['matched_ids'] + [sid for sid in ordered_ids if sid not in matched], audit
