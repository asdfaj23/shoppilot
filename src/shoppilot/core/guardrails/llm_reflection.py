"""LLM self-reflection baseline (Reflexion-style).

This is the *control* for the experiment, not part of the default pipeline. It
asks an LLM to audit the answer against a natural-language checklist covering the
same failure modes the deterministic rules catch. It therefore spends tokens and
is non-deterministic — which is exactly what we compare against. Track its spend
on ``self.total_tokens`` so the experiment can attribute the extra cost.
"""
from __future__ import annotations

import json

from shoppilot.core.guardrails.base import GuardrailContext, Verdict
from shoppilot.core.llm import LLMProvider

DEFAULT_CHECKLIST = """请逐条核查回答：
1. 回答中的引用编号 [n] 是否都能在"已检索资料"中找到，有无凭空编号？
2. 推荐商品的价格是否超出用户预算上限？有无虚构商品价格/参数？
3. 回答提到的商品是否都来自已检索资料，有无编造商品池之外的商品？
4. 若用户是售后/政策类问题，回答是否在有资料时错误地拒绝回答（误拒）？
5. 回答是否覆盖了用户提到的所有商品与在意维度？
6. 该给结构化推荐/对比/研究结果的，是否真的给出了？
只输出 JSON：{"pass": true|false, "issues": ["问题1", "问题2"]}。无问题时 pass=true 且 issues 为空。"""


class LLMReflectionGuardrail:
    """An LLM-as-auditor guardrail. Non-deterministic; consumes LLM tokens."""

    name = "llm_reflection"
    severity = "blocker"

    def __init__(self, llm: LLMProvider, checklist: str | None = None,
                 temperature: float = 0.0):
        self.llm = llm
        self.checklist = checklist or DEFAULT_CHECKLIST
        self.temperature = temperature
        self.total_tokens = 0
        self.last_raw: dict | None = None

    def check(self, ctx: GuardrailContext) -> Verdict:
        src_text = "\n".join(
            f"[{s.get('no')}] {s.get('source', '')}|{s.get('heading', '')}: {str(s.get('text', ''))[:120]}"
            for s in ctx.sources[:30])
        constraints = json.dumps({
            "intent": ctx.intent,
            "entities": ctx.entities,
            "budget_max": ctx.budget_max,
            "user_input": ctx.user_input,
        }, ensure_ascii=False)
        messages = [
            {"role": "system", "content": "你是严格的电商导购回答审查员。" + self.checklist},
            {"role": "user", "content": (
                f"[用户约束]\n{constraints}\n\n"
                f"[已检索资料]\n{src_text or '（无）'}\n\n"
                f"[待审查回答]\n{ctx.answer_md}")},
        ]
        data, tk = self.llm.chat_json(messages, temperature=self.temperature)
        self.last_raw = data
        self.total_tokens += tk
        passed = bool(data.get("pass", True))
        issues = data.get("issues") or []
        if passed:
            return Verdict.ok(self.name, "LLM自反思审查", {"tokens": tk})
        return Verdict.fail(
            self.name, "LLM自反思审查",
            "；".join(str(i) for i in issues) or "LLM 审查未通过",
            evidence={"tokens": tk, "raw": data})


CRITIC_SYSTEM = """你是一个独立的"质检 Critic Agent"，与生成回答的 Agent 完全分离（AutoGen/MetaGPT 范式）。
你不撰写回答，只做对抗性审查。请严格按顺序工作（最终只输出 JSON）：
1. 列出用户的硬约束：预算上限、必须覆盖的商品/在意维度、本题应拒答还是应回答；
2. 列出已检索资料实际支持的事实（商品、价格、引用编号）；
3. 逐类判定回答是否存在：超预算 / 库外实体(编造) / 越界引用 / 误拒答 / 维度遗漏 / 缺结构化卡片；
4. 给出最终 verdict 与必须打回修改的问题清单。
只输出 JSON：{"pass": true|false, "issues": ["问题1", "问题2"]}，无问题时 pass=true、issues 为空。"""


class CriticAgentGuardrail(LLMReflectionGuardrail):
    """A *separate* adversarial critic agent (AutoGen/MetaGPT-style). It uses an
    independent role, fuller evidence and a per-category judgement, making it the
    highest-cost arm; still bounded to a single repair round for fairness.
    """

    name = "critic_agent"

    def __init__(self, llm: LLMProvider, temperature: float = 0.0):
        super().__init__(llm, checklist=CRITIC_SYSTEM, temperature=temperature)

    def check(self, ctx: GuardrailContext) -> Verdict:
        # Independent agent: consumes the full evidence, not a shared summary.
        src_text = "\n".join(
            f"[{s.get('no')}] {s.get('source', '')}|{s.get('heading', '')}: {str(s.get('text', ''))}"
            for s in ctx.sources)
        constraints = json.dumps({
            "intent": ctx.intent,
            "entities": ctx.entities,
            "budget_max": ctx.budget_max,
            "concerns": ctx.concerns,
            "user_input": ctx.user_input,
        }, ensure_ascii=False)
        messages = [
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": (
                f"[用户硬约束]\n{constraints}\n\n"
                f"[已检索资料全文]\n{src_text or '（无）'}\n\n"
                f"[待审查回答]\n{ctx.answer_md}")},
        ]
        data, tk = self.llm.chat_json(messages, temperature=self.temperature)
        self.last_raw = data
        self.total_tokens += tk
        passed = bool(data.get("pass", True))
        issues = data.get("issues") or []
        if passed:
            return Verdict.ok(self.name, "独立Critic审查", {"tokens": tk})
        return Verdict.fail(
            self.name, "独立Critic审查",
            "；".join(str(i) for i in issues) or "独立 Critic 打回",
            evidence={"tokens": tk, "raw": data})
