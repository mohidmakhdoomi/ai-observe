"""Tool-free self-test tier for the agent-sessions capability (Spec 38).

Deterministic `unittest` modules that exercise the harness plumbing and oracle
logic **without any agent tool**. Named `selftest_*.py` (not `test_*.py`) so CI's
`ls test_*.py` glob and unittest's default `test*.py` discovery never enumerate
them — the CI-collected set stays byte-identical (Spec 38, M2). Run via
`python -m tests.agent_sessions --selftest` or, per module,
`python -m unittest tests.agent_sessions.selftest.selftest_harness`.
"""
