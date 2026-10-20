"""Non-overlapping answer phases; includes failed calls, never request text."""
from contextlib import contextmanager
import time
import math
import asyncio
import httpx


def provider_failure(exc):
    """Allowlisted cause metadata; never store provider bodies or messages."""
    causes = []
    current = exc
    while current and id(current) not in {id(x) for x in causes}:
        causes.append(current)
        current = current.__cause__
    for error in causes:
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            return dict(error_kind='http_status', http_status=status,
                        retryable=status in {408, 429, 502, 503, 504})
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            return dict(error_kind='timeout', retryable=True)
        if isinstance(error, httpx.NetworkError):
            return dict(error_kind='network', retryable=True)
    return dict(error_kind='schema' if isinstance(exc, (ValueError, TypeError)) else 'unknown', retryable=False)

STAGES = ('catalogue', 'rewrite', 'retrieval', 'discovery', 'fact_selection',
          'generation', 'verification', 'prepared_narration')


class AnswerTiming:
    def __init__(self):
        self.events = []
        self.retry_used = False
        self.retry_delay_ms = 0

    @contextmanager
    def measure(self, stage):
        if stage not in STAGES:
            raise ValueError('Unknown answer phase')
        started = time.perf_counter()
        event = dict(stage=stage, status='ok')
        try:
            yield
        except BaseException as exc:
            event.update(status='error', error_type=type(exc).__name__, **provider_failure(exc))
            raise
        finally:
            event['ms'] = round((time.perf_counter()-started)*1000, 3)
            self.events.append(event)

    async def run(self, stage, awaitable):
        with self.measure(stage):
            return await awaitable

    async def run_with_retry(self, stage, make_call, deadline):
        """At most one transient retry per answer, under its original deadline."""
        for attempt in range(2):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError('Model stage budget exhausted')
            try:
                return await self.run(stage, asyncio.wait_for(make_call(), timeout=min(25, remaining)))
            except Exception as exc:
                can_retry = (not self.retry_used and attempt == 0 and provider_failure(exc)['retryable']
                             and deadline - time.perf_counter() > 5)
                self.events[-1].update(retry_scheduled=can_retry)
                if not can_retry:
                    raise
                self.retry_used = True
                delay = time.perf_counter()
                await asyncio.sleep(.25)
                self.retry_delay_ms += round((time.perf_counter()-delay)*1000, 3)

    def snapshot(self):
        return dict(version='answer-phases-v1', retry_used=self.retry_used, retry_delay_ms=self.retry_delay_ms,
            totals_ms={s:round(sum(e['ms'] for e in self.events if e['stage']==s),3) for s in STAGES},
            calls={s:sum(e['stage']==s for e in self.events) for s in STAGES},
            events=[dict(e) for e in self.events])


def public_timing(value):
    """Export numeric allowlisted aggregates, never arbitrary trace fields."""
    if not isinstance(value, dict) or value.get('version') != 'answer-phases-v1':
        return None
    result = dict(version='answer-phases-v1')
    for group in ('totals_ms', 'calls'):
        result[group] = {stage:number for stage,number in value.get(group,{}).items()
            if stage in STAGES and type(number) in (int,float) and math.isfinite(number) and number>=0}
    return result
