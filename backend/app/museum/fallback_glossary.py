"""Small explicit bilingual query glossary, never used as answer evidence."""
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


def expand_query(query):
    matched={key:value for key,value in GLOSSARY.items() if key in query}
    # A lone broad cue (e.g. blue/gilded) can swamp a precise Chinese hit.
    # Require two distinct glossary concepts; aliases for one concept count once.
    enabled=len(set(matched.values()))>=2
    english=list(dict.fromkeys(word for value in matched.values() for word in value.split()))
    return ' '.join([lexical_query(query),*(english if enabled else [])]),dict(
        version=VERSION,matched=matched,applied=enabled,minimum_concepts=2)
