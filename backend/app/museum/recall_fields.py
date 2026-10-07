"""Offline, source-anchored retrieval aliases. Never authoritative answer text.

These glossary additions stay draft, even when their anchors validate. Only
explicit title/artist/material fields qualify; incidental prose is not credit.
"""
import copy
import re
from app.retrieval.bm25 import tokenize

VERSION='chinese-recall-fields-v2'
TITLES={
    'Water Lilies':'睡莲', 'American Gothic':'美国哥特式', 'Nighthawks':'夜游者 夜鹰',
    'The Bedroom':'卧室 阿尔勒的卧室', 'Paris Street; Rainy Day':'巴黎街道 雨天',
    'Portrait of Pablo Picasso':'毕加索肖像',
    'Acrobats at the Cirque Fernando (Francisca and Angelina Wartenberg)':'费尔南多马戏团的杂技演员',
    'Cliff Walk at Pourville':'普尔维尔的悬崖漫步', 'Self-Portrait':'自画像',
    'A Sunday on La Grande Jatte — 1884':'大碗岛的星期天下午',
    "The Bay of Marseille, Seen from L'Estaque":'从埃斯塔克看马赛湾', 'Nightlife':'夜生活',
}
ARTISTS={'Claude Monet':'克劳德 莫奈', 'Vincent van Gogh':'文森特 梵高 梵·高',
         'Georges Seurat':'乔治 修拉', 'Paul Cézanne':'保罗 塞尚',
         'Edward Hopper':'爱德华 霍珀', 'Grant Wood':'格兰特 伍德',
         'Pablo Picasso':'巴勃罗 毕加索', 'Archibald Motley':'阿奇博尔德 莫特利',
         'Pierre-Auguste Renoir':'皮埃尔 奥古斯特 雷诺阿',
         'Gustave Caillebotte':'古斯塔夫 卡耶博特', 'Juan Gris':'胡安 格里斯'}


def lexical_query(query):
    if not re.search('[\u4e00-\u9fff]',query):return query
    stopwords={'的','了','吗','呢','啊','我','想','想要','请','帮','帮我','找','找找','看看','看','一个','一件','作品','藏品'}
    return ' '.join(t for t in tokenize(query) if t not in stopwords)


def named_title_ids(query,annotations):
    """Prioritize complete long titles, not categories or inferred entities.

    This retrieves records, not a photo identity. Preserve homonymous works;
    any explicit negation disables the shortcut. Short/common titles remain
    in normal retrieval. No fuzzy spelling or query-specific IDs.
    """
    if re.search(r'不是|并非|不要|不找|除了|排除|别找|类似|相似|相比|比较|对比|区别|not\b|except\b',query,re.I):return []
    return [sid for sid,fields in annotations.items() if any(
        len(title)>=4 and title in query
        for name in ['title_zh','aliases_zh'] for title in fields.get(name,'').split())]


def order_named_ids(named,baseline):
    """Retain baseline order among homonyms, then append other exact names."""
    positions={sid:i for i,sid in enumerate(baseline)}
    return sorted(named,key=lambda sid:positions.get(sid,len(positions)))


def extend_manifest(manifest, records):
    result=copy.deepcopy(manifest)
    entries={e['source_id']:e for e in result['records']}
    if len(entries)!=len(result['records']):
        raise ValueError('Duplicate annotation source')
    for sid,record in records.items():
        entry=entries.setdefault(sid,dict(source_id=sid,source_hash=record['source_hash'],
                                        source_url=record['source_url'],fields={}))
        if entry['source_hash']!=record['source_hash'] or entry['source_url']!=record['source_url']:
            raise ValueError('Stale annotation source')

        def add(name,text,quote,scope='content'):
            if quote not in record[scope]:
                raise ValueError('Glossary anchor absent from source')
            existing=entry['fields'].get(name)
            if existing and (existing['review_status']=='human_reviewed' or existing.get('evidence_scope','content')!=scope):
                return
            entry['fields'][name]=dict(text=((existing['text']+' ') if existing else '')+text,
                evidence_quotes=list(dict.fromkeys((existing['evidence_quotes'] if existing else [])+[quote])),
                evidence_scope=scope, derivation=VERSION+'_controlled_glossary',review_status='draft',reviewer=None)

        if record['title'] in TITLES and 'title_zh' not in entry['fields']:
            add('title_zh',TITLES[record['title']],record['title'],'title')
        artist=record.get('fields',{}).get('artist_display','')
        for english,chinese in ARTISTS.items():
            if artist==english or artist.startswith(english+' (') or artist.startswith(english+'\n'):
                add('subject_zh',chinese,english)
        material=record.get('fields',{}).get('medium_display','')
        if not material:
            material=next((line.split(':',1)[1].strip() for line in record['content'].splitlines()
                           if line.startswith('materialsAndTechniques:')),'')
        match=re.search(r'\b(?:gilding|gilded|gilt)\b',material,re.I)
        if match and not re.search(r'\b(?:without|no|not|removed|lost)\b',material,re.I):
            # Retrieval synonyms, not a claim about a specific gilding process.
            add('material_zh','金饰 镀金 描金',match.group())
    result.update(version=VERSION,human_reviewed=False,records=list(entries.values()))
    return result
