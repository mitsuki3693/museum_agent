# MongoDB 运行记录与评测闭环

## 本轮范围

只迁移运行数据。馆藏原文、讲解库、地图和视觉参考清单仍由固定版本 JSON 加载，V&A 原文、图片及实验明细继续保留在忽略目录。没有接入 Milvus、神经 Reranker 或多 Agent。

| 集合 | 保存内容 | 索引与保留策略 |
| --- | --- | --- |
| museum_sessions | 会话哈希、历史、过期时间 | BSON Date `purge_at` TTL；默认30分钟 |
| museum_traces | 原问题、改写、候选、生成草稿、引用核对、耗时、模型及版本 | 时间、会话、状态、失败环节 |
| museum_photo_traces | 图片哈希、图像/文字候选、相似度、最终候选、失败步骤、耗时、Token | 同上；不保存原图、文件名、EXIF、OCR原文 |
| museum_feedback | 有帮助/事实错误/没有解答及补充说明 | 会话与反馈更新时间 |
| museum_request_cache | 请求指纹、处理状态、固定Trace ID、完成结果 | 会话+请求ID唯一约束；TTL |
| eval_runs | 题集、语料、模型、提示词版本，逐题指标、人工评分 | 时间、题集及语料哈希 |

### 已实现的行为

- 数据库连接或建索引失败即启动失败，不悄悄退回内存。采用本机单实例 `w=1, journal=true`，这不是副本集高可用保证。
- 同一会话、同一请求ID及相同输入返回已保存结果；换问题返回409，不能覆盖。原子占位避免重复执行。
- 处理前保存运行记录，进程重启把未完成记录标为 `interrupted`。未完成请求重放返回409，由维护者检查后使用新ID重试。若完整Trace已落盘而缓存尚未完成，恢复缓存，不再次调用模型。
- 这不是跨进程事务或严格 exactly-once：Trace、会话历史和请求缓存分别写入，极端中断可能导致会话历史缺少最后一轮；保留Trace用于复盘。当前只支持一个API进程，禁止多个worker共享本地锁与备份写入闸门。
- 会话权限在服务端校验，A不能读取或评价B的Trace。会话过期立即返回401；MongoDB的后台TTL删除可以稍后发生。TTL仅清理会话和缓存，Trace/反馈保留。
- “新对话”关闭旧会话但保留复盘记录。原有 `DELETE /api/museum/session` 仍可清除当前有效会话所属记录；历史备份不会同步删除。已过期的历史记录需维护者按内部流程处理。
- `/review` 需要独立管理凭据，可筛选5种失败环节、执行状态和会话，人工记录原因与改动并关联回归Trace。自动归因只是提示。
- 导出采用字段白名单：仅保留指标、候选ID和版本，重新随机盐哈希会话/Trace编号；不导出问题、回答、引用原文、补充说明、原图。每批最多500条，可按时间翻页；不应把它当作去标识化原始语料。

## 本机配置与启动

已在当前工作区下载MongoDB 8.0.32与Database Tools 100.19.0到 `.runtime/tools/mongodb/`，不修改系统PATH。服务器包与官方SHA256核对一致；工具包记录了下载来源与本地SHA256。其他机器需从官方渠道自行安装，不随Git发布二进制。

```dotenv
MUSEUM_STORAGE=mongo
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DB=museum_agent
MUSEUM_ADMIN_TOKEN=设置独立随机凭据
MUSEUM_MONGODUMP=.runtime/tools/mongodb/mongodb-database-tools-windows-x86_64-100.19.0/bin/mongodump.exe
MUSEUM_BACKUP_DIR=.runtime/backups
MUSEUM_DAILY_BACKUP=true
```

先运行 `scripts/start-mongo.ps1`，再运行 `scripts/start-backend.ps1`，最后启动前端。数据库只绑定127.0.0.1，未开放给局域网。当前是本地个人演示，未实现正式账号/RBAC或数据库服务账号；公开部署前需单独设计认证、TLS和网络策略。

回滚应用可改回 `MUSEUM_STORAGE=memory` 并重启，原Mongo数据保留，但新记录不持久化；这是明确的人工切换，不能当作自动故障转移。

## 每日备份与恢复

服务运行时每24小时备份一次；关闭期间不会执行，下次启动补做。管理页显示最近状态，也可立即备份。失败有状态记录，后台在下个周期重试，未接入外部告警或Windows开机任务。

备份先暂停新的修改请求、等待正在处理的请求结束，再运行 `mongodump --archive --gzip`。比较导出前后所有6个集合的记录数和完整内容哈希；如果TTL清理或外部写入导致变化则标记失败，不认证为一致快照。备份期间请求等待，适用于小规模单进程演示，不是零停机多副本备份。

备份目录包含对话原文，属于私有数据；不上传GitHub。目前不自动删除旧备份，维护者需关注磁盘空间。凭据通过临时工具配置文件传递，执行完删除，不写进命令行。

恢复工具只允许**新的** `museum_restore_*` 数据库，不接受覆盖现有库，不自动切换应用：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m scripts.museum_restore --archive <archive.gz> --manifest <manifest.json> --target museum_restore_drill1 --tool <mongorestore.exe> --report .runtime/restore-drill1.json
```

工具核对压缩包SHA256，再比较恢复后的数量、ID和全部文档内容哈希。核对通过后由维护者决定切换数据库名；无需清空原库。

## 真实可靠性验收

可复现命令（独立测试端口27018/18000与 `museum_test_*` 库，假模型，不产生API费用）：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m scripts.check_museum_mongo --mongod <mongod.exe> --dump <mongodump.exe> --restore <mongorestore.exe> --output .runtime/new-reliability-report.json
```

2026-10-01实测通过：10次问答、3次识图、3条反馈后重启API及Mongo进程，6集合内容完全一致；重复请求不增写，改问题返回409，跨会话读取/反馈被拒；短TTL会话立即失效并由Mongo清理；20个会话产生200个唯一请求并重放200次，未丢失、串会话或重复写入；真实备份恢复到新的空库后数量和内容哈希一致。测试将TTL检查间隔设为1秒，正式数据库保持默认。完整原始报告在 `.runtime/mongo-reliability-verified.json`，公开摘要为 `docs/mongo-reliability-results.json`。

同日还对实际运行库做了重启前后完整内容比较，并用 `museum_restore` 工具恢复到独立空库，均通过；真实问答及识图各有一次连接失败，未改算法的重试分别成功回答和返回待确认候选，关联回归记录已保存。

这证明本地小规模持久化链路可靠，不证明回答准确率、真实模型并发性能、万人在线、硬件断电恢复或生产SLA。

## 参考

- [MongoDB TTL](https://www.mongodb.com/docs/manual/core/index-ttl/)：TTL清理由后台执行，不保证在到期瞬间删除。
- [备份与恢复工具](https://www.mongodb.com/docs/v8.0/tutorial/backup-and-restore-tools/)：运行中一致性备份需控制写入；本项目用单进程暂停写入。
- [mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/)：恢复到独立命名空间后再核验。
