"""Restore into a NEW isolated database only, verify full content before any cutover."""
import argparse
import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from app.museum.config import MuseumSettings
from app.museum.backup import database_manifest


async def run(args):
    if Path(args.report).exists():
        raise ValueError('Choose a new restore report path')
    if not re.fullmatch(r'museum_restore_[a-zA-Z0-9_]{1,60}',args.target):
        raise ValueError('Restore target must be a new museum_restore_* database')
    settings=MuseumSettings()
    archive=Path(args.archive);manifest=json.loads(Path(args.manifest).read_text())
    if manifest['status']!='completed' or hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['archive_sha256']:
        raise ValueError('Archive verification failed')
    client=AsyncIOMotorClient(settings.mongodb_uri,tz_aware=True,serverSelectionTimeoutMS=5000)
    try:
        if args.target in await client.list_database_names():
            raise ValueError('Target database already exists; never overwrite it')
        config=archive.with_name('restore-'+uuid.uuid4().hex+'.config.yml')
        config.write_text('uri: '+json.dumps(settings.mongodb_uri)+'\n',encoding='utf-8')
        try:
            proc=await asyncio.create_subprocess_exec(args.tool,'--config='+str(config),'--archive='+str(archive),'--gzip',
                '--nsFrom='+manifest['database']+'.*','--nsTo='+args.target+'.*','--stopOnError',
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:
                code=await asyncio.wait_for(proc.wait(),180)
            except BaseException:
                if proc.returncode is None:proc.kill()
                await proc.wait()
                raise
            if code:raise RuntimeError('mongorestore failed')
        finally:config.unlink(missing_ok=True)
        actual=await database_manifest(client[args.target])
        report={'target':args.target,'matched':actual==manifest['collections'],'actual':actual,'expected':manifest['collections']}
        Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
        if not report['matched']:raise ValueError('Restored content differs; do not activate it')
        print('Restore verified:',args.target)
    finally:client.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--manifest',required=True)
    p.add_argument('--target',required=True);p.add_argument('--tool',required=True);p.add_argument('--report',required=True)
    asyncio.run(run(p.parse_args()))
