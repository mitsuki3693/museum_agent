"""Non-overlapping answer phases; includes failed calls, never request text."""
from contextlib import contextmanager
import time
import math

STAGES = ('catalogue', 'rewrite', 'retrieval', 'discovery', 'fact_selection',
          'generation', 'verification', 'prepared_narration')


class AnswerTiming:
    def __init__(self):
        self.events = []

    @contextmanager
    def measure(self, stage):
        if stage not in STAGES:
            raise ValueError('Unknown answer phase')
        started = time.perf_counter()
        event = dict(stage=stage, status='ok')
        try:
            yield
        except BaseException as exc:
            event.update(status='error', error_type=type(exc).__name__)
            raise
        finally:
            event['ms'] = round((time.perf_counter()-started)*1000, 3)
            self.events.append(event)

    async def run(self, stage, awaitable):
        with self.measure(stage):
            return await awaitable

    def snapshot(self):
        return dict(version='answer-phases-v1',
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
