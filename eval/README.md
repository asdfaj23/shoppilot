# 输出护栏对照实验（eval/）

回答一个问题：**在同一个 ReAct 内核上，"最终输出怎么被检查"对违规率与成本的影响有多大？**

## 设计

- **同一内核**：规划 → 有界 ReAct 工具 → 成稿，四个臂共用；同一题库（`cases.json`，27 题）、同一模型、`temperature=0`、会话隔离。
- **只换检查策略**：
  | 臂 | guardrail_mode | 检查方式 | 修复轮次 |
  |---|---|---|---|
  | vanilla | `none` | 无 | 0 |
  | llm_reflection | `llm_reflection` | LLM 自反思（Reflexion） | ≤1 |
  | multi_agent | `multi_agent` | 独立 Critic Agent（AutoGen/MetaGPT） | ≤1 |
  | deterministic | `deterministic` | 6 条确定性规则 | ≤1 |
- **同一把尺子**：不管哪个臂产出的最终回答，都用同一套**确定性规则离线复扫**，统计"还剩多少违规"。不采用各臂自报的指标。

## 指标口径

- **任务通过率**：无降级 + 约束（must_contain / must_not_contain）全过 + 拒答行为正确。
- **违规率（总 + 四类）**：超预算 / 编造(库外实体) / 越界引用 / 误拒答。
- **额外 tokens/题**：检查与修复环节花的 token。deterministic 的规则本身 0 token，只有触发修复时才花一次。
- **相对延迟**：各臂端到端耗时对比。

## 跑法

```bash
# 离线冒烟（ScriptedLLM，零 token）：只验证管线，数字是脚本化占位
python -m eval.run --all --dry-run --limit 2

# 真实运行（需 API key，会产生少量费用）
cp .env.example .env   # 填 key
python -m eval.run --all            # 全部臂 × 全部题
python -m eval.run --strategy deterministic --limit 5
```

结果写入 `eval/reports/`：`results_<tag>_<time>.json`（明细）+ `main_table_<tag>_<time>.md`（主表）。

## 读表结论时的注意事项

- dry-run 的数字**不是研究结论**；真实结论以 `--dry-run` 关闭后的运行为准。
- 小样本下先看**方向性差距**（deterministic 是否把四类违规压到 0、且额外 token 远低于两个 LLM 臂），再谈百分比。
- 不与 llmeval 评测框架的口径混用；本实验只比较"输出检查策略"这一个变量。
