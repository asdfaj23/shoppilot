"""The four output-checking strategies of the ablation.

All four share the same ReAct kernel, the same seven tools, the same model at
temperature=0, the same cases and isolated sessions. They differ *only* in how
the final answer is checked:

  vanilla         no output check at all (raw multi-agent pipeline).
  llm_reflection  the model self-audits against a checklist (Reflexion) — high cost.
  multi_agent     a separate adversarial Critic Agent rejects/rewrites (AutoGen
                  / MetaGPT style) — highest cost.
  deterministic   the six zero-token programmatic rules + one bounded repair
                  (this project) — ≈0 extra tokens.

For fairness the two LLM arms are also bounded to at most one repair round.
"""
from __future__ import annotations

STRATEGY_ORDER = ["vanilla", "llm_reflection", "multi_agent", "deterministic"]

STRATEGIES: dict[str, dict] = {
    "vanilla": {
        "guardrail_mode": "none",
        "label": "无护栏（裸 ReAct）",
        "uses_llm_check": False,
        "max_repair_rounds": 0,
    },
    "llm_reflection": {
        "guardrail_mode": "llm_reflection",
        "label": "LLM 自反思（Reflexion）",
        "uses_llm_check": True,
        "max_repair_rounds": 1,
    },
    "multi_agent": {
        "guardrail_mode": "multi_agent",
        "label": "独立 Critic Agent（AutoGen/MetaGPT）",
        "uses_llm_check": True,
        "max_repair_rounds": 1,
    },
    "deterministic": {
        "guardrail_mode": "deterministic",
        "label": "确定性护栏（本项目）",
        "uses_llm_check": False,
        "max_repair_rounds": 1,
    },
}

# The four violation categories scored by the common deterministic ruler.
VIOLATION_KINDS = ["over_budget", "fabrication", "citation", "refusal"]
VIOLATION_LABELS = {
    "over_budget": "超预算",
    "fabrication": "编造(库外)",
    "citation": "越界引用",
    "refusal": "误拒答",
}
