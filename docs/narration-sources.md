# 官方讲解稿来源核查与首批选材

核查日期：2026-09-29。只核查官方网页和 API 文档；未批量下载音频或逐字稿。本文是选材记录，未确认的权利不标为已授权。

## 结论

官方讲解逐字稿确实存在，适合研究讲解结构；公开可读不等于可以把整套原文或 AI 改写版本发布到公开 GitHub。首批继续使用现有 AIC 馆藏介绍，为 3 件作品编写带证据的中文基础讲稿，经人工核对后再让 AI 调整表达。该内容应标为“依据馆方介绍编写的项目讲稿”，不能标成“馆方官方讲解稿”。

## 已观察到的官方资料

| 来源 | 实际核查结果 | 本项目用途 |
|---|---|---|
| [V&A Europe 音频帮助](https://www.vam.ac.uk/audioguide/europeaudio/help.html) | 明确每段音频都有 transcript，提供 Read transcript 操作；可按作品、房间、主题进入 | 证明官方讲解稿入口真实存在 |
| [V&A Europe & the World](https://www.vam.ac.uk/audioguide/europeaudio/tour/1/) | 页面可读 Neptune and Triton、Table、Time and Death、Flower pyramid 等逐字稿，包含讲述人身份、观察引导和解释；同页另有第三方音乐授权说明 | 人工研究内容组织和来源索引；不直接整套搬入公开语料 |
| [V&A 无障碍服务](https://www.vam.ac.uk/info/disability-access) | Raphael Court 的触觉版本附凸起二维码，可用个人手机扫码听描述；页面说明 7 段约 3–4 分钟的作品音频及导言 | 支持“二维码选定作品→听讲解”的产品参考；不能据此推断已开放内容再分发 |
| [AIC API：Mobile Sounds](https://api.artic.edu/docs/#mobile-sounds) | 文档有 `transcript` 与 `web_url`；示例 4742 为 Hartwell Memorial Window 的讲解，含策展人及旁白文本 | 证明 AIC 官方逐字稿存在；本次未把该作品加入原有 12 件 |

V&A 页面的观看和聆听入口已确认；本次没有实际测试馆内二维码或播放完整音频。AIC 的逐字稿证据来自官方文档示例，单条实时 API 请求未成功，因此不把字段示例当作完整现行语料覆盖率。`tours` 的介绍文字稿也不能替代逐件作品讲解；本次未核实 3 件试点各自的官方音频 ID 与对应全文。

## 许可边界

- **AIC 馆藏介绍：** [Artworks 端点说明](https://api.artic.edu/docs/#artworks)明确 `description` 为 CC BY 4.0，其他响应数据为 CC0，且援引馆方条款。不能把元数据许可自动扩展到图片文件、音频或其他端点。
- **AIC 音频讲稿：** [Mobile 部分的 Tours / Mobile Sounds](https://api.artic.edu/docs/#mobile)采用版权和第三方限制声明，列出非商业教育、个人用途和法律允许的合理使用，并要求保留权利声明及作者、来源。它们没有获得与 `artworks.description` 相同的通用开放许可。因此，目前不足以确认整库公开再分发和风格改写的授权。
- **AIC 总条款：** 官方入口是 [Terms](https://www.artic.edu/terms)。本次网页工具无法取得正文；API 文档中已经明确的端点许可可据实记录，总条款细节仍须补核。不能写成已完整审阅。
- **V&A：** [网站条款第 1、2、5、6 节](https://www.vam.ac.uk/info/va-websites-terms-conditions)允许规定范围内非商业使用 V&A 自有内容并要求署名；第三方内容须另查，不能暗示馆方认可。没有发现针对本批逐字稿、足以支持通用公开讲稿库及其改编的逐项开放许可。本项目先保留链接和研究笔记；如要全文收录并改写，须核定每篇权利和具体用途。不能把“非商业可用”简化为“禁止使用”，也不能把它扩大成不受限开放授权。
- **已许可介绍的改写：** [CC BY 4.0 官方说明](https://creativecommons.org/licenses/by/4.0/)允许分享及改编，包括商业用途；需要适当署名、附许可链接并注明修改。中文翻译、口语重写和 AI 调整都应保留来源与修改记录，不暗示馆方审校或背书。

## 建议：先做 3 件、每件一份可审核底稿

下列 3 件均已存在于本地 `data/corpus.json`，保存了 AIC 来源、许可、抓取时间与内容散列；当前内容是作品介绍，不是音频逐字稿。

| 作品 | 官方事实来源 | 首批底稿范围 | 官方音频逐字稿状态 |
|---|---|---|---|
| The Bedroom，28560 | [作品页](https://www.artic.edu/artworks/28560) / [API](https://api.artic.edu/api/v1/artworks/28560) | 色彩、休息意图、芝加哥所藏版本 | 未核实对应音频及改编授权 |
| Water Lilies，16568 | [作品页](https://www.artic.edu/artworks/16568) / [API](https://api.artic.edu/api/v1/artworks/16568) | 水面、睡莲与树云倒影，1906年版本 | 未核实对应音频及改编授权 |
| A Sunday on La Grande Jatte—1884，27992 | [作品页](https://www.artic.edu/artworks/27992) / [API](https://api.artic.edu/api/v1/artworks/27992) | 点彩技法、标题年份与完整创作跨度 | 未核实对应音频及改编授权 |

建议底稿采用“指向一个细节→解释可证实背景→给出开放观察问题”的结构。编辑新增的是组织方式、中文表达和观察问题；不能新增无来源的传说、心理活动或历史因果。AI 仅在审核后的底稿和证据范围内调整成人简洁版、儿童易懂版等表达。

每条保留 `artwork_id`、来源 URL、来源版本、许可、原文证据、基础稿版本、修改说明、人工审核状态、风格版本和停用状态。人工审核前保持 draft；公开展示时注明“项目改写，非馆方官方讲解”。公开仓库可包含允许再分发的来源和项目底稿，不纳入未确认权利的整套 V&A/AIC 音频稿。

300 元预算不必用于购买或批量转写官方音频；优先留给少量生成对比、失败重试和演示成本。这是预算分配建议，不是已经验证的调用报价。当前调查足以支持 3 件小范围试点，尚不能证明完整导览库已建立、馆方授权已取得或 AI 改写质量已通过评测。
