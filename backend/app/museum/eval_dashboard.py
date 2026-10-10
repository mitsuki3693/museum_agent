"""Read-only presentation of evaluation evidence; never merges separate runs."""
from collections import Counter, defaultdict
import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Track = Literal['retrieval', 'generation', 'photo', 'route', 'conservation', 'reliability']
TRACKS = [('retrieval', '文字检索'), ('generation', '讲解与问答'), ('photo', '局部识图'),
          ('route', '路线规划'), ('conservation', '文保问答'), ('reliability', '工程可靠性')]


class EvalMetric(BaseModel):
    group: str = Field(default='全部', max_length=80)
    variant: str = Field(max_length=80)
    label: str = Field(max_length=80)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: Literal['ratio', 'ms', 'seconds', 'count', 'score']
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, gt=0)
    samples: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def valid_counts(self):
        if self.unit == 'ratio' and self.value is not None and not 0 <= self.value <= 1:
            raise ValueError('Ratio must be between zero and one')
        if self.numerator is not None:
            if self.denominator is None or self.numerator > self.denominator:
                raise ValueError('Ratio counts require a valid denominator')
            if self.unit != 'ratio' or self.value is None or not math.isclose(self.value, self.numerator/self.denominator, abs_tol=1e-6):
                raise ValueError('Ratio value and counts disagree')
        return self


class EvalDisplay(BaseModel):
    track: Track
    scope: str = Field(min_length=1, max_length=500)
    decision: Literal['adopt', 'reject', 'pending'] = 'pending'
    metrics: list[EvalMetric] = Field(default_factory=list, max_length=80)


def ratio(group, variant, label, numerator, denominator):
    return dict(group=group, variant=variant, label=label, unit='ratio',
                value=numerator/denominator if denominator else None,
                numerator=numerator if denominator else None, denominator=denominator or None)


def legacy_metrics(run):
    """Only known metric schemas; unknown summaries remain unscored."""
    rows, summary = run.get('results', []), run.get('summary', {})
    metrics = []
    # These two fixed experiments have explicit disjoint cohorts and counts.
    if run['_id'] in ('foreground-crop-v1', 'photo-crop-aggregation-v1'):
        for key, label in [('baseline', '原方案'), ('query_crop', '只裁查询'),
                           ('both_crop', '两侧裁剪'), ('candidate', '裁剪均值')]:
            for group, values in summary.get(key, {}).items():
                n = values['in_corpus']
                cohort = {'stage30': '20 张局部／角度库内图', 'legacy12': '旧 10 张库内图'}.get(group, group)
                for top in (1, 5, 10, 20):
                    metrics.append(ratio(cohort, label, f'Top{top} 命中', values[f'top{top}'], n))
                metrics.append(dict(group=cohort, variant=label, label='全库 MRR', unit='score', value=values['mrr'], samples=n))
        return metrics
    # Paired rank exports can be shards. Do not read duplicated whole-run
    # summaries or combine shards without a separately verified experiment ID.
    groups = defaultdict(list)
    for row in rows:
        if 'before_rank' in row and 'after_rank' in row and row.get('scored', True) is True:
            groups[row.get('group', '本批次／分片')].append(row)
    for group, cases in groups.items():
        for key, label in [('before_rank', '改动前'), ('after_rank', '改动后')]:
            for top in (1, 5):
                hits = sum(isinstance(r[key], (int, float)) and 0 < r[key] <= top for r in cases)
                metrics.append(ratio(group, label, f'Top{top} 命中', hits, len(cases)))
    # Latency of a specific replay, not answer accuracy or a service SLA.
    for row in rows:
        if 'measured_ms' in row and 'case_id' in row and 'variant' in row:
            metrics.append(dict(group=row['case_id'], variant=row['variant'], label='单次问答耗时',
                                unit='ms', value=row['measured_ms'], samples=1))
    return metrics


def present_run(run):
    display = run.get('display')
    if display:
        display = EvalDisplay.model_validate(display).model_dump()
        track, scope, decision, metrics = (display[k] for k in ('track', 'scope', 'decision', 'metrics'))
    else:
        track = {'text': 'retrieval', 'photo': 'photo', 'route': 'route',
                 'conservation': 'conservation', 'reliability': 'reliability'}.get(run.get('kind'), 'unclassified')
        if run['_id'] == 'catalogue-literal-answer-v1-paired':
            track = 'generation'
        scope = ('历史开发批次；按本批次／分片展示，不跨批次合并。'
                 '指标仅代表对应检索或单次执行，不等于游客任务完成率。')
        decision = 'reject' if run.get('summary', {}).get('decision') in (
            'reject_automatic_foreground_crop', 'reject_candidate_aggregation') else 'pending'
        metrics = legacy_metrics(run)
    rows = run.get('results', [])
    grades = defaultdict(list)
    for row in rows:
        if isinstance(row.get('human_grade'), dict):
            grades[(row.get('group', '已评分子集'), row.get('variant', '未标注方案'))].append(row['human_grade'])
    for (group, variant), group_grades in grades.items():
        for label, good, total in [('人工事实正确比例', 'facts_correct', 'facts_total'),
                                    ('人工引用支持比例', 'citations_supported', 'citations_total')]:
            metrics.append(ratio(group, variant, label, sum(g.get(good, 0) for g in group_grades),
                                 sum(g.get(total, 0) for g in group_grades)))
    return {**{k: run.get(k) for k in ('_id', 'created_at', 'status', 'dataset_version', 'dataset_hash',
                                      'corpus_hash', 'model', 'prompt_version', 'embedding_model', 'visual_index_hash')},
            'track': track, 'scope': scope, 'decision': decision, 'metrics': metrics,
            'human_reviewed': run.get('human_reviewed') is True,
            'human_grades_completed': sum(len(g) for g in grades.values()), 'result_count': len(rows),
            'online_ab': False, 'current_deployment_inferred': False}


async def evaluation_dashboard(store, limit=200):
    total = await store.count('eval_runs')
    rows = await store.find('eval_runs', limit=limit if store.mongo else 0)
    rows = sorted(rows, key=lambda r: (r.get('created_at', 0), r['_id']), reverse=True)[:limit]
    runs = [present_run(r) for r in rows]
    counts = Counter(r['track'] for r in runs)
    return dict(version='evaluation-dashboard-v1', total=total, shown=len(runs), truncated=total > len(runs),
                tracks=[dict(id=k, label=v, count=counts[k]) for k, v in TRACKS], runs=runs)
