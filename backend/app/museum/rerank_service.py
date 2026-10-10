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
from .worker_job import WorkerJob


class RerankService:
    def __init__(self, model_path, *, timeout=8, startup_timeout=60, command=None, sort_by_length=False,evidence_controls=False,
                 auto_recover=False,restart_delay=1,cooldown=30,failure_limit=3,
                 backend='torch',onnx_path=None,context_budget=256):
        model_path=Path(model_path)
        if not model_path.is_absolute():model_path=ROOT/model_path
        self.command=command or [sys.executable,'-m','app.museum.rerank_worker',str(model_path)]
        self.batching='length' if sort_by_length else 'original'
        if sort_by_length and command is None:self.command.append('--sort-by-length')
        self.evidence_controls=evidence_controls
        if evidence_controls and command is None:self.command.append('--evidence-controls')
        if backend not in ('torch','onnx','int8') or context_budget not in (160,192,256):raise ValueError('Invalid backend/budget')
        self.backend=backend;self.context_budget=context_budget
        if command is None:
            if context_budget!=256:self.command+=['--context-budget',str(context_budget)]
            if backend!='torch':
                if onnx_path is None:raise ValueError('ONNX export path required')
                onnx_path=Path(onnx_path)
                if not onnx_path.is_absolute():onnx_path=ROOT/onnx_path
                self.command+=['--backend',backend,'--onnx-path',str(onnx_path)]
        self.timeout=timeout;self.startup_timeout=startup_timeout
        self.process=None;self.state='not_started';self.busy=False
        self.startup_profile={};self.completed_requests=0
        self.auto_recover=auto_recover;self.restart_delay=restart_delay;self.cooldown=cooldown;self.failure_limit=failure_limit
        if restart_delay<0 or cooldown<=0 or failure_limit<1:raise ValueError('Invalid recovery bounds')
        self.failures=0;self.restarts=0;self._recovery_task=None;self._closed=False
        self._start_lock=asyncio.Lock()
        self._job=None

    async def start(self):
        self._closed=False
        async with self._start_lock:
            if self.state=='ready':return
            await self._spawn()
        if self.state=='unavailable':self._schedule_recovery()

    async def _spawn(self):
        if self._closed:return
        self.state='starting'
        try:
            self.process=await asyncio.create_subprocess_exec(*self.command,cwd=str(ROOT/'backend'),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                limit=2**20,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self._job=WorkerJob(self.process.pid)
            msg=json.loads(await asyncio.wait_for(self.process.stdout.readline(),self.startup_timeout))
            if msg.get('ready') is not True:raise ValueError('Worker not ready')
            self.startup_profile=msg.get('startup_profile',{});self.completed_requests=0
            self.state='ready'
        except asyncio.CancelledError:
            await self._terminate('cancelled');raise
        except Exception:
            await self._terminate('unavailable')

    async def stop(self, state='closed'):
        self._closed=True
        task,self._recovery_task=self._recovery_task,None
        if task and not task.done():
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        async with self._start_lock:await self._terminate(state)

    async def _terminate(self,state):
        self.state=state
        proc,self.process=self.process,None
        job,self._job=self._job,None
        if job:job.close()
        if proc:
            if proc.returncode is None:
                try:proc.kill()
                except ProcessLookupError:pass
            await proc.wait()

    def _schedule_recovery(self):
        if not self.auto_recover or self._closed:return
        self.failures+=1
        self.state='cooldown' if self.failures>=self.failure_limit else 'recovering'
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task=asyncio.create_task(self._recover())

    async def _recover(self):
        # Only one replacement worker; requests fall back while loading/cooling.
        # A ready worker is a half-open probe. Only successful ranking resets
        # consecutive failures, so repeated slow requests cannot restart-storm.
        while not self._closed:
            await asyncio.sleep(self.cooldown if self.failures>=self.failure_limit else self.restart_delay)
            async with self._start_lock:
                if self._closed or self.state=='ready':return
                self.restarts+=1
                await self._spawn()
            if self.state=='ready':return
            self.failures+=1
            self.state='cooldown' if self.failures>=self.failure_limit else 'recovering'

    async def rank(self, query, candidates, sources):
        started=time.perf_counter()
        trace=dict(status=self.state,budget_ms=round(self.timeout*1000),candidate_ids=[c['source_id'] for c in candidates],batching=self.batching,
                   recovery_enabled=self.auto_recover,restarts=self.restarts,consecutive_failures=self.failures,backend=self.backend)
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
            if reply.get('backend','torch')!=self.backend or reply.get('context_token_budget',256)!=self.context_budget:
                raise ValueError('Worker backend/budget mismatch')
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
                         model_revision=reply.get('model_revision'),context_token_budget=reply.get('context_token_budget',256),
                         ranking_policy=reply.get('ranking_policy','raw-score-v1'),evidence_ties=reply.get('evidence_ties',[]),
                         evidence_controls=reply.get('evidence_controls'),material_status=reply.get('material_status'),
                         profile=reply.get('profile',{}),startup_profile=self.startup_profile,
                         first_request=self.completed_requests==0)
            self.completed_requests+=1
            self.failures=0
            return reply['ids'],trace
        except asyncio.TimeoutError:
            trace['status']='timeout';await self._terminate('disabled_after_timeout');self._schedule_recovery()
            return None,trace
        except asyncio.CancelledError:
            await self._terminate('disabled_after_cancel');self._schedule_recovery();raise
        except Exception as exc:
            trace.update(status='error',error_type=type(exc).__name__)
            await self._terminate('disabled_after_error');self._schedule_recovery();return None,trace
        finally:
            self.busy=False;trace['ms']=round((time.perf_counter()-started)*1000)
            if trace.get('profile'):
                trace['profile']['exchange_overhead_ms']=max(0,trace['ms']-trace['profile'].get('worker_ms',0))
