"""Explicit catalogue-field lookup; prose mentions are never field evidence.

Dates mean equality of the displayed date, including approximation/ranges.
Names require complete token equality (surname/given-name order may differ).
No inferred dates, partial-name matching or generation is used here.
"""
import re
import unicodedata
from collections import defaultdict

VERSION = 'catalogue-author-date-v1'
LABELS = {'author': {'Maker', '作者 Artist'}, 'date': {'Date', '年代 Date'}}
_NEGATIVE = re.compile(r'不是|不要|不找|除了|排除|别找|并非|\b(?:not|except|without|and|or)\b', re.I)
_UNCERTAIN_AUTHOR = re.compile(r'\b(?:unknown|unidentified|attributed|after|possibly|probably|workshop|circle|school|style|manner)\b', re.I)
_ALIASES = {
    '莫奈':'Claude Monet', '克劳德·莫奈':'Claude Monet',
    '乔瓦尼·博洛尼亚':'Giovanni Bologna', '乔凡尼·博洛尼亚':'Giovanni Bologna',
    '吉安博洛尼亚':'Giovanni Bologna', '梵高':'Vincent van Gogh', '梵·高':'Vincent van Gogh',
    '格兰特·伍德':'Grant Wood', '毕加索':'Pablo Picasso', '修拉':'Georges Seurat',
}


def clean(value):
    return unicodedata.normalize('NFKC', value).strip().translate(str.maketrans({'–':'-', '—':'-', '−':'-'}))


def author_key(value):
    value = clean(value)
    if _UNCERTAIN_AUTHOR.search(value):
        return None
    # AIC's trailing nationality/life-date display is not part of the name.
    # Do not erase arbitrary qualifiers such as "(attributed to)".
    value = re.sub(r'\s*\([^()]*\b\d{4}\s*-\s*\d{4}[^()]*\)\s*$', '', value)
    if re.search(r'[\d();:/&]', value):
        return None
    tokens = re.findall(r'[^\W\d_]+', value.casefold())
    return tuple(sorted(tokens)) if tokens else None


def date_key(value):
    value = clean(value).casefold()
    match = re.fullmatch(r'(?P<approx>(?:ca\.?|c\.?|circa|约)\s*)?(?P<start>\d{4})(?:\s*-\s*(?P<end>\d{4}))?年?', value)
    if not match:
        return None
    start, end = int(match['start']), int(match['end']) if match['end'] else None
    if not start or (end is not None and end < start):
        return None
    return (bool(match['approx']), start, end)


class CatalogueMetadata:
    def __init__(self, records):
        self.by_key = {kind:defaultdict(dict) for kind in LABELS}
        for sid, record in records.items():
            for line in record['content'].splitlines():
                label, sep, value = line.partition(':')
                if not sep or not value.strip():
                    continue
                for kind, labels in LABELS.items():
                    if label.strip() not in labels:
                        continue
                    # Multiple makers are delimited explicitly in the importer.
                    values = value.split(';') if kind == 'author' else [value]
                    for part in values:
                        key = (author_key if kind == 'author' else date_key)(part)
                        if key is not None:
                            self.by_key[kind][key][sid] = line

    def parse(self, query):
        query = clean(query).rstrip('。？！.!?').strip()
        if _NEGATIVE.search(query):
            return None
        date = re.fullmatch(r'(?:find (?:works|objects) dated\s+|(?:找|寻找)(?:年代标注为|年代为|制作年代为)?)(.+?)(?:的(?:作品|藏品))?', query, re.I)
        if date:
            value = date.group(1)
            key = date_key(value)
            if key is not None:
                return dict(kind='date', value=value, key=key, match_mode='displayed_date_equality')
        patterns = [r'find (?:works|objects) by\s+(.+)',
                    r'(?:找|寻找)(?:作者是|作者为)(.+?)的(?:作品|藏品)',
                    r'(?:找|寻找)(.+?)(?:创作|制作)的(?:作品|藏品)']
        for pattern in patterns:
            match = re.fullmatch(pattern, query, re.I)
            if match:
                value = match.group(1)
                # Institutional aliases and class/role constraints are outside
                # this narrow complete-person-name route.
                if re.search(r'\b(?:factory|workshop|studio|company)\b|工厂|工作室|公司', value, re.I):
                    return None
                key = author_key(_ALIASES.get(value, value))
                if key is not None:
                    return dict(kind='author', value=value, key=key, match_mode='complete_author_name')
        # "找蓝色的作品" must not become an author query. The shorthand is
        # allowed only for a known full catalogue name or an explicit alias.
        short = re.fullmatch(r'(?:找|寻找)(.+?)的(?:作品|藏品)', query)
        if short:
            value = short.group(1)
            key = author_key(_ALIASES.get(value, value))
            if key is not None and (value in _ALIASES or key in self.by_key['author']):
                return dict(kind='author', value=value, key=key, match_mode='complete_author_name')
        return None

    def lookup(self, request):
        evidence = self.by_key[request['kind']].get(request['key'], {})
        return dict(evidence)
