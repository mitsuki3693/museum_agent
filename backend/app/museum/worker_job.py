"""Windows venv launches can have descendants: kill the whole owned job."""
import os


class WorkerJob:
    def __init__(self,pid):
        self.handle=None
        if os.name!='nt':return
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),
                ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),('affinity',c.c_size_t),
                ('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):
            _fields_=[(name,c.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
                ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        k=c.WinDLL('kernel32',use_last_error=True)
        k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
        k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
        self.kernel=k
        job=k.CreateJobObjectW(None,None)
        if not job:raise c.WinError(c.get_last_error())
        process=None
        try:
            limits=Limits();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)):raise c.WinError(c.get_last_error())
            process=k.OpenProcess(0x0101,False,pid) # SET_QUOTA | TERMINATE
            if not process or not k.AssignProcessToJobObject(job,process):raise c.WinError(c.get_last_error())
            self.handle=job
        except BaseException:
            k.CloseHandle(job);raise
        finally:
            if process:k.CloseHandle(process)

    def close(self):
        handle,self.handle=self.handle,None
        if handle:self.kernel.CloseHandle(handle)
