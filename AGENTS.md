# CI Doctor: project contract

CI Doctor investigates failing CI runs. It parses JUnit XML, fingerprints failures, detects flaky tests from stored run history, and asks Gemini (primary) or Ollama to explain the root cause.

## Stack
Python 3.11+, uv, src layout (src/cidoctor), Typer + Rich, Pydantic v2, defusedxml, rapidfuzz, httpx, google-genai, pytest, ruff, mypy.

## Layout
core/ parsers/ providers/ agent/ storage/ integrations/ cli.py

## Fixed contracts (add fields only if needed, and report it)
- TestStatus: passed, failed, error, skipped
- TestResult: name, classname, status, duration_s, message, trace, fingerprint
- Run: id, commit, branch, created_at, results
- FailureGroup: fingerprint, count, sample_message, tests
- FlakyVerdict: test, runs, pass_rate, verdict (stable | flaky | failing | insufficient_data), confidence (0-1), reason
- Diagnosis: root_cause, category (test_bug | infra | dependency | flaky | unknown), suggested_fix, confidence (0-1), sources
- AnalysisReport: run, failure_groups, flaky_tests, diagnosis (optional)
- Parser protocol: name, can_parse(path), parse(path) -> list[TestResult]
- Provider protocol: name, diagnose(evidence) -> Diagnosis

## Rules for every block
1. Do only what the current block asks. Do not modify completed modules except to fix a bug, and report any such change.
2. Security: no shell=True, no eval, defusedxml for XML, never log secrets or API keys, validate all file paths.
3. Tests never touch the network. Mock Gemini, Ollama and GitHub.
4. Full type hints. Small modules. Docstrings on public functions.
5. Before reporting, run: uv run ruff check . && uv run mypy src && uv run pytest
6. A block is not done because the code compiles. Tests and acceptance criteria must pass.

## Report format
Files created or changed; decisions and deviations; test commands and results; acceptance checklist (pass/fail each item); known gaps.
