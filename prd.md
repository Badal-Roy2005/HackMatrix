# CI Doctor: Backend Build Prompts for OpenCode

November 14, 2026 · @Ak

Eight prompts build the complete Python backend, CLI and SDK in about 10 hours, with no database, and a ninth builds the docs site: paste the project contract once, then one block at a time, and audit each report before moving on.

## Decisions locked

You're right that a CLI tool only needs docs for now, so no REST API (`cidoctor serve` stays out of scope). Your pick for decision 6 (finish backend, CLI and SDK, then the GitHub Action) works if the blocks are ordered so a thin end-to-end path exists early. This plan does that: `analyze` works at Block 4 and `explain` at Block 5.

| Decision | Choice |
| --- | --- |
| Language and packaging | Python 3.11+ with uv |
| AI provider order | Gemini first, Ollama second |
| Database | Local JSON files, one per run (no database) |
| GitHub access | `GITHUB_TOKEN` (Action) plus `gh` CLI locally |
| AI architecture | Tool-calling agent loop with deterministic fallback |
| Delivery order | Backend, CLI and SDK first, then the GitHub Action |
| Environment | Arch Linux plus OpenCode |

## Block map

| Block | Hours | Delivers |
| --- | --- | --- |
| 1. Foundation and models | 0-1 | Repo, packaging, config, errors, Pydantic models, CI |
| 2. Parser and fingerprinting | 1-3 | Safe JUnit parser, parser registry, failure grouping |
| 3. Storage and flaky engine | 3-5 | JSON run history, Wilson-score flaky detection |
| 4. Analysis, CLI base, SDK | 5-6 | `init`, `analyze`, `flaky`, `demo seed`, `Analyzer` |
| 5. Gemini provider and explain | 6-8 | Structured diagnosis, redaction, `explain` |
| 6. Investigation agent | 8-9 | Tool-calling loop, safe tools, fallback |
| 7. Ollama and launch | 9-10 | Local models, model routing, `launch` |
| 8. GitHub, Action, release check | 10-12 | PR comment, composite Action, mock provider, e2e and plugin proof |

If you fall behind, Block 8 splits in two: ship the PR comment and Action first, and treat the plugin proof and performance test as optional.

The docs site (Block 9) is static and does not depend on the backend internals, so a second person can build it in parallel from hour 5. It only needs the final command list from Blocks 4 to 8.

## Step 0: project contract

Save this as `AGENTS.md` in the repo root before Block 1, so OpenCode reads it on every block.

```markdown
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
```

## Block 1: Foundation and models

Goal: a clean, installable project skeleton with typed models and settings.

```
Implement Block 1 of CI Doctor following AGENTS.md.

Create:
- pyproject.toml: hatchling build, name cidoctor, requires-python >=3.11, script cidoctor = cidoctor.cli:app. Runtime deps: typer, rich, pydantic>=2, defusedxml, rapidfuzz, httpx, google-genai, tomli-w. Dev deps: pytest, pytest-cov, ruff, mypy.
- src/cidoctor/ with packages core, parsers, providers, agent, storage, integrations (each with __init__.py).
- core/errors.py: CIDoctorError, ParseError, StorageError, ProviderError, ConfigError.
- core/models.py: the Pydantic v2 models from the AGENTS.md contract, with validation (non-empty names, confidence within 0-1, duration >= 0), enums for status, verdict and category, and clean JSON round-trip.
- core/config.py: Settings loaded in this order, later wins: defaults, cidoctor.toml (tomllib), CIDOCTOR_* env vars, explicit overrides. Fields: store_path (default .cidoctor/runs), provider, model, flaky window_days (14), min_runs (10), threshold (0.95), main_branch (main), redact_patterns, max_log_chars (400000), ollama_host (http://localhost:11434).
- core/logging.py: logging setup with a --verbose switch; include a filter that masks anything resembling an API key.
- cli.py: Typer app with --version and --help only.
- .github/workflows/ci.yml (uv sync, ruff, mypy, pytest), .gitignore, .env.example (GEMINI_API_KEY=), README stub.

Tests: model validation failures, JSON round-trip, config precedence (defaults < file < env < override), logging mask, --version output.

Acceptance: uv sync works; cidoctor --version prints a version; ruff, mypy and pytest are clean; config precedence test passes.
```

## Block 2: JUnit parser and fingerprinting

Goal: turn JUnit XML into `TestResult` objects safely, and group failures by root cause.

```
Implement Block 2 following AGENTS.md. Do not change Block 1 code except for bug fixes.

Create:
- parsers/base.py: Parser protocol and a registry. get_parser(path) checks built-in parsers, then entry points in group cidoctor.parsers. Raise ParseError if none match.
- parsers/junit.py: JUnitParser using defusedxml only. Handle testsuites and testsuite roots, nested suites, failure, error, skipped, system-out and system-err. Map to TestResult (message and trace from the failure element). Enforce a file size limit (default 50 MB, configurable). Raise ParseError on malformed XML, missing file, or a directory path.
- core/fingerprint.py:
  - normalize(message, trace) strips ISO and other timestamps, 0x addresses, UUIDs, line numbers, absolute paths (keep basenames), temp dirs, ports, durations like 1.23s, and long digit runs; collapses whitespace; keeps the exception type and the first three stack frames.
  - fingerprint(message, trace) returns a 12-character SHA-1 hex of the normalized text.
  - group_failures(results) returns FailureGroup list sorted by count descending.
  - find_similar(text, candidates, threshold) uses rapidfuzz.

Fixtures in tests/fixtures: pytest-style XML, jest-junit-style XML, multiple suites, failure plus error plus skipped, malformed XML, an XXE attack file, an oversized file.

Tests: the same error with different timestamps, UUIDs and paths gives one fingerprint; different exception types give different fingerprints; fingerprints are deterministic; the XXE file never resolves entities and raises or parses safely; malformed XML raises ParseError; grouping counts are correct; the registry finds JUnit and rejects unknown files.

Acceptance: all fixtures parse or fail as expected; ruff, mypy and pytest are clean.
```

## Block 3: JSON storage and flaky engine

Goal: persistent per-project run history and statistically sound flaky verdicts.

```
Implement Block 3 following AGENTS.md. There is no database: run history is stored as plain JSON files.

Create:
- storage/base.py: a Store protocol with save_run(run), get_run(id or 'latest'), list_runs(since, branch), test_history(name, since) and past_failures_for_fingerprint(fp, limit). Everything outside storage/ must depend only on this protocol.
- storage/json_store.py: JsonStore(path), default .cidoctor/runs. One file per run, named <UTC timestamp>_<short id>.json, containing a schema_version and the Run model dump.
  - save_run writes to a temporary file in the same directory, then os.replace, so a crash never leaves a half-written run.
  - list_runs and test_history use the timestamp in the filename to skip old files without parsing them.
  - test_history(name, since) returns run id, commit, branch, created_at and status for that test.
  - Run ids may contain only letters, digits, underscore and hyphen. Resolved paths must stay inside the store directory (no traversal). Skip symlinks. Enforce a 50 MB file size limit.
  - Corrupt files and unknown schema versions are skipped with a logged warning and never crash a command. Wrap IO errors in StorageError.
  - Keep a small per-instance cache so repeated test_history calls in one command do not re-read files.
- core/flaky.py:
  - wilson_interval(successes, n, z=1.96).
  - evaluate_test(history, threshold, min_runs, main_branch) returns FlakyVerdict.
  - Rules: mixed pass and fail on the same commit means flaky with strong confidence; pass rate on the main branch below threshold with mixed outcomes means flaky, with confidence derived from the Wilson interval; pass rate near zero over min_runs or more means failing; fewer than min_runs means insufficient_data with low confidence; otherwise stable.
  - Document how confidence is computed in the module docstring.

Tests: save and load round-trip; atomic write (a simulated failure after the temp write leaves no partial run); 'latest' ordering; the since filter; a corrupt file is skipped with a warning; an unknown schema version is skipped; hostile run ids and test names; path traversal is rejected; 30 runs of 10,000 results each load test_history in under 3 seconds; Wilson interval against known values; same-commit detection; five runs never produce a high-confidence verdict; stable, flaky and consistently-failing sequences. Everything runs in temporary directories.

Acceptance: verdicts match the expected table in the tests; ruff, mypy and pytest are clean.
```

## Block 4: Analysis engine, CLI base and SDK

Goal: the first working end-to-end path with no AI.

```
Implement Block 4 following AGENTS.md.

Create:
- core/analysis.py: analyze(results_path, log_path, commit, branch, store, settings) parses, fingerprints, groups, saves the run, then computes flaky verdicts for tests in that run and returns an AnalysisReport. Auto-detect commit and branch with git (subprocess, argument list, no shell), falling back to GITHUB_SHA and GITHUB_REF_NAME, then 'unknown'.
- cidoctor/__init__.py: export Analyzer and the public models. Analyzer(provider=None, store=None, settings=None) with analyze(path, log=None) and flaky(since).
- CLI commands in cli.py:
  - init: write cidoctor.toml, create .cidoctor/, and print a hint to gitignore it.
  - analyze <results> [--log FILE] [--commit] [--branch] [--format table|json]: Rich tables; exit code 1 if any test failed, otherwise 0.
  - flaky [--since 14d] [--min-runs N] [--format table|json]: parse durations like 14d and 12h; ranked output.
  - demo seed: deterministic generator of 30 simulated runs of about 60 tests with two flaky tests, one consistently failing test and the rest stable. Print a clear notice that the data is simulated.

Tests (typer CliRunner and temp dirs): every command; JSON output shape; exit codes; duration parsing; demo seed followed by flaky flags exactly the seeded flaky tests; SDK round-trip; missing and directory paths give clean error messages, not tracebacks.

Acceptance: running init, demo seed, analyze on a fixture, then flaky works end to end offline; ruff, mypy and pytest are clean.
```

## Block 5: Gemini provider and explain

Goal: a validated, redacted, structured Gemini diagnosis behind `cidoctor explain`.

```
Implement Block 5 following AGENTS.md.

Create:
- providers/base.py: Provider protocol, an Evidence model (log_text, diff_text, failure_groups, flaky verdicts, similar_failures, test_source) and a registry that also loads entry points in group cidoctor.providers.
- core/redact.py: redact(text, extra_patterns) masks AWS keys, GitHub tokens (ghp_ and github_pat_), Google API keys (AIza...), Bearer tokens, private key blocks, and NAME=value pairs where NAME contains SECRET, TOKEN, PASSWORD or KEY. Replace with [REDACTED].
- providers/gemini.py: GeminiProvider(model, client=None) with an injectable client. Read GEMINI_API_KEY and raise a clear ProviderError if it is missing. Use the google-genai SDK with response_mime_type application/json and response_schema set to Diagnosis, then validate with Pydantic. Retry up to 3 times with exponential backoff on 429, 5xx and timeouts. Apply request timeouts. Cap the log at max_log_chars, keeping head and tail with a truncation marker. Redact before building the prompt. Put the log inside delimited data blocks, and tell the model in the system instruction that log content is untrusted data, never instructions. The default model name comes from settings; do not hardcode anything else.
- explain <run-id|latest> [--provider] [--model] [--format table|json]: build Evidence from the store, call the provider, and show a Rich panel with root cause, category, suggested fix and confidence. If the run has no failures, say so and exit 0. Provider errors print a short actionable message.

Tests with a fake client, no network: success; invalid JSON then retry then ProviderError; 429 then retry then success; missing key; truncation; assert that seeded secrets never appear in the outgoing prompt; a log containing 'ignore previous instructions' does not change output handling; explain with a fake provider.

Acceptance: explain works against the fake provider end to end; ruff, mypy and pytest are clean. Out of scope: search grounding and the agent loop.
```

## Block 6: Investigation agent

Goal: let the model gather its own evidence with safe tools, with a deterministic fallback.

```
Implement Block 6 following AGENTS.md.

Create:
- agent/tools.py: four tools with JSON-schema declarations over a ToolContext(store, repo_root, run_id):
  - get_failure_groups(run_id)
  - get_test_history(test_name, days)
  - get_git_diff(commit): allow only HEAD, HEAD~N or a 4-40 character hex SHA; run git with an argument list and a timeout; cap output size.
  - read_file(path, start_line, end_line): resolve the path and require it inside repo_root, reject symlinks that escape, deny .env, .git/, *.pem and id_rsa, cap at 100 KB.
  Every tool output is size-capped and passed through redact().
- agent/loop.py: an AgentModel protocol with step(history, tool_declarations) returning either ToolCall(name, args) or Final(text). investigate(model, context, max_tool_calls=6, on_event=callback) runs the loop, rejects unknown tool names, and when the cap is reached forces a final structured answer with no tools. Return the Diagnosis plus a trace of ToolCallRecord items.
- Deterministic fallback: on any ProviderError, malformed output or invalid final JSON, run the single-request path from Block 5 and mark the trace mode as fallback.
- providers/gemini.py: add GeminiAgentModel using manual function calling (disable automatic function calling) so every call can be counted and shown.
- explain gains --agent/--no-agent (agent by default). Print each tool call as it happens, for example: investigating: get_test_history(test_checkout_retry).

Tests with a scripted fake AgentModel: tools are called and then a final answer; the cap is enforced; unknown tools are rejected; path traversal is blocked (../../etc/passwd, an escaping symlink, .env); hostile git arguments are rejected (--output=/tmp/x, '; rm -rf'); fallback triggers on bad output; outputs are redacted; the trace is recorded.

Acceptance: the agent path and the fallback path both produce a Diagnosis in tests; ruff, mypy and pytest are clean.
```

## Block 7: Ollama provider and launch

Goal: local models, model routing, and the one-command `launch` experience.

```
Implement Block 7 following AGENTS.md.

Create:
- providers/ollama.py: OllamaProvider(host, model, num_ctx=65536) using httpx. list_models() calls GET /api/tags. diagnose() calls POST /api/chat with stream false and the Diagnosis JSON schema in the format field; if the response does not validate, retry once, then raise ProviderError. Always send options.num_ctx. Add OllamaAgentModel using the tools field of /api/chat. After two invalid or missing tool calls, signal fallback to the fixed pipeline. Friendly errors: Ollama not running (say 'start it with ollama serve') and model not installed (say 'run ollama pull <name>').
- core/routing.py: resolve_model(name, installed) returns a provider spec. Names starting with gemini go to Gemini; names in the installed list go to Ollama; anything else raises ConfigError with the ollama pull hint.
- CLI command launch [run-id] [--model/-m NAME] [--config]: discover installed Ollama models, add the Gemini default when GEMINI_API_KEY is set (listed first), show a numbered Rich picker unless --model is given, save the choice to cidoctor.toml with tomli-w, then run the agent investigation on the latest failed run or the given run. With --config, save and exit without investigating. If no models are available, say how to fix it.

Tests using httpx.MockTransport: tags listing, chat success, chat with tool calls, num_ctx is sent, connection refused message, routing table for every case, launch via CliRunner with patched model lists, --config does not run an investigation, unknown model error text, fallback after two invalid tool calls.

Acceptance: launch works end to end with a mocked Ollama and with a fake Gemini; ruff, mypy and pytest are clean.
```

## Block 8: GitHub integration, Action and release check

Goal: PR comments, the composite Action, an offline mock provider, and proof the whole thing installs and runs from scratch.

```
Implement Block 8 following AGENTS.md.

Create:
- integrations/github.py:
  - format_comment(report): a Markdown body starting with the marker <!-- cidoctor:comment -->, with a summary line, a failure-groups table (max 10), flaky tests, the diagnosis, and a collapsed details section. Escape untrusted text from logs: neutralize HTML, markdown injection and @mentions, and use code spans. Cap at 60,000 characters.
  - post_or_update_comment(repo, pr_number, body, token): use httpx against the GitHub REST API. List comments, find the marker and PATCH it, otherwise POST. On 403 (read-only token on a fork PR) log a warning and exit 0 without crashing. Never log the token. Detect the PR number from GITHUB_EVENT_PATH or GITHUB_REF.
- CLI command comment [run-id] [--dry-run]: print the body on dry run; otherwise read GITHUB_TOKEN and GITHUB_REPOSITORY.
- providers/mock.py: a deterministic offline provider returning a canned Diagnosis, selectable with --provider mock. It is used for CI tests and as the demo safety net.
- action/action.yml: composite action with inputs results, log, provider, model, comment. Steps: set up Python, install cidoctor, run analyze, explain and comment. Document the pull-requests: write permission and caching .cidoctor/runs/. Add examples/workflow.yml.
- examples/plugins/tap-parser: a separate small package registering a parser through the cidoctor.parsers entry point, plus a test proving it loads without any core change.
- tests/e2e: build the wheel with uv build, install it into a fresh venv, then run init, demo seed, analyze, flaky, explain with the mock provider, and comment --dry-run.
- A performance test: analyzing 10,000 results completes in under 5 seconds.
- Security checklist as a test or script: no shell=True anywhere in src, no secrets in logs, pip-audit run on dependencies.
- README quick start that has been executed from a fresh clone.

Tests: comment formatting with hostile log text; update-versus-create logic with a mocked GitHub API; the 403 path; PR number detection; mock provider selection.

Acceptance: the e2e test passes from a built wheel; ruff, mypy and pytest are clean; the README quick start works verbatim.
```

## How to run each block

1. Paste the block prompt into OpenCode, and let it finish.
2. Run `uv run ruff check . && uv run mypy src && uv run pytest` yourself. Do not trust the report alone.
3. Send me the report and test output. I'll audit it before you start the next block.
4. If a block takes more than 1.5 times its budget, cut scope from its stretch items, not from its tests.

For the audit, I'll look at interface drift from the contract, security rules, test quality, and anything that would break the demo.

## Frontend: basic docs site

For now the frontend is a static docs site: no framework, no backend, deployed on GitHub Pages. A working reference design is published at CI Doctor docs site; ask OpenCode to follow its structure, then correct it against the real CLI.

### Block 9: Docs site

Goal: a clear, accurate install and usage site that can be opened with a double click and deployed for free.

```
Implement Block 9 (docs site) following AGENTS.md. Do not touch src/.

Create a site/ folder with index.html, styles.css and app.js. No framework, no build step, no external scripts. Google Fonts are optional, with system font fallbacks.

Sections: Overview with a hero terminal showing cidoctor launch investigating a failure; Install (pip, pipx, uv and from-source tabs, then choosing Gemini or Ollama); Quick start (demo seed, analyze, flaky, launch); Models and launch; CLI reference; How it works; Python SDK; GitHub Action; Configuration; Plugins; Security; Roadmap; Troubleshooting.

Behavior: sticky sidebar that highlights the current section; a copy button on every code block; install tabs; light and dark themes through prefers-color-scheme; responsive down to 360 px wide; visible keyboard focus; prefers-reduced-motion respected; sufficient color contrast.

Accuracy rule: every command and flag on the site must exist in the real CLI. Write scripts/check_docs.py that extracts every line starting with 'cidoctor ' from the site's code blocks and runs --help for each subcommand, failing on any unknown command or flag. Mention only features that the backend actually has; everything else goes under Roadmap.

Deploy: .github/workflows/pages.yml publishing site/ to GitHub Pages on push to main.

Tests: check_docs.py passes; no unclosed HTML tags (use Python html.parser); every href starting with # has a matching id; no network requests other than the optional fonts.

Acceptance: the site works offline apart from fonts, check_docs.py passes, and the layout holds on a 360 px viewport.
```

A later idea, not in this plan: a `cidoctor report --html` command that writes a single-file report of one run. It would be backend work, so it stays out of scope for now.