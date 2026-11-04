"""Language accuracy evals for the order-parsing LLM call.

Separate from the pytest suite in tests/ because these call a real provider
and cost real API quota. Run with `uv run python -m evals.run --provider ...`.
"""
