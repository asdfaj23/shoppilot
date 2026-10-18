# ShopPilot Benchmark Report

> 3 题导购验收集 · 四项确定性指标（零裁判 token）· 字段设计与 llmeval 体系同源

## 总览

| 指标 | 数值 |
| --- | --- |
| 任务完成率 task_completed | 1.0 |
| 工具命中率 tool_selection_acc | 0.667 |
| 拒答正确率 refusal_correct | 1.0 |
| 引用可验证率 citation_verifiable | 0.667 |
| 平均 tokens/题 | 10405 |

## 明细

| id | 题目 | 完成 | 工具命中 | 拒答 | 引用 | tokens | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shop-rec-001 | 预算500以内推荐一副降噪耳机… | True | 0.0 | True | True | 14632 |  |
| shop-rec-002 | 我想给新家组网，预算300左右，有什… | True | 1.0 | True | True | 7054 |  |
| shop-rec-003 | 500预算买个充电宝，主要出差用，在… | True | 1.0 | True | False | 9530 |  |

## 复现

```
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
```