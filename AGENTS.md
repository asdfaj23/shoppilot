# AGENTS.md

面向协作者（人或 AI）的开发约定。

## 项目是什么

ShopPilot 是一个**域可插拔的电商导购 Agent**，核心主张是 *Verifiable-by-Design*：
在可验证子问题上，用**零 token 的确定性输出护栏**替代 LLM 自我反思。结构为
`core / orchestrator / domains` 三层，依赖方向向内（core/orchestrator 不 import 具体业务域）。

## 开发环境

- Python 3.11；安装：`pip install -e ".[dev,serve]"`
- 不打真实 LLM 时也必须能跑：`ScriptedLLM` 是所有测试/演示的默认替身。

## 常用命令

```bash
pytest -q                 # 离线测试（ScriptedLLM，不联网）
ruff check src tests eval # 静态检查
python examples/01_quickstart.py              # 离线看完整链路 + 护栏拦截/修复
python -m eval.run --dry-run --all --limit 2  # 离线验证实验管线
```

## 改动护栏（最常见的扩展）

1. 在 `core/guardrails/` 实现 `OutputGuardrail`（`name / severity / check(ctx) -> Verdict`）；
2. 在 `domains/ecommerce/factory.py` 的 `build_ecommerce_guardrails` 里挂上；
3. 加一条用例到 `tests/unit/test_guardrails.py`；
4. **不要**在规则里调 LLM——要调 LLM 的检查器属于对照 baseline（见 `llm_reflection.py`），默认不进链路。

## 边界

- 不引入 LangGraph / AutoGen / CrewAI / 向量数据库（窄域任务，三部件足够）；
- 不提交 `.env`、`data/ecommerce/traces|reports|sessions`、`eval/reports/*`；
- 真实模型实验只在配置好 API key 后跑，本地默认离线。
