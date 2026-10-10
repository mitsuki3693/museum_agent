"""Freeze/check a local demo configuration. Never publish the private snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.request import urlopen


HEALTH_FIELDS = (
    'storage', 'retrieval', 'corpus_count', 'corpus_hash', 'prompt_version',
    'answer_policy', 'catalogue_answers', 'rewrite_overlong_answers',
    'verifier_policy', 'photo_prompt_version', 'photo_policy_version',
    'photo_reference_mode', 'photo_verification', 'route_planning',
    'photo_retrieval', 'visual_index_hash',
)
RERANK_FIELDS = ('enabled', 'chinese_recall', 'batching', 'backend',
                 'context_token_budget', 'auto_recover', 'budget_ms')
DATA_FIELDS = ('museum_corpus', 'museum_private_corpus', 'museum_search_fields',
               'museum_visual_manifest', 'museum_route_manifest',
               'museum_floor_demo_manifest', 'museum_operations_demo_manifest')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def version(health: dict) -> dict:
    if health.get('status') != 'ok':
        raise ValueError('Live backend is not healthy')
    # Fail closed on an old/incomplete health contract. Never copy arbitrary keys.
    return {**{key: health[key] for key in HEALTH_FIELDS},
            'text_rerank': {key: health['text_rerank'][key] for key in RERANK_FIELDS}}


def contained(root: Path, path: Path) -> Path:
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Snapshot paths must remain in the project')
    return path


def release_dir(root: Path, name: str) -> Path:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name):
        raise ValueError('Use a short lowercase release name')
    return contained(root, root / '.runtime' / 'releases' / name)


def source_hashes(root: Path) -> dict:
    paths = []
    for directory in ('backend/app', 'web/src'):
        paths.extend(p for p in (root / directory).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
    for name in ('web/package.json', 'web/package-lock.json', 'web/next.config.mjs',
                 'web/tsconfig.json', 'requirements.lock', 'backend/pyproject.toml'):
        if (root / name).is_file():
            paths.append(root / name)
    if not paths:
        raise ValueError('No application source files found')
    return {p.relative_to(root).as_posix(): digest(contained(root, p))
            for p in sorted(paths)}


def capture(root: Path, name: str, health: dict, artifacts: list[Path], model: str) -> Path:
    root = root.resolve()
    current = version(health)
    env = contained(root, root / '.env')
    env_hash = digest(env)
    data = {p.relative_to(root).as_posix(): digest(p)
            for p in [contained(root, p) for p in artifacts]}
    manifest = {'release': name, 'schema_version': 1, 'health': current,
                'model': model, 'source_files': source_hashes(root),
                'data_files': data, 'config_sha256': env_hash,
                'scope': 'local demo snapshot; not a database or model backup'}
    target = release_dir(root, name)
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(env, target / 'config.env')
    if digest(target / 'config.env') != env_hash or digest(env) != env_hash:
        raise ValueError('Configuration changed during capture; snapshot incomplete')
    # Write last: an interrupted snapshot has no complete manifest and cannot pass.
    with (target / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return target


def verify(root: Path, name: str, health: dict) -> list[str]:
    root = root.resolve()
    target = release_dir(root, name)
    manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
    changes = []
    if manifest.get('schema_version') != 1 or manifest.get('release') != name:
        raise ValueError('Invalid release manifest')
    if version(health) != manifest['health']:
        changes.append('live_version')
    if source_hashes(root) != manifest['source_files']:
        changes.append('application_source')
    for filename in ('.env', f'.runtime/releases/{name}/config.env'):
        path = contained(root, root / filename)
        if not path.is_file() or digest(path) != manifest['config_sha256']:
            changes.append('configuration:' + filename)
    for filename, expected in manifest['data_files'].items():
        path = contained(root, root / filename)
        if not path.is_file() or digest(path) != expected:
            changes.append('data:' + filename)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'verify'))
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    from app.museum.config import MuseumSettings, ROOT
    # Only a read-only loopback endpoint; no remote URL or paid provider calls.
    with urlopen('http://127.0.0.1:8000/api/museum/health', timeout=15) as response:
        health = json.load(response)
    if args.action == 'freeze':
        config = MuseumSettings()
        paths = [getattr(config, name) for name in DATA_FIELDS if getattr(config, name)]
        capture(ROOT, args.release, health, paths, config.deepseek_model)
        print(json.dumps({'release': args.release, 'frozen': True, 'private_config': True}))
    else:
        changes = verify(ROOT, args.release, health)
        print(json.dumps({'release': args.release, 'matches': not changes, 'changes': changes}))
        raise SystemExit(1 if changes else 0)


if __name__ == '__main__':
    main()
