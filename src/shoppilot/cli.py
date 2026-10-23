"""Command-line interface for ShopPilot.

Examples
--------
  # One-shot question with the default deterministic guardrails
  shoppilot ask "500元内降噪耳机推荐"

  # Machine-readable output (guardrail verdict + token cost)
  shoppilot ask "对比蓝牙耳机A和蓝牙耳机F的音质" --json

  # Ablation arms (see eval/): no guardrails, or LLM self-reflection
  shoppilot ask "..." --guardrail-mode none
  shoppilot ask "..." --guardrail-mode llm_reflection

  # HTTP API
  shoppilot serve --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import json
import sys

from shoppilot import __version__
from shoppilot.core.llm import build_default_llm
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent

MODES = ["deterministic", "none", "vanilla", "llm_reflection"]


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--guardrail-mode", choices=MODES, default="deterministic",
                   help="输出护栏策略（消融实验三臂）")
    p.add_argument("--no-memory", action="store_true", help="关闭跨轮记忆")


def _build_agent(args):
    return build_ecommerce_agent(
        llm=build_default_llm(),
        guardrail_mode=args.guardrail_mode,
        enable_memory=not args.no_memory)


def _print_human(result) -> None:
    print("\n" + result.answer_md + "\n")
    if result.structured:
        print(f"[结构化卡片] {result.structured.get('kind')}")
    g = result.guardrail
    status = "通过" if g.get("passed") else ("已修复" if result.guardrail_repaired else "未解决")
    print(f"[护栏] {status} | blockers={g.get('blockers')} warnings={g.get('warnings')} "
          f"修复轮次={result.guardrail_repair_rounds} 护栏额外tokens={result.guardrail_extra_tokens}")
    if result.degradations:
        print(f"[降级] {'; '.join(result.degradations)}")
    print(f"[成本] tokens={result.tokens}  耗时={result.duration_s:.1f}s")


def cmd_ask(args) -> int:
    agent = _build_agent(args)
    result = agent.run(args.query)
    if args.json:
        print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if not result.guardrail_unresolved else 2


def cmd_repl(args) -> int:
    agent = _build_agent(args)
    print(f"ShopPilot {__version__}  输入 exit/quit 退出")
    while True:
        try:
            query = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if query.lower() in {"exit", "quit", "q"}:
            return 0
        if not query:
            continue
        result = agent.run(query)
        _print_human(result)


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("shoppilot.serving.api:app", host=args.host, port=args.port,
                reload=False)
    return 0


def cmd_eval(args) -> int:
    from eval import run as eval_run

    argv: list[str] = []
    if args.all:
        argv.append("--all")
    elif args.strategy:
        argv += ["--strategy", args.strategy]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    if args.dry_run:
        argv.append("--dry-run")
    return eval_run.main(argv) or 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shoppilot", description="ShopPilot 电商导购智能体")
    parser.add_argument("--version", action="version", version=f"ShopPilot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="单次问答")
    p_ask.add_argument("query", help="导购问题")
    p_ask.add_argument("--json", action="store_true", help="输出 JSON")
    _add_common(p_ask)
    p_ask.set_defaults(func=cmd_ask)

    p_repl = sub.add_parser("repl", help="多轮交互")
    _add_common(p_repl)
    p_repl.set_defaults(func=cmd_repl)

    p_serve = sub.add_parser("serve", help="启动 FastAPI 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_eval = sub.add_parser("eval", help="输出护栏四臂对照实验")
    p_eval.add_argument("--strategy",
                        choices=["vanilla", "llm_reflection", "multi_agent", "deterministic"])
    p_eval.add_argument("--all", action="store_true", help="跑全部臂")
    p_eval.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    p_eval.add_argument("--dry-run", action="store_true", help="离线 ScriptedLLM 冒烟（零 token）")
    p_eval.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
