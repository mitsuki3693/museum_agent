/** Authored, synthetic UI fixture. No museum readings or internal documents. */
export const samples = [
  {time:"09:00", rh:53, temperature:21.0}, {time:"09:15", rh:54, temperature:21.1},
  {time:"09:30", rh:55, temperature:21.2}, {time:"09:45", rh:56, temperature:21.3},
  {time:"10:00", rh:60, temperature:21.6}, {time:"10:15", rh:66, temperature:22.0},
  {time:"10:30", rh:68, temperature:22.1}, {time:"10:45", rh:68, temperature:22.2},
  {time:"11:00", rh:67, temperature:22.1},
];
export const reminder = 65;
export const sources = [
  {id:"D01", title:"展柜湿度提醒规则", kind:"模拟规则", version:"DEMO-RH-01 · v0.1", scope:"仅用于本案例的提醒演示",
    section:"第 1 条 · 提醒与复核", url:null,
    text:"本案例将 65% RH 设为演示提醒线。读数超过提醒线时，记录异常并提示人工复核；不自动判定藏品受损，也不自动控制设备。",
    limitation:"这是本项目编写的模拟规则，不是 V&A 馆方文件或通用文保标准。"},
  {id:"G01", title:"Climate guidelines overview", kind:"公开指南", version:"CCI 网页 · 核对 2026-09-30", scope:"气候控制决策的一般背景",
    section:"Purpose and limitations / Introduction to guidelines and specifications",
    url:"https://www.canada.ca/en/conservation-institute/services/preventive-conservation/climate-guidelines/climate-guidelines-overview.html",
    text:"不同藏品受温度、相对湿度及其波动影响的方式不同。气候控制决策还应结合建筑、控制系统与展柜等微环境条件。",
    limitation:"此处为中文摘要。原文不为本案例提供 65% RH 阈值、损坏诊断或设备设定值。"},
] as const;
export const questions = [
  {id:"reason", question:"为什么触发提醒？", status:"需人工复核", sourceIds:["D01","G01"],
    answer:"展柜 C-03 的模拟读数在 10:15、10:30、10:45、11:00 四次采样中均超过 65% RH 的演示提醒线，最新为 67% RH。\n\n这说明本案例满足提醒条件。下一步应核对测量、展柜情况和适用要求，不能直接推断藏品已经受损。"},
  {id:"damage", question:"这是否说明文物已经受损？", status:"依据不足，不能判断", sourceIds:["G01","D01"],
    answer:"不能。环境读数只构成需要复核的信号，不是损坏诊断。\n\n当前缺少藏品具体材料、状况检查和既往环境记录。不同对象对湿度及其波动的反应也不同，应先补齐这些信息。"},
  {id:"equipment", question:"能直接调整空调或除湿吗？", status:"需要补充现场条件", sourceIds:["G01","D01"],
    answer:"这份演示资料不足以给出设备设定值或操作指令。应结合展柜微环境、设施能力和经批准的控制要求，由有权限的人员确认处理方案。\n\n本页可以记录复核意见，不会向设备发送指令。"},
] as const;
export const initialNote = "建议核对 C-03 读数与传感器状态，补充展柜及藏品条件后复测。当前仅记录待复核事项，不调整设备，也不判定损坏。";
