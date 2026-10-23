"""Offline ablation harness comparing four output-checking strategies.

The four strategies share the same ReAct core, tools, model, temperature and
case set; only the final output check changes. All strategies are re-scored with
the *same* deterministic rules so the comparison uses one ruler.
"""
