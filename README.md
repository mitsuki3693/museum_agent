# MUSE · 博物馆随行助手（原馆语）

面向现场拍照、听讲解和继续追问的个人 AI 产品作品。基于经项目持有人授权的文枢课程代码二开；与芝加哥艺术博物馆、V&A 均无隶属关系，不代表在馆方上线。

## 当前状态（2026-10-07）

30 张新开发图对照完成：20 张库内不同视角／局部图、10 张库外相似作品，尚待人工复核。DINOv2 的库内 Top1 为 16/20，加入 ALIKED + LightGlue 后为 17/20，但发现拍摄台面匹配导致一例退化，提升案例也受背景干扰，故未接入游客端。220 项后端测试通过；图片和逐例证据仅本地保留。见 [实测结果与下一步](docs/local-matcher-stage-30.md)。

游客识图恢复流程：最多展示两件可确认候选，增加“都不是”、一次补拍后的文字查找和馆藏浏览入口；服务故障单独提示，用户确认与自动识别结论分开记录。217 项后端回归、10 项网页检查、正式构建及手机／桌面模拟交互通过。另生成 40 件作品、150 个空图片名额的独立评测采集计划，尚未收图或复核，不代表正式准确率。见 [恢复流程与扩库评测准备](docs/photo-recovery-flow.md)。

局部识图实验：可见性规则改善了花器顶部候选展示，DINOv2 patch 暂未显示额外收益。后续已修复新分支的部位字段容错：18 份真实回复配对校验中，4 份格式失败恢复正常，其余 14 份游客结果保持一致；208 项后端测试通过。海神背面仍未解决，默认保持旧核对方式。结果已存入本地评测记录，不是正式准确率或线上 A/B。见 [实现范围与失败记录](docs/partial-region-verification.md)。

海神视角诊断：独立实验图库仅增加一张 V&A 档案肩背参考，12 图本地检索中海神背面从第 2 名升至第 1 名，其余库内图排名未下降。五图真实对照已完成：背面新增组仍三次拒识；原组一次误推参孙，新增组未出现错误身份，但有一次连接失败。说明检索改善尚未解决跨视角核对；当前网站图库未切换。见 [参考视角对照](docs/neptune-reference-view.md)。

代码已发布到 [GitHub](https://github.com/mitsuki3693/museum_agent)；应用代码提交 `7ac23d6` 的 [Actions 检查已通过](https://github.com/mitsuki3693/museum_agent/actions/runs/36545829709)。

最新进展：本地 V&A 试验库扩至 **100 件**（连同 AIC 共 112 条），增加可续传导入和版本化图像特征缓存。固定开发图的正确作品检索排名没有因扩库退化；真实单图/多图核对对照未见明确收益，故多图模式仍关闭。局部图仍有拒识、相似花器仍保留展签保护；没有正式准确率或线上 A/B 结论。详见 [100 件试验库与实际对照结果](docs/pilot-100-evaluation.md)。此前 [花器修复记录](docs/blue-ceramics-diagnosis.md) 及以下测试数量均为历史版本证据。

- 原版 59 项离线测试通过；初版交付时共 81 项测试通过，历史记录见 `docs/combined-tests.xml`。
- 网页生产构建通过，真实本地多语言向量模型已运行。
- 已导入 12 件公开藏品，准备 40 道待人工复核问题。
- DeepSeek 密钥已在本地配置，单件藏品的真实生成与引用核对已跑通，保留失败与修复记录。这不是正式准确率评测；未配置密钥的副本显示“资料检索模式”。
- 本机已切换 MongoDB 持久化，真实重启、幂等/冲突、会话隔离、TTL、20会话200请求和备份恢复验收通过。馆藏原文仍保留为固定JSON，详见 [运行记录与验收](docs/persistence.md)。独立检出默认仍为 memory，需自行配置Mongo。
- `/review` 已支持文本/照片失败筛选、人工归因、回归关联、脱敏指标导出、备份状态和评测批次。新增40道文字/30张图片的待复核清单、冻结校验和版本留档；没有产生正式准确率。
- 本轮162项后端测试及前端生产构建通过；真实API检查发生过连接失败，同题和同图重试后分别回答成功、返回待确认作品候选，前后记录均保留。
- 离线检索报告只是小样本工程检查，不是回答准确率、线上 A/B 测试或真实用户成效。

## 9 月 29 日后续版本

- 支持作品图片确认、口语找画、HTTP 局域网手机会话兼容，以及简明／深入／儿童讲解入口。
- 增加可选的本地 V&A 官方英文讲解试点；中文内容是项目改写，不是馆方官方中文稿。私有数据不会随仓库发布，见 [讲解库说明](docs/local-narration-library.md)。
- 9 月 30 日将本地 V&A 试点扩充至 8 件，配套 24 份三种风格讲解；新增 7 张视觉参考图和 7 张独立的展厅检查图。加载通过，同时保留展厅照片无法确认的真实失败，见 [扩充范围与验证记录](docs/va-collection-expansion.md)。
- 增加 DINOv2 图片特征检索，与文字候选合并后核对参考图。默认关闭，通过本地参考图库显式启用，见 [实现、配置与测评边界](docs/visual-retrieval.md)。
- 游客入口整合为连续聊天：底部始终保留文字、拍照、相册、语音入口；照片和原问题在确认作品后衔接，简明／深入／儿童版通过回答下方选项递进。见 [聊天入口与验收记录](docs/chat-interface.md)。
- 前端以 MUSE 为独立英文名称，使用黑紫背景、白色字标与少量暖黄强调；没有使用馆方标志，不代表馆方官方应用。
- 新增对话内路线规划：确认时间、兴趣与起点后，在有来源的有限展厅图上推荐顺序，支持跳过重算；找出口、楼梯／电梯、厕所和服务台独立读取馆方设施说明与位置链接。地图、实馆数据和本地验证记录不公开，见 [路线规划说明](docs/route-planning.md)。
- 路线卡可加载本地保存的馆方真实平面图，叠加一条人工核对通道的固定路线，展示“你在这里”、播放／暂停、进度拖动及放大跟随。模拟位置与速度，未接入设备定位；地图原图和实馆坐标不随仓库公开。
- 新增独立 `/staff-demo` 单案例：模拟聚集与环境异常 → 查看预置公开依据 → 人工确认演示限制 → 游客路线预览重算。仅作用于演示，不修改正式路线或接入内部文保资料；详见 [馆方协作演示](docs/staff-demo.md)。
- `/conservation-demo` 为独立文保工作台 UI：模拟温湿度趋势、带出处的预置问答、适用范围查看、人工复核及模拟记录导出。无真实监测、内部 RAG、设备控制或账号系统，详见 [文保演示说明](docs/conservation-demo.md)。
- 本地 Python 自动化检查现为 140 项；最新前端网络回归 4 项通过，前端类型检查及生产构建通过。前述 Actions 链接是历史提交的结果，不代表这些后续改动已经运行云端 CI。

## 本地运行（Windows / Python 3.12 / Node 22+）

在仓库根目录执行：

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-vector.lock
uv pip install --python .venv/Scripts/python.exe --no-deps -e backend
Copy-Item .env.example .env
.venv/Scripts/python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_folder='models')"
```

只在本地 `.env` 填写 `DEEPSEEK_API_KEY`。当前官方模型名 `deepseek-flash`，详见 [DeepSeek 文档](https://api-docs.deepseek.com/updates/)。不要提交 `.env`。

分别在两个终端启动：

```powershell
./scripts/start-backend.ps1
```

```powershell
cd web
npm ci --ignore-scripts --no-audit --no-fund
npm run dev -- --hostname 127.0.0.1 --port 3000
```

打开 http://127.0.0.1:3000 。管理页 `/review` 需要 `.env` 中设置独立的 `MUSEUM_ADMIN_TOKEN`，未设置时关闭访问。修改 `.env` 后重启后端。

如果还未下载向量模型，可以将 `MUSEUM_EMBEDDING=lexical` 明确切到 BM25 检索；这不是语义向量检索。`local` 模式模型缺失会启动失败，不会偷偷改用 hash 向量。

## 游客入口（依据用户反馈于 9 月 29 日调整）

拍作品或展签 → 图片特征检索（可选）＋可见文字检索 → 照片与候选记录／参考图核对 → **游客确认作品** → 有依据的讲解 → 点击播放 / 语音追问。名称搜索为备用入口。

图片仅在点击发送后交由 DeepSeek 处理；后端检查大小、格式、像素数，去除 EXIF 后发送，不保存原图。照片预览只在当前页面内存中保留，新对话或离开页面后释放。浏览器语音识别可能由浏览器厂商远端服务处理，文本须用户确认后发送。语音播放依赖设备可用的中文音色；不能声称已验证所有手机。

公开仓库覆盖 12 件文字示范馆藏；截至 9 月 30 日，本地 V&A 试点增加 8 件，共 20 件。当前本地图片参考库包含 13 张图，覆盖其中 11 件；不能将文字库规模当作视觉识别覆盖率。这不是通用文物鉴定。参观路线使用独立核对的地图快照，覆盖范围与作品识别库不同；没有室内定位或实时通行信息，不能作为实时导航。

## 最小产品链路

```mermaid
flowchart LR
    A[选藏品或输入问题] --> B[会话隔离 / 重复请求保护]
    B --> C[追问改写]
    C --> D[BM25 + 多语言向量 + RRF]
    D --> E[回查有效版本原文]
    E --> F[生成逐条陈述及原文引用]
    F --> G[原文匹配 + 模型事实复核]
    G --> H[展示通过核对的回答或降级]
    H --> I[明确反馈 / 记录 / 人工复盘]
```

保留课程的 `BM25Index`、`HybridRetriever`、`MemoryVectorStore`、`EmbeddingClient`、`DeepSeekClient` 和存储接口。博物馆入口为 `app.museum.api:app`；旧校园入口 `app.main` 不属于当前演示。未启用 pi、Redis、校园路由、自进化、Milvus、神经重排、K8s。

## 检查和评测

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
.venv/Scripts/python.exe -m scripts.evaluate_museum
cd web
npm run build
```

`eval/questions.json` 共 40 题。先复核问题、原文、预期行为，再把 `reviewed` 设为 `true`；不要批量机械勾选。当前检索脚本对其中 24 道独立且有来源的题比较 BM25 与混合检索；追问、拒答和边界题另行进行完整回答检查。

`--answers` 只在全部题目已复核且有密钥时允许付费运行，并留下待人工评分的回答。模型审查通过不等于人工认定正确。见 [评测说明](docs/evaluation.md)。

## 资料与许可

数据来自 [Art Institute of Chicago 官方 API](https://api.artic.edu/docs/)，保留原始响应、来源 URL、采集时间、来源哈希。

**实际接口响应明确：description 为 CC BY 4.0，其余元数据为 CC0。**馆方介绍已去除 HTML 标签，并添加中文字段标签；作者为 Art Institute of Chicago，作品级来源链接见每条记录，许可见 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。3 张示例图片来自公有领域复制图，单独记录于 `data/image-provenance.json`；V&A 试点素材只保存在本地忽略目录。不声称提供实时展位或开放状态。

刷新资料：`.venv/Scripts/python.exe -m scripts.import_artic`。刷新后须重新复核评测集。

## 维护边界

- 仅监听本机；默认限制同时处理 2 个问题。这不是高并发认证或公开生产部署。
- 会话 token 随机生成，服务端只存其哈希，30 分钟后失效。当前单进程会话锁和限流不能用于多副本部署。
- 相同会话的相同请求编号会复用结果；请求编号与输入不一致返回冲突。
- 原始候选、生成草稿、核对结果、最终状态、模型 usage 分开记录；不伪造置信度或美元费用。
- 反馈只收集明确操作，不把追问默认当作不满意。
- 后续扩展必须保留引用、失败降级与回归检查。已有有限真实照片开发检查及简明／深入／儿童讲解；仍待用户体验评审，不能推广为任意照片都能识别。已加入有限馆区路线与设施查询；文保环境工具尚未实现。

详细见 [PRD](docs/PRD.md)、[已验证证据](docs/evidence.md)、[来源及修改归属](docs/provenance.md)。

图像开发检查：已完成四张合成控制图的真实模型调用，首轮包含一次失败，单独复测保留原记录。见 [图像评测说明](docs/photo-evaluation.md)，不能表述为真实照片识别准确率。
