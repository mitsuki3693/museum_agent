"""Small offline regression for a named Chinese query; no generation API."""
import asyncio
import argparse
import json
from pathlib import Path
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


async def main():
    import torch
    torch.set_num_threads(4)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fields',type=Path)
    args=parser.parse_args()
    config = MuseumSettings(deepseek_api_key='', museum_text_rerank=False,
                            museum_search_fields=args.fields,museum_search_allow_drafts=bool(args.fields))
    index = MuseumIndex(config, MemoryStore())
    await index.start()
    query = '找参孙击杀非利士人的雕塑'
    rows,trace=await index.search_for_answer(query)
    ids = [s['_id'] for s in rows]
    print(json.dumps({'query': query, 'ids': ids,'route':trace['status']}, ensure_ascii=False))
    assert 'va-o14761-samson' in ids, 'Named in-corpus work missing from fallback Top5'


if __name__ == '__main__':
    asyncio.run(main())
