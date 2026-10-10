"""Image availability is separate from catalogue existence or photo identity."""
from pathlib import Path


def image_availability(config, record, manifest, *, public_root=None):
    from .config import ROOT
    sid=record['_id']
    local=record.get('local_image')
    if local and config.museum_private_corpus:
        root=config.museum_private_corpus.parent.resolve()
        path=(root/local).resolve()
        valid=path.is_relative_to(root) and path.suffix.lower() in {'.jpg','.png','.webp'} and path.is_file()
        return dict(image_url=f'/api/museum/objects/{sid}/image' if valid else None,
                    image_status='available' if valid else 'missing',image_reason=None if valid else 'local_file_missing')
    entry=manifest.get(sid)
    if not entry:
        return dict(image_url=None,image_status='missing',image_reason='not_in_image_manifest')
    if entry.get('is_public_domain') is not True:
        return dict(image_url=None,image_status='missing',image_reason='not_public_domain' if entry.get('is_public_domain') is False else 'rights_unverified')
    root=Path(public_root) if public_root else ROOT/'web/public/collection'
    path=(root/f'{sid}.jpg').resolve()
    valid=bool(entry.get('image_id')) and path.is_relative_to(root.resolve()) and path.is_file()
    return dict(image_url=f'/collection/{sid}.jpg' if valid else None,
                image_status='available' if valid else 'missing',image_reason=None if valid else 'local_file_missing')
