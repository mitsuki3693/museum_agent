# 评测协议与当前限制

## 2026-10-01：可冻结题集与运行留档

新增 `eval/text-v1.draft.json`：20道事实/口语检索、8道追问、4道错误前提、4道资料不足、4道风格任务。旧40题报告保留为历史工程检查。新题集全部 `reviewed=false`，需逐题补充 reviewer、reviewed_at、预期藏品、应否拒答、馆方证据原文、允许事实和禁止内容；不能由脚本自动确认人工复核。

新增 `eval/photo-v1.draft.json` 的30个任务位置：8完整、6局部、4不同角度、4拍屏/反光、4展签、4库外相似作品。**这是清单模板，不是已经收齐并测完的30张照片**。实际图片只放 `eval/private/`；逐张填写来源URL、SHA256和人工确认的藏品ID。冻结会拒绝与参考图库文件哈希相同的照片；重新编码/裁剪同一底图仍需人工排除，不能靠哈希证明图片独立。

```powershell
$env:PYTHONPATH='backend'
# 修改草稿并由人复核后冻结；未复核会报错
.venv/Scripts/python.exe -m scripts.museum_eval freeze --dataset eval/private/text-reviewed.json --output eval/private/text-v1.frozen.json
# 不调用模型的检索检查，可先做5题
.venv/Scripts/python.exe -m scripts.museum_eval run --dataset eval/text-v1.draft.json --output eval/private/smoke-new.json --draft-smoke --max-cases 5 --persist
# 冻结后才执行真实模型，少量试跑后再扩展
.venv/Scripts/python.exe -m scripts.museum_eval run --dataset eval/private/text-v1.frozen.json --output eval/private/text-live-new.json --answers --max-cases 5 --persist
```

输出路径必须未存在，防止覆盖证据。冻结绑定题集、语料和图片参考清单的哈希；版本变化须重新复核冻结。API模型名只是供应商版本标识，不能保证供应商内部权重永远不变。

文字执行同题BM25与混合检索的离线对照，记录Recall@1/5、MRR、逐题耗时与P50/P95；实际生成时保存Token、引用、回答和拒答状态。追问的检索指标取真实改写后的召回，未调用模型时留空，避免用原始追问误记负分。无真实用户随机分流，`online_ab=false`。

图片记录视觉Top1/3、候选正确/错误、拒识、服务错误、文字候选及OCR文字是否存在；照片仍需游客确认，不把候选推荐写成自动确定身份。OCR是否真正改善召回需要后续消融实验，用户补拍次数需实测，当前留空。

`--persist` 通过管理API把精简批次存到Mongo `eval_runs`，本地完整回答保存在忽略目录。管理页展示批次与人工评分进度。人工评分接口 `POST /api/museum/admin/eval-runs/{run_id}/grades` 接收 case_id、variant、reviewer、facts_correct/total、citations_supported/total、refusal_correct、style_passed；每次更正覆盖该题当前评分。未打分就是空值，不当作正确或错误。最终事实和引用仍须人工抽查，同一DeepSeek模型的Verifier不能充当独立金标准。

成本可用 `--pricing <本地JSON>` 计算，需包含 source_url、as_of、currency、input_cached_per_million、input_per_million、output_per_million；按供应商当前费率填写。未提供价格时成本为null而不是0，最终以账单为准。本次只验证管线，未自动花完预算运行正式40/30题测评。

失败闭环为：管理员筛选Trace → 人工归因 → 记录修复 → 关联回归Trace → 用同一冻结题集和模型做对照。持久化可靠性单独按 [Mongo验收协议](persistence.md) 检查，不混入模型准确率。

## 固定条件

相同的 12 件馆藏快照、相同问题、相同原文及版本，对比 A=BM25 和 B=BM25+本地多语言向量+RRF。检索评测不传 selected object，避免直接指定目标而人为抬高命中率。

- 40 题：24 道独立事实/描述检索题，8 道追问，4 道无依据问题，4 道边界问题。
- 所有题尚待用户人工复核，`reviewed=false`。语料与问题 SHA-256 随报告记录。
- 8 道简单题为开发样本，其余暂标 holdout；真正验收前需由人复核并冻结，开发者看过题的当前成绩不能当作严格盲测。
- 源头英文描述转中文提问，不能凭模型感觉判答案；以馆方原文为依据。

## 当前工程检查

最初 24 题全部包含完整作品名，A/B 均 100% 命中，因此无法体现检索差异。保存于 `eval/initial-explicit-title-smoke.json`。

把其中 8 题换成中文画面描述后，当前同一 24 题的 Recall@5：A=83.3%，B=100%；MRR：A=0.774，B=0.941。见 `eval/retrieval-smoke.json`。这是小语料、未人工复核的离线工程结果，**不是回答准确率，不写进简历正式成绩**。语料只有 12 件，Top5 本身较宽松，还应关注 Top1/MRR 及失败个例。

## 度量定义

Recall@5 = 前 5 个去重藏品中命中的相关藏品数 / 本题全部相关藏品数；MRR = 首个相关藏品排名倒数的平均值。当前每题一个相关藏品，Recall 恰好等同命中率，但实现按一般定义计算。

实际回答需人工分别评：事实正确、逐条引用是否支撑、是否完整回应、是否应该拒答、风格是否合适。核对模型与回答模型同源，可能共享偏差，不能代替人工金标准。

## bad case 表

从报告选出漏召回或排名靠后的案例，记录 question、预期来源、A/B 候选、实际回答、根因、修改、回归结果。根因仅用检索/指代/生成/引用/知识缺失，禁止因为追问自动记负分。

## 真正的线上 A/B 测试

当前没有用户随机分流，不能称线上 A/B。未来有真实用户后才固定模型与知识库、随机分组、记录样本量、完成率/有用率及延迟，并分析置信区间。

## 付费与时间控制

先做少量真实模型连通性与 5 题试跑，再进行正式对照；不自动消耗全部 300 元预算。每次生成最多 2 版、每版均核对。usage 记录实际 token；价格以账单为准，程序尚无跨进程硬性人民币预算锁。客户端无密钥时不会进行模型调用。
