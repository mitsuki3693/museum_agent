"""Offline comparison ablation: hide catalogue identity hints, preserve pixels.

Not wired to the visitor service. Blinding removes titles, accession metadata
and meaningful source IDs from the comparison payload, not from retrieval or
subsequent source validation. It cannot guarantee the model won't recognize art.
"""
import copy
import json

VERSION = 'blind-photo-comparison-v1'
REFERENCE_PREFIX = '馆藏参考图，候选 id：'


def blind_messages(messages):
    """Accept only the established single-reference comparison message format.

    Returns a fresh payload and an alias-to-source map. Unknown/free-form blocks
    fail closed instead of accidentally forwarding an unredacted identity hint.
    """
    if len(messages) != 2 or messages[0].get('role') != 'system' or messages[1].get('role') != 'user':
        raise ValueError('Expected comparison system and user messages')
    content = messages[1].get('content')
    if not isinstance(content, list) or len(content) < 5 or (len(content)-3) % 2:
        raise ValueError('Unexpected image comparison layout')
    if content[0] != {'type':'text','text':'待识别的游客照片：'} or content[1].get('type') != 'image_url':
        raise ValueError('Missing query image marker')
    if set(content[2]) != {'type','text'} or content[2]['type'] != 'text':
        raise ValueError('Missing candidate metadata')
    try:
        records = json.loads(content[2]['text'])
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid candidate metadata') from exc
    if not isinstance(records,list) or not 1 <= len(records) <= 8:
        raise ValueError('Bounded candidate records required')
    ids = []
    for record in records:
        if not isinstance(record,dict) or set(record) != {'id','title','accession_number'}:
            raise ValueError('Unknown metadata schema')
        if not isinstance(record['id'],str) or not record['id'] or record['id'] in ids:
            raise ValueError('Candidate IDs must be unique strings')
        ids.append(record['id'])
    aliases = {sid:f'C{i:02}' for i,sid in enumerate(ids,1)}
    output = copy.deepcopy(messages)
    new_content = output[1]['content']
    new_content[2]['text'] = json.dumps([{'id':aliases[sid]} for sid in ids], ensure_ascii=False)
    seen = set()
    for i in range(3,len(content),2):
        label, image = content[i:i+2]
        if set(label) != {'type','text'} or label['type'] != 'text' or not label['text'].startswith(REFERENCE_PREFIX):
            raise ValueError('Unknown reference label format')
        sid = label['text'][len(REFERENCE_PREFIX):]
        if sid not in aliases or sid in seen or image.get('type') != 'image_url':
            raise ValueError('Unknown or duplicate reference identity')
        seen.add(sid)
        new_content[i]['text'] = REFERENCE_PREFIX+aliases[sid]
    if not 1 <= len(seen) <= 5:
        raise ValueError('One to five single references required')
    return output, {alias:sid for sid,alias in aliases.items()}


def restore_ids(response, alias_to_source):
    """Only deterministic ID translation; all normal policy validation still runs."""
    if not isinstance(response,dict) or not isinstance(response.get('comparisons'),list):
        raise ValueError('Missing comparison response')
    output = copy.deepcopy(response)
    seen = set()
    for row in output['comparisons']:
        alias = row.get('candidate_id') if isinstance(row,dict) else None
        if not isinstance(alias,str) or alias not in alias_to_source or alias in seen:
            raise ValueError('Unknown or duplicate comparison alias')
        seen.add(alias)
        row['candidate_id'] = alias_to_source[alias]
    return output
