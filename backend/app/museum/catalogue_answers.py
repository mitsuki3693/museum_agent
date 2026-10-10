"""Literal answers for an already selected work, never photo identification.

Only whole, narrow questions are eligible. Keep qualifiers, maker roles and
original vocabulary verbatim; no generated translations or inferred facts.
"""
import hashlib
import re
import unicodedata

VERSION = 'catalogue-literal-answer-v1'
LABELS = {
    'author': ('Maker', '作者 Artist'),
    'date': ('Date', '年代 Date'),
    'material': ('Materials and techniques', 'materialsAndTechniques', '材质 Medium'),
}
HEADINGS = {'author':'馆方编目的作者／制作者', 'date':'馆方编目标注的年代', 'material':'馆方编目的材质与技法'}
PATTERNS = {
    'author': [r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)的?)?(?:作者|制作者)是谁',
               r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)(?:是)?)?谁(?:制作|创作|做)的',
               r'who (?:made|created) (?:it|this(?: work| object)?)', r'who is (?:the|its) (?:artist|maker)'],
    'date': [r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)(?:是)?)?(?:什么|哪个)年代(?:的)?',
             r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)(?:是)?)?(?:哪一年|什么时候)(?:制作|创作|做)(?:的)?',
             r'when was (?:it|this(?: work| object)?) (?:made|created)', r'what is (?:the|its) date'],
    'material': [r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)(?:是)?)?什么材质(?:的)?',
                 r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)的?)?材质是什么',
                 r'(?:(?:它|这件(?:作品|藏品)?|这个(?:作品|藏品)?)(?:是)?)?(?:用)?什么(?:材料|材质)做的',
                 r'what is (?:it|this(?: work| object)?) made (?:of|from)', r'what is (?:the|its) (?:material|medium)'],
}


def parse_field_question(query):
    value = unicodedata.normalize('NFKC', query).strip().rstrip('？?。.!！').strip().casefold()
    value = re.sub(r'^(?:请问|请告诉我)\s*', '', value)
    return next((field for field, patterns in PATTERNS.items()
                 if any(re.fullmatch(p, value) for p in patterns)), None)


def literal_answer(query, source, expected):
    field = parse_field_question(query)
    if not field or not source or not expected:
        return None
    if source.get('status') != 'active' or expected.get('status') != 'active':
        return None
    if (source.get('_id') != expected.get('_id') or not source.get('source_hash')
        or source['source_hash'] != expected.get('source_hash') or source.get('content') != expected.get('content')):
        return None
    content = source.get('content', '')
    entries = []
    for line in content.splitlines():
        label, sep, value = line.partition(':')
        if sep and label.strip() in LABELS[field]:
            entries.append((line, value.strip()))
    # Duplicated/empty/malformed fields are not silently resolved.
    if len(entries) != 1:
        return None
    quote, value = entries[0]
    if not value or len(value) > 700 or len(quote) > 1200:
        return None
    return dict(field=field, text=f'{HEADINGS[field]}：{value}', quote=quote,
                source_id=source['_id'], source_hash=source['source_hash'],
                content_hash=hashlib.sha256(content.encode()).hexdigest(), version=VERSION)
