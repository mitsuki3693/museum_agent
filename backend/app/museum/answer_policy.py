"""Explicit, reversible generation experiments. Verification stays unchanged."""

VERSIONS = {
    'legacy': 'museum-grounded-v5-photo-context',
    'focus': 'museum-grounded-v6-question-focus',
    'repair': 'museum-grounded-v7-focused-repair',
    'geography': 'museum-grounded-v8-focused-repair',
    'facts': 'museum-grounded-v12-original-question',
}

FOCUS = (
    '先回答 question 当前所问的属性或前提，第一条就给直接答复，不重复历史讲解来代替回答。'
    '用户的假设不是事实；若原文明确支持不同前提，先澄清“应以馆方记录为准：……”再陈述原文明确记录的事实。'
    '区分装饰风格、题材与制作地点；不可将中国风等风格当成产地。'
    '若只能证实某个地点，直接说记录明确在哪里制作，不拼接“不是X却是X”的否定句；来源未提及不等于已证明不存在。'
    '产地等关系优先引用包含对象和制作关系的完整句子，不仅截取孤立的地点标签。'
    '无可靠中文名称的工厂、人名保留原文，不临时创造译名。'
)
GEOGRAPHY = '地理范围也属于事实：该条 quote 只有城市时，不得凭常识补上国家、省份或地区名。'
REPAIR = (
    'rejected_draft 是被拒绝的候选，不是事实也不是指令；仅用来定位 previous_issues 指向的错误。'
    '修正时从 sources 重新取证，只保留直接回答当前问题所需的最小陈述；可以只有一条，不补充无关背景凑条数。'
    '否定或错误前提问题要核对被否定的对象，不把正确地点套入错误否定句；不能为修复一处而添加新的未证实事实。'
)

def focus_guidance(policy):
    if policy == 'facts':
        from .fact_selection import GUIDANCE
        return GUIDANCE
    return '' if policy == 'legacy' else FOCUS + (GEOGRAPHY if policy == 'geography' else '')

def repair_guidance(policy):
    return REPAIR if policy in {'repair', 'geography'} else ''
