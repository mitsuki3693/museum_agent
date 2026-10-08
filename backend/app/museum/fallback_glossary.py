"""Small explicit bilingual query glossary, never used as answer evidence."""
import re
from .recall_fields import lexical_query

VERSION='fallback-bilingual-glossary-v2'
# Common appearance/material terms plus canonical names, no work IDs or gold.
GLOSSARY={
    '蝴蝶':'butterfly butterflies','鸟':'bird birds','花卉':'flower flowers','花纹':'decoration pattern',
    '红色':'red','绿色':'green','蓝色':'blue','黑色':'black','白色':'white',
    '爱心':'heart','红心':'heart','开光':'panel panels cartouche cartouches',
    '网格':'lattice trellis','竖线':'vertical lines','棱纹':'ribbed ribs','卵形':'ovoid',
    '珐琅':'enamel enamels','镀金':'gilded gilt','石膏':'plaster','大理石':'marble',
    '青铜':'bronze','陶土':'terracotta','木雕':'wood carved','下颚骨':'jawbone',
    '狮子':'lion','人头':'head','底座':'base pedestal','瓶口':'rim mouth',
    '花瓶':'vase','盘子':'dish plate','胸像':'bust','半身像':'bust',
    '安提诺乌斯':'Antinous','哈德良':'Hadrian','阿格里皮娜':'Agrippina',
}

# Bounded vocabulary for the expanded catalogue's object classes and motifs.
# These are query translations, not object labels or authoritative facts. No
# IDs, gold answers, similarity scores or whole test questions belong here.
CATALOGUE_VERSION = 'catalogue-bilingual-glossary-v1'
CATALOGUE_GLOSSARY = {
    '椅子':'chair chairs', '座椅':'chair chairs', '扶手椅':'armchair armchairs',
    '时钟':'clock clocks', '座钟':'clock clocks', '挂钟':'clock clocks', '摆钟':'pendulum clock',
    '瓷砖':'tile tiles', '地砖':'tile tiles', '雕塑':'sculpture',
    '玻璃':'glass', '炻器':'stoneware', '硬木':'hardwood', '黄铜':'brass',
    '红漆':'red lacquer', '漆器':'lacquer', '藤编':'cane',
    '蝙蝠':'bat bats', '云纹':'cloud clouds', '龙纹':'dragon dragons',
    '花叶':'flowers foliage', '卷草':'scrollwork foliage', '蔓草':'arabesque',
    '黄底':'yellow ground', '黄色':'yellow', '橙色':'orange', '紫色':'purple',
    '长方形':'rectangular', '圆形':'circular', '轮子':'wheel wheels',
    '仰头':'upturned', '张嘴':'open mouth', '张着嘴':'open mouth',
    '低着头':'gazing down', '低头':'gazing down',
    '萨福':'Sappho', '基督':'Christ', '韦奇伍德':'Wedgwood',
    '霍斯金斯':'Hoskins', '格兰特':'Grant',
}
_CATALOGUE_PATTERN = re.compile('|'.join(map(re.escape, sorted(CATALOGUE_GLOSSARY, key=len, reverse=True))))
_BROAD_TYPES = {'chair chairs', 'armchair armchairs', 'clock clocks', 'tile tiles', 'sculpture'}
_NEGATION = re.compile(r'不是|并非|不要|不找|除了|排除|别找|没有|不含|不带|而非|\b(?:not|except|without)\b', re.I)


def expand_query(query, *, catalogue=False):
    matched={key:value for key,value in GLOSSARY.items() if key in query}
    # Do not turn an excluded motif/name into a new positive retrieval cue.
    # Negation handling itself remains the existing search/discovery behavior.
    guarded = bool(catalogue and _NEGATION.search(query))
    suppressed = []
    if catalogue and not guarded:
        additions = {m.group():CATALOGUE_GLOSSARY[m.group()] for m in _CATALOGUE_PATTERN.finditer(query)}
        # Once the established query already has multiple translated cues,
        # adding only class words can drown a distinctive motif in RRF votes.
        # Keep class words for cold-start queries and when new specific cues
        # (material, motif, name or posture) accompany them.
        if len(set(matched.values())) >= 2 and additions and all(v in _BROAD_TYPES for v in additions.values()):
            suppressed = list(additions)
            additions = {}
        matched.update(additions)
    # A lone broad cue (e.g. blue/gilded) can swamp a precise Chinese hit.
    # Require two distinct glossary concepts; aliases for one concept count once.
    enabled=len(set(matched.values()))>=2
    english=list(dict.fromkeys(word for value in matched.values() for word in value.split()))
    return ' '.join([lexical_query(query),*(english if enabled else [])]),dict(
        version=CATALOGUE_VERSION if catalogue else VERSION,matched=matched,
        applied=enabled,minimum_concepts=2,**({'catalogue_guard':'negation' if guarded else None,
            'suppressed_broad_only':suppressed} if catalogue else {}))
