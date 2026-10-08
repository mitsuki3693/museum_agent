"""Single-flight local worker. Deadlines kill computation, not just the wait."""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import ROOT
from .evidence_ties import POLICY
from .rerank_evidence_controls import VERSION as CONTROLS_VERSION


class RerankService:
    def __init__(self, model_path, *, timeout=8, startup_timeout=60, command=None, sort_by_length=False,evidence_controls=False):
        model_path=Path(model_path)
        if not model_path.is_absolute():model_path=ROOT/model_path
        self.command=command or [sys.executable,'-m','app.museum.rerank_worker',str(model_path)]
        self.batching='length' if sort_by_length else 'original'
        if sort_by_length and command is None:self.command.append('--sort-by-length')
        self.evidence_controls=evidence_controls
        if evidence_controls and command is None:self.command.append('--evidence-controls')
        self.timeout=timeout;self.startup_timeout=startup_timeout
        self.process=None;self.state='not_started';self.busy=False

    async def start(self):
        self.state='starting'
        try:
            self.process=await asyncio.create_subprocess_exec(*self.command,cwd=str(ROOT/'backend'),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                limit=2**20,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            msg=json.loads(await asyncio.wait_for(self.process.stdout.readline(),self.startup_timeout))
            if msg.get('ready') is not True:raise ValueError('Worker not ready')
            self.state='ready'
        except asyncio.CancelledError:
            await self.stop('cancelled');raise
        except Exception:
            await self.stop('unavailable')

    async def stop(self, state='closed'):
        self.state=state
        proc,self.process=self.process,None
        if proc:
            if proc.returncode is None:
                try:proc.kill()
                except ProcessLookupError:pass
            await proc.wait()

    async def rank(self, query, candidates, sources):
        started=time.perf_counter()
        trace=dict(status=self.state,budget_ms=round(self.timeout*1000),candidate_ids=[c['source_id'] for c in candidates],batching=self.batching)
        if self.state!='ready' or not self.process:return None,trace
        if self.busy:
            trace.update(status='busy',ms=0);return None,trace
        self.busy=True
        request_id=uuid.uuid4().hex
        async def exchange():
            self.process.stdin.write((json.dumps(dict(request_id=request_id,query=query,candidates=candidates,sources=sources))+'\n').encode())
            await self.process.stdin.drain()
            reply=json.loads(await self.process.stdout.readline())
            if reply.get('request_id')!=request_id or reply.get('error'):raise ValueError('Invalid worker reply')
            if reply.get('batching','original')!=self.batching:raise ValueError('Worker batching mismatch')
            if self.batching=='length' and reply.get('ranking_policy')!=POLICY:
                raise ValueError('Worker ranking policy mismatch')
            if self.evidence_controls and (reply.get('evidence_controls')!=CONTROLS_VERSION or reply.get('ranking_policy')!=POLICY):
                raise ValueError('Worker evidence controls mismatch')
            ids=reply['ids'];expected=trace['candidate_ids']
            if len(ids)!=len(expected) or len(set(ids))!=len(ids) or set(ids)!=set(expected):
                raise ValueError('Changed candidate identity')
            return reply
        try:
            reply=await asyncio.wait_for(exchange(),self.timeout)
            trace.update(status='applied',ordered_ids=reply['ids'],evidence=reply.get('evidence',[]),
                         model_revision=reply.get('model_revision'),context_token_budget=256,
                         ranking_policy=reply.get('ranking_policy','raw-score-v1'),evidence_ties=reply.get('evidence_ties',[]),
                         evidence_controls=reply.get('evidence_controls'),material_status=reply.get('material_status'))
            return reply['ids'],trace
        except asyncio.TimeoutError:
            trace['status']='timeout';await self.stop('disabled_after_timeout')
            return None,trace
        except asyncio.CancelledError:
            await self.stop('disabled_after_cancel');raise
        except Exception as exc:
            trace.update(status='error',error_type=type(exc).__name__)
            await self.stop('disabled_after_error');return None,trace
        finally:
            self.busy=False;trace['ms']=round((time.perf_counter()-started)*1000)
