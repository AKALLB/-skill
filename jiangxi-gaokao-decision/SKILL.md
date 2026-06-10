---
name: jiangxi-gaokao-decision
description: Use when advising a Jiangxi Gaokao candidate about applications, university program groups, majors, public-sector paths, company careers, entrepreneurship, Jiangxi demand, or AI-era career durability.
---

# 江西报考决策

## 核心原则

这不是热门专业推荐器，也不是人物模仿器。它以可追溯证据研究普通家庭
如何进入社会分配体系，再决定院校专业组和四年行动路线。

必须遵守：

- 数据先于结论；过期、缺失或冲突必须明示。
- 热门度不得加分。
- 不得生成万能总分；保留各维度差异和取舍。
- 第三方来源只能作为线索，关键结论回到官方原文或原始研究。
- 性别不得触发职业刻板印象，只处理用户明确提出的现实约束。
- 执行力不足时增加外部约束，不使用宿命化或侮辱性标签。

## 六层架构

按顺序使用原始证据、关系数据、文本检索、证据审查、决策引擎和个案记忆。
详细模型见 [references/data-model.md](references/data-model.md)。

## 开始分析前

在工作区定位 `.skill-data/jiangxi-gaokao-decision/`。若不存在，运行：

```powershell
python <skill-dir>/scripts/warehouse.py init
```

读取 `profile.yaml`，再运行 `status`。对 `missing` 或 `stale` 模块运行
`refresh`；网络失败时保留旧快照并标注数据缺口，不得假装已经更新。

常用命令：

```powershell
python <skill-dir>/scripts/warehouse.py status
python <skill-dir>/scripts/warehouse.py refresh
python <skill-dir>/scripts/warehouse.py search "色觉异常"
python <skill-dir>/scripts/warehouse.py query "SELECT * FROM admission_results"
python <skill-dir>/scripts/warehouse.py import jxeea path/to/file.pdf --period 2025
python <skill-dir>/scripts/warehouse.py trace 1
python <skill-dir>/scripts/warehouse.py validate
```

## 决策协议

1. **硬门槛**：预算、地域、公办性质、中外合作、选科、体检和组内最坏调剂。
2. **社会入口**：分别研究公家、公司、创业，不默认其中任何一条更高贵。
3. **学校资源**：只计算普通学生可获得的校招、实习、证书、实验室、
   本地认可和校友连接，不拿少数优秀案例代表全体。
4. **身份覆盖**：用真实职位表统计专业大类、学历、应届、资格证和其他条件。
5. **区域兑现**：政府报告只是信号；必须继续核验投资、企业落地和本科岗位。
6. **AI任务分析**：拆解岗位任务的替代、增强和责任保留，不粗暴判断专业消失。
7. **执行匹配**：按真实行为设计路线，不假设进入大学后会自动觉醒。
8. **路径组合**：给出主路径、备选路径、保底路径及四年行动和失败退路。

输出遵循 [references/output-contract.md](references/output-contract.md)。
每项关键判断形成：

**结论 → 数据 → 原始来源 → 推理过程 → 不确定性 → 失败退路**

## 本地个案

个人条件以工作区 `.skill-data/jiangxi-gaokao-decision/profile.yaml` 为准，
不得把分数、位次、家庭条件或动态偏好写回 Skill。档案不存在时，先收集
考试年份、选科、分数或位次、地域、预算、办学性质和不可接受项，再创建
本地档案；不得将该档案、数据库或抓取快照上传到公开仓库。

## 禁止捷径

- 不能因计算机、电气、医学或师范“热门/稳定”就直接推荐。
- 不能用学校最低投档分代替具体院校专业组和组内专业。
- 不能用一份政府报告证明四年后的就业需求。
- 不能用历史人物言论代替最新政策、职位表和产业证据。
- 不能在2026年数据未发布时伪装成正式志愿排序。
