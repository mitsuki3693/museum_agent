import asyncio
import time
import sys

import pytest

from app.museum.rerank_service import RerankService


def worker(body):
    script="import sys,json,time\nprint(json.dumps({'ready':True}),flush=True)\nfor line in sys.stdin:\n r=json.loads(line)\n"+body
    return [sys.executable,'-u','-c',script]


@pytest.mark.asyncio
async def test_timeout_recovers_worker_without_restarting_site():
    svc=RerankService('',timeout=.08,auto_recover=True,restart_delay=.01,command=worker(
        " if r['query']=='hang':time.sleep(5)\n"
        " print(json.dumps({'request_id':r['request_id'],'ids':['a']}),flush=True)"))
    try:
        await svc.start();old=svc.process
        before=time.perf_counter()
        ids,trace=await svc.rank('hang',[dict(source_id='a')],[])
        assert ids is None and trace['status']=='timeout' and time.perf_counter()-before<1
        assert old.returncode is not None
        for _ in range(100):
            if svc.state=='ready':break
            await asyncio.sleep(.01)
        ids,trace=await svc.rank('ok',[dict(source_id='a')],[])
        assert ids==['a'] and trace['status']=='applied'
        assert svc.process is not old
        assert svc.failures==0 and svc.restarts==1
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_repeated_timeouts_cool_down_and_stop_cancels_restart():
    svc=RerankService('',timeout=.06,auto_recover=True,restart_delay=.01,cooldown=2,failure_limit=2,
        command=worker(' time.sleep(5)'))
    try:
        await svc.start()
        await svc.rank('hang',[dict(source_id='a')],[])
        for _ in range(100):
            if svc.state=='ready':break
            await asyncio.sleep(.01)
        await svc.rank('hang',[dict(source_id='a')],[])
        assert svc.state=='cooldown' and svc.failures==2
        start=time.perf_counter()
        assert (await svc.rank('next',[dict(source_id='a')],[]))[1]['status']=='cooldown'
        assert time.perf_counter()-start<.1
    finally:await svc.stop()


@pytest.mark.asyncio
async def test_backend_mismatch_never_counts_as_applied():
    svc=RerankService('',backend='int8',command=worker(
        " print(json.dumps({'request_id':r['request_id'],'ids':['a'],'backend':'torch'}),flush=True)"))
    try:
        await svc.start()
        ids,trace=await svc.rank('q',[dict(source_id='a')],[])
        assert ids is None and trace['status']=='error'
    finally:await svc.stop()
    await asyncio.sleep(.02)
    assert svc.process is None and svc.state=='closed' and svc._recovery_task is None


@pytest.mark.asyncio
async def test_stop_during_replacement_startup_leaves_no_worker():
    import sys
    svc=RerankService('',auto_recover=True,restart_delay=.01,command=worker(' time.sleep(5)'),timeout=.04)
    await svc.start()
    # Replacement intentionally never emits ready.
    svc.command=[sys.executable,'-u','-c','import time; time.sleep(30)']
    await svc.rank('hang',[dict(source_id='a')],[])
    for _ in range(100):
        if svc.state=='starting' and svc.process:break
        await asyncio.sleep(.01)
    proc=svc.process
    assert proc
    await svc.stop()
    assert proc.returncode is not None and svc.process is None and svc.state=='closed'


@pytest.mark.asyncio
async def test_windows_job_kills_grandchild_on_timeout(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Windows venv descendant containment')
    import ctypes
    from ctypes import wintypes
    marker=tmp_path/'pid.txt'
    body=(" import subprocess\n"
        " child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f" open({str(marker)!r},'w').write(str(child.pid))\n"
        " time.sleep(30)")
    svc=RerankService('',timeout=.3,command=worker(body))
    try:
        await svc.start()
        task=asyncio.create_task(svc.rank('q',[dict(source_id='a')],[]))
        for _ in range(100):
            if marker.exists():break
            await asyncio.sleep(.002)
        assert marker.exists()
        pid=int(marker.read_text())
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x00100000,False,pid)
        assert handle
        try:
            assert (await task)[1]['status']=='timeout'
            assert kernel.WaitForSingleObject(handle,500)==0
        finally:kernel.CloseHandle(handle)
    finally:await svc.stop()
