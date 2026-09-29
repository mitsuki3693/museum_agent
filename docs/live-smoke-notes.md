# 真实模型开发检查与 bad case

日期：2026-09-29。模型：DeepSeek `deepseek-flash`，馆藏：Art Institute of Chicago 的 The Bedroom。问题固定为“请介绍这件作品，让我先知道值得留意的地方。”使用同一份公开资料开发调试，不属于盲测、A/B 用户实验或总体准确率评估。

## 观察与修改

1. `eval/live-generation-thinking-failure.json`：请求返回的 1799 个 completion tokens 全部用于 reasoning，缺少可解析的 JSON，系统返回服务不可用并保留资料。官方接口默认开启 thinking，见 [官方参数说明](https://api-docs.deepseek.com/api/create-chat-completion/)。在博物馆客户端显式关闭 thinking；课程旧入口不改默认行为。增加实际 HTTP 请求载荷的回归测试。
2. `eval/live-generation-quote-failure.json`：模型能输出 JSON，但把年份、标题等其他段落事实并入单条讲解，所配引文不能支持整条陈述。生成内容被拦截，没有作为可信答案展示。将生成要求改为先选连续引文再忠实转述，逐条修正上次具体错误；审查员区分事实问题和纯文风偏好，继续拒绝无依据内容。
3. `eval/live-generation-smoke.json`：v2 提示词的一次运行通过逐字引用匹配和模型复核，耗时 5571 ms，2 次调用，provider 返回合计 2493 tokens。这个记录仍不是人工专家评分。该次模型还超出了 brief 的条数要求，因此随后增加服务端条数上限：brief 2 条，deep 5 条；超过部分在事实复核前截去并记录 omitted_claims。上限有自动化回归测试，不能把 5571 ms 当作 P95。

## 可复现与限制

在配置本地密钥、可访问模型服务的环境中，从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -m scripts.smoke_museum --live
```

该命令会进行少量付费调用并覆盖 `eval/live-generation-smoke.json`；前两份失败快照保留。模型服务可能更新，生成具有波动，因此不承诺每次结果或耗时相同。完整的 40 题评估仍等待人工复核，不因这一道开发题通过而宣布验收。

真实图片识别、语音设备兼容、MongoDB 持久化、公开部署与负载能力均不由这次检查证明。
