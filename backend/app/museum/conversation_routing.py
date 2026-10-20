"""Deterministic entity boundaries before query rewriting.

Catalogue matches identify records, never an uploaded photo. Unknown names still
use existing retrieval. Comparison/negation take precedence over entity switching.
"""
from dataclasses import dataclass, field
import re
import unicodedata

VERSION = 'conversation-routing-v1'
COMPARISON = re.compile(r'比较|对比|相比|区别|有什么不同|有何不同|哪里不同|差别|相同|共同点|\b(?:compare|comparison|versus|vs|difference|differ)\b', re.I)
NEGATION = re.compile(r'不是|并非|不要|不找|除了|排除|别找|\b(?:not|except|exclude)\b', re.I)
FOLLOWUP = re.compile(r'它|他|她|这件|这个|这幅|那个|刚才|继续|再讲|详细|深入|儿童版|孩子|小朋友|\b(?:it|this|that|he|she|more|detail|children)\b', re.I)
FIND = re.compile(r'(?:^|[，。！？,;]\s*)(?:请|请你)?(?:帮我找|我想找|我要找|找|看看|看一下|我要看|我想看|我要问的是|改问|切换|换一件|换一幅|换个作品|换一个作品|换件作品|我刚才说错了)|\b(?:find|show me|switch to|look for)\b', re.I)
STYLE = re.compile(r'换(?:成|个|一种|一个).{0,12}(?:说法|讲法|孩子|小朋友|儿童)|\b(?:change|switch).{0,20}(?:style|children|brief)\b', re.I)


def normalize(text):
    return unicodedata.normalize('NFKC', text).translate(
        str.maketrans({'–': '-', '—': '-', '−': '-'})).strip()


@dataclass
class Route:
    kind: str
    query: str
    candidate_ids: list[str] = field(default_factory=list)
    reset: bool = False
    rewrite: bool = False
    continuation_query: str | None = None
    unknown_identifiers: list[str] = field(default_factory=list)

    def trace(self):
        return dict(version=VERSION, kind=self.kind, query=self.query,
                    candidate_ids=self.candidate_ids, reset=self.reset, rewrite=self.rewrite,
                    continuation_query=self.continuation_query, unknown_identifiers=self.unknown_identifiers)


class ConversationRouter:
    def __init__(self, records, accessions, search_fields):
        # Match only complete known keys, with boundaries to avoid prefix hits.
        self.numbers = [
            (re.compile(r'(?<![A-Za-z0-9])' + r'[\s.\-:/,()&]*'.join(map(re.escape, key))
                        + r'(?![A-Za-z0-9]|\s*[.\-:/]\s*[A-Za-z0-9])', re.I), ids)
            for key, ids in accessions.items()
        ]
        self.names = {}
        for sid, record in records.items():
            names = [record['title']]
            for key in ('title_zh', 'aliases_zh'):
                names.extend(search_fields.get(sid, {}).get(key, '').split())
            for name in names:
                name = normalize(name).casefold()
                if len(name) >= 2:
                    self.names.setdefault(name, []).append(sid)

    def route(self, query, selected=None):
        query = normalize(query)
        # An explicit unknown identifier must not inherit a prior object's
        # facts. This recognises the common accession grammar, without fuzzing.
        number_tokens = re.findall(
            r'(?<![A-Za-z0-9])[A-Z]{1,4}[.\s-]*\d{1,8}(?:[A-Z])?'
            r'(?:\s*(?:to|&)\s*[A-Z])?[\s./:-]+\d{2,4}'
            r'(?:[.\-/][A-Za-z0-9]+)?(?![A-Za-z0-9])', query, re.I)
        unknown = [token for token in number_tokens
                   if not any(pattern.fullmatch(token) for pattern, _ in self.numbers)]
        numbers = list(dict.fromkeys(sid for pattern, ids in self.numbers
                                     if pattern.search(query) for sid in ids))
        q = query.casefold().strip(' ?？!！。\'"“”《》')
        positive = re.split(r'我要看|我想看|我要问的是|改问|切换到|换成', q)[-1].strip()
        positive_switch = positive != q
        # Bare names or a name-only search can enumerate homonyms. Descriptive
        # queries retain hybrid retrieval rather than matching generic nouns.
        target = re.sub(r'^(?:(?:请|请你)?(?:帮我找|我想找|我要找|找|看看|看一下|find|show me|look for)\s*(?:一下)?\s*(?:一[件把张个幅尊]|那[件把张个幅尊])?\s*)', '', positive, flags=re.I)
        target = target.strip(' ?？!！。\'"“”《》')
        names = list(self.names.get(target, []))
        matched_name = target if names else None
        # Long distinctive titles are allowed in self-contained requests, but
        # not incidental nouns in a selected object's question.
        title_hits = []
        comparison = bool(COMPARISON.search(query))
        reduced = query
        for pattern, _ in self.numbers:
            reduced = pattern.sub('它', reduced)
        if not names and (comparison or not selected or positive_switch or not FOLLOWUP.search(query)):
            for name, ids in self.names.items():
                distinctive = len(name.split()) >= 3 or len(name) >= 5 and bool(re.search('[\u4e00-\u9fff]', name))
                pattern = (r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])'
                           if name.isascii() else re.escape(name))
                if distinctive and re.search(pattern, positive):
                    title_hits.append((len(name), name, ids))
            if title_hits:
                _, matched_name, names = max(title_hits, key=lambda hit: hit[0])
                if comparison:
                    # Each explicitly named target contributes evidence. Keep
                    # longer names over contained aliases, not over other works.
                    names = list(dict.fromkeys(
                        sid for length, name, ids in title_hits
                        if not any(length < other_length and name in other_name
                                   for other_length, other_name, _ in title_hits)
                        for sid in ids))
        if matched_name:
            reduced = re.sub(re.escape(matched_name), '它', reduced, flags=re.I)
        if comparison:
            return Route('comparison', query, list(dict.fromkeys(numbers + names)), unknown_identifiers=unknown)
        if NEGATION.search(positive):
            return Route('followup' if selected else 'question', query,
                         rewrite=bool(selected or re.match(r'不是[，,]', query)))
        from .catalogue_answers import parse_field_question
        reduced = re.sub(r'(?:馆藏|藏品)?编号(?:为|是)?\s*', '', reduced)
        reduced = re.sub(r'\s+', '', reduced) if re.search('[\u4e00-\u9fff]', reduced) else reduced
        continuation = reduced if parse_field_question(reduced) else None
        if unknown:
            return Route('unknown_accession', query, reset=True, unknown_identifiers=unknown)
        if numbers:
            return Route('accession', query, numbers, reset=True, continuation_query=continuation)
        if names:
            if selected and names == [selected] and q not in self.names and not FIND.search(query):
                return Route('followup', query)
            return Route('title', query, names, reset=True, continuation_query=continuation)
        if positive_switch and not re.match(r'^(?:给|适合|简明|深入|儿童|成人|\d+岁|孩子|小朋友)', positive):
            return Route('search', query, reset=True)
        if STYLE.search(query):
            return Route('followup', query, rewrite=bool(selected))
        if FIND.search(query):
            empty_switch = bool(re.fullmatch(r'(?:请|帮我)?(?:换一件|换一个|换一幅|换件|换个|切换作品)[吧呢。!！?？]*', query))
            return Route('clarify' if empty_switch else 'search', query, reset=True)
        return Route('followup' if selected else 'question', query,
                     rewrite=bool(selected or FOLLOWUP.search(query)))
