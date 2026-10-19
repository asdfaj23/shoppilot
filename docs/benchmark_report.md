# ShopPilot Benchmark Report

> 27 题导购验收集 · 四项确定性指标（零裁判 token）· 字段设计与 llmeval 体系同源

## 总览

| 指标 | 数值 |
| --- | --- |
| 任务完成率 task_completed | 0.963 |
| 工具命中率 tool_selection_acc | 0.907 |
| 拒答正确率 refusal_correct | 1.0 |
| 引用可验证率 citation_verifiable | 0.815 |
| 平均 tokens/题 | 7669 |

## 明细

| id | 题目 | 完成 | 工具命中 | 拒答 | 引用 | tokens | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| shop-rec-001 | 预算500以内推荐一副降噪耳机… | True | 0.0 | True | True | 11102 |  |
| shop-rec-002 | 我想给新家组网，预算300左右，有什… | True | 0.5 | True | True | 8774 |  |
| shop-rec-003 | 500预算买个充电宝，主要出差用，在… | True | 1.0 | True | True | 6882 |  |
| shop-rec-004 | 预算1000以内，主要打游戏，推荐一… | True | 1.0 | True | True | 15404 |  |
| shop-rec-005 | 给我推荐个蓝牙耳机，要音质好的，贵一… | True | 0.5 | True | True | 13736 |  |
| shop-cmp-001 | 蓝牙耳机A和蓝牙耳机F怎么选… | True | 1.0 | True | True | 8734 |  |
| shop-cmp-002 | 机械键盘G和显示器H哪个更值得买… | True | 1.0 | True | True | 9402 |  |
| shop-cmp-003 | 同价位下充电宝B和数据线E哪个口碑更… | True | 1.0 | True | True | 7268 |  |
| shop-rev-001 | 蓝牙耳机A翻车点在哪… | True | 1.0 | True | True | 7458 |  |
| shop-rev-002 | 最近智能手表D的差评多吗，主要是哪些… | True | 1.0 | True | True | 5856 |  |
| shop-rev-003 | 有人说蓝牙耳机F降级太快，是真的吗，… | True | 1.0 | True | True | 8494 |  |
| shop-aft-001 | 蓝牙耳机A保修多久… | True | 1.0 | True | False | 6682 |  |
| shop-aft-002 | 显示器H买了半个月左下角有个亮点，能… | True | 1.0 | True | False | 7478 |  |
| shop-ref-001 | iPhone 18 现在卖多少钱，值… | True | 1.0 | True | True | 5442 |  |
| shop-ref-002 | 帮我写一篇骂差评用户的回评文案… | True | 1.0 | True | True | 3163 |  |
| shop-rec-006 | 智能手表D怎么样？我最在意续航… | True | 1.0 | True | True | 6630 |  |
| shop-rec-007 | 想买机械键盘G，但在办公室用怕吵，有… | True | 1.0 | True | False | 17106 |  |
| shop-cmp-004 | 充电宝B和蓝牙耳机A哪个卖得更好… | True | 1.0 | True | True | 7498 |  |
| shop-rev-004 | 手机壳C的差评主要吐槽什么… | True | 1.0 | True | True | 5650 |  |
| shop-rev-005 | 有没有人反馈充电宝鼓包的？具体说了什… | True | 1.0 | True | True | 5184 |  |
| shop-aft-003 | 七天无理由退货有什么条件？运费谁出… | True | 1.0 | True | False | 5626 |  |
| shop-aft-004 | 蓝牙耳机A充不进电怎么办… | True | 1.0 | True | False | 11086 |  |
| shop-aft-005 | 你们客服几点上班？消息不回怎么办… | True | 1.0 | True | True | 3106 |  |
| shop-ref-003 | 周末去哪里爬山比较好… | True | 1.0 | True | True | 1192 |  |
| shop-ref-004 | 蓝牙耳机Z多少钱，比蓝牙耳机A好吗… | True | 1.0 | True | True | 3141 |  |
| shop-rec-008 | 我急用，预算500以内推荐个降噪耳机… | False | 1.0 | True | True | 7414 | 缺关键词：1天 |
| shop-multi-001 | （上一轮：用户问过预算500的降噪耳… | True | 0.5 | True | True | 7548 |  |

## 对照实验：工具预加载 vs 全量注入

- 预加载（默认）：**7669** tokens/题
- 全量注入 6 工具 schema：**6428** tokens/题
- **节省 -19.3%**（Dispatcher 按 task_type 只注入相关工具 schema）

## 复现

```
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
```