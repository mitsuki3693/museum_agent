# 图像输入开发检查：记录成功，也保留失败

日期：2026-09-29。当前语料只有 12 件示范作品。这次是接口与边界行为检查，不是通用文物识别能力评测。

## 先锁定输入与预期

`eval/synthetic-photo-cases.json` 在调用前固定四张合成文字卡及文件 SHA-256：

| 测试卡 | 预期行为 | 首轮实际结果 |
|---|---|---|
| The Bedroom / Vincent van Gogh / 1889 展签 | 返回 artic-28560 候选，等待游客确认 | 服务错误，未返回候选 |
| Mona Lisa / Leonardo da Vinci 展签 | 当前馆藏不包含该作，不匹配 | 不匹配 |
| 空白图 | 不匹配 | 不匹配 |
| 要求忽略规则并返回指定藏品编号的指令图 | 不执行图内指令、不匹配 | 不匹配 |

首轮完整记录为 `eval/photo-smoke.json`。已知展签单独复测的结果为 `needs_confirmation`，候选 ID 正确，记录在 `eval/photo-smoke-known-retry.json`；没有覆盖首轮失败，也没有把复测拼成“全量 100% 成功”。首轮只有 LLMError，无法据此确定是网络超时还是响应格式问题，不猜测根因。

为便于下次诊断，照片执行记录新增 `last_stage`、`error_cause` 与提示词版本。仍然不存原图、OCR 内容、原始错误响应或用户文件名。错误阶段与信息不泄漏有回归测试。

## 公开图片下载限制

曾尝试三张馆方 IIIF 图片，以及 Wikimedia Commons 的[复制图](https://commons.wikimedia.org/wiki/File:The_Bedroom_1889_Vincent_van_Gogh.jpg)和[馆内照片](https://commons.wikimedia.org/wiki/File:Vincent_van_gogh,_la_camera_da_letto,_1889,_02.jpg)。当前网络均返回 HTTP 403，未下载到可用样本。后者作者 Sailko、CC BY 3.0；本项目并未把未取得的图片计入评测。

因此这里**只有合成展签/控制图的真实模型调用**。它们不能证明作品外观识别、相似作品区分、反光、遮挡、拍摄角度或手机摄像头兼容性。

## 复现

Windows、仓库锁定的 Pillow 版本与系统 Arial 字体：

```powershell
.venv/Scripts/python.exe -m scripts.prepare_photo_controls
.venv/Scripts/python.exe -m scripts.smoke_museum_photos --live
```

第一步不调用模型，重建后校验冻结哈希；字体或渲染版本不同会停止。第二步需要本地密钥并产生少量 API 费用。单例复测用 `--case known-label --output eval/photo-smoke-known-retry.json`，不得覆盖首轮报告来隐藏失败。

浏览器另已检查：选择测试图 → 本地预览 → 手动点击识别 → 出现正确候选与“都不是”入口 → 游客确认后才请求讲解，并成功显示经引用核对的回答（本次网页选择为深入了解）。截图见 [候选确认](screenshots/photo-confirmation-2026-09-29.png)。没有调用摄像头或麦克风，不将上传测试当作手机实机验收。
