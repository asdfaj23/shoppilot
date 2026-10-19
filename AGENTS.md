# AGENTS.md · 给 AI 协作者的工作约定

## 项目是什么

ShopPilot：电商导购智能体。8 层 Agent Harness（config/llm/models 契约/harness/agents/前端），
组合"规划→ReAct→Reflection"三范式与四类记忆分层。教学代码已归档至 `archive/week1-4`
（只读参考，不再修改）。项目全景文档见 `docs/PROJECT_GUIDE.md`（数据/架构/示例/评测/面试问答合一）。

## 示例提问（与文档/控制台/题库保持同源）

README「示例提问」一节是**官方示例集**（五大场景 × 若干问题，与 `ShopPilot.html`
场景卡片、`bench_cases.json` 题库对齐）。约定：
- 改商品池/工具/意图路由后，先跑 README 全部示例链接确认行为；
- 新增场景能力 = README 加示例 + 控制台加卡片 + `bench_cases.json` 加题，三处同步。

## 环境与命令

- Python：`D:\conda_envs\agent\python.exe`（依赖已装齐，勿新装；Windows cmd）
- 运行包内脚本一律用 `-m`：`python -X utf8 -m shoppilot.xxx`（直接跑文件会 sys.path 报错）
- 前端：`python -m streamlit run app.py`
- 评测：`python -X utf8 benchmark.py --run [--compare] [--limit N]`
- .env 存 API Key（已被 .gitignore）；`config.py` 是唯一配置出口，禁止散落 `os.getenv`

## 架构纪律（改代码前必读）

1. **单向依赖**：`models.py`（契约）← 所有模块可 import；`domains → harness → agents`；
   `app.py / benchmark.py / mcp/server.py` 是平级消费方，只 import `agents.team.run_team`
   与 `domains.ecommerce.tools.registry`，不得跳层。
2. **数据契约**：LLM/工具的脏数据一律在 `models.py` 的 `field_validator` 清洗，
   不要在使用点各写一套。
3. **工具**：新增工具 = 在 `domains/ecommerce/tools.py` 写实现 + Pydantic args 模型 +
   `register_all()` 注册（name/func/description/args/task_types 五要素），
   OpenAI schema 由 ToolRegistry 自动生成，禁止手写 schema。
4. **记忆**：四类记忆（短期/Session State/画像/知识库）语义不混；画像只写确定性抽取的
   用户明确表达；任何"长期记忆写入"必须过筛（稳定/有用/被确认）。
5. **Reflection**：新增检查必须是**确定性规则**（可回归、零 token），LLM 只做修复。
6. **评测**：新增能力先在 `data/ecommerce/bench_cases.json` 加题（字段设计与 llmeval
   llmeval schema 同源：id/prompt/reference/constraints/meta.expected_tools/expected_behavior），
   跑 `benchmark.py --run` 回归后再提交。

## Git 约定

- 按阶段 commit 存档点，message 中文、写清层与机制（参考 git log）。
- 提交前跑 `python -X utf8 benchmark.py --limit 3` 冒烟（省 token），大改动全量回归。
