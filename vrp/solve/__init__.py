"""Solver adapters. SDD §7.3 — a portfolio, not a single engine.

Each adapter compiles a `Problem` into an engine's own model and maps the result
back, carrying the engine's timings so the independent verifier judges the
engine rather than agreeing with our evaluator.
"""
