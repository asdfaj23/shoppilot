# 数据说明（重要，面试会问"你的数据是哪来的"）

## 数据策略：真实开源底表 + LLM 合成增强

本项目当前数据为 **LLM 合成数据**（结构模仿巴西 Olist 电商数据集），理由：
1. Olist 在 Kaggle 需登录下载，且原始表很大（10 万+ 订单）；
2. 教学阶段重点是 Agent 编排逻辑，不是数据规模；
3. "哪些真、哪些合成、怎么合"本身就是 JD 常见考点，能讲清楚反而加分。

## 换成真实开源数据的方法（强烈建议第 2 周末做）

1. 去 Kaggle 下载 Olist Brazilian E-commerce 数据集：
   https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
2. 需要 `olist_order_reviews_dataset.csv`（评论）和 `olist_orders_dataset.csv`（订单）
3. 用 pandas 清洗后映射到本项目的两张表结构：
   - `reviews.csv`: product, rating, aspect, text, date
   - `sales.csv`:  category, product, sales, rank
4. 覆盖 `data/*.csv` 即可，代码无需改动（`week2/tools.py` 只认这两张表）

## 文件清单

| 文件 | 内容 | 被谁使用 |
|---|---|---|
| `reviews.csv` | 商品评论（星级/维度/原文/日期） | week2/tools.py、week4 数据 Agent |
| `sales.csv` | 品类销量排行 | week2/tools.py、week4 数据 Agent |
| `knowledge/*.md` | 商品手册/售后政策/物流FAQ 知识库文档 | week3 RAG、week4 检索 Agent、week4 评测集生成 |
| `eval_cases.json` | LLM 生成的评测集（week4 产出） | week4/eval.py |
