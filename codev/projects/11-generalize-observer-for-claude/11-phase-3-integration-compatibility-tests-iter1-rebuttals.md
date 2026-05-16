# Rebuttal: phase-3-integration-compatibility-tests iteration 1

## Consultation status

- Codex completed review and requested changes.
- Claude consultation was attempted repeatedly but failed with API 500/internal server errors and did not produce a substantive review.
- Per architect instruction on 2026-05-16T18:07:06.073Z, this phase is proceeding with degraded consultation. A Claude-unavailable note was written to `11-phase-3-integration-compatibility-tests-iter1-claude.txt`.

## Codex findings and responses

### 1. Compatibility matrix missing some shared env aliases

Codex noted that Phase 3 required preferred-vs-legacy compatibility coverage for shared variables beyond the initially covered session/dir/disable/live/quiet paths, specifically strict parse, include-log-writes, symlink-dir allowance, and signal grace.

Accepted. I expanded `tests/test_observe_env.py` to cover:

- `AI_OBSERVE_STRICT_PARSE` preference over `CODEV_OBSERVE_STRICT_PARSE` via `env_flag`;
- `AI_OBSERVE_INCLUDE_LOG_WRITES` preference over `CODEV_OBSERVE_INCLUDE_LOG_WRITES` via `env_flag`;
- `AI_OBSERVE_SIGNAL_GRACE` preference over `CODEV_OBSERVE_SIGNAL_GRACE` via `env_value`;
- `AI_OBSERVE_ALLOW_SYMLINK_DIR` preference over `CODEV_OBSERVE_ALLOW_SYMLINK_DIR` with actual `prepare_logs()` symlink-dir behavior in both allowed and blocked directions.

I also expanded `tests/test_observe_cli.py` with an integration test showing `AI_OBSERVE_STRICT_PARSE=1` wins over `CODEV_OBSERVE_STRICT_PARSE=0` in an actual generic wrapper run with parser failure injection.

### 2. Codex wrapper execution with preferred `AI_OBSERVE_REAL_CODEX` was missing

Codex noted that existing Codex subprocess tests used legacy `CODEV_OBSERVE_REAL_CODEX`, while the preferred-path coverage was resolver-only.

Accepted. I added `test_codex_shim_runs_with_preferred_ai_real_codex` to `tests/test_observe_cli.py`, exercising `bin/codex` end-to-end with `AI_OBSERVE_REAL_CODEX`, fake strace, preferred observe dir/session/quiet variables, and wrapper execution.

## Validation after changes

Ran:

```bash
python3 -m unittest tests.test_observe_env tests.test_observe_cli
python3 -m unittest discover -s tests
```

Results:

- Focused env/CLI tests passed.
- Full discovery passed: `157 tests OK`.

## Result

All Codex-requested changes have been addressed. Claude was unavailable due to repeated API 500 failures; proceeding under architect-approved degraded consultation.
