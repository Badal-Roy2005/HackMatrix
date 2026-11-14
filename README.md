# CI Doctor

CI Doctor investigates failing CI runs. It parses JUnit XML, fingerprints
failures, detects flaky tests from stored run history, and asks Gemini
(primary) or Ollama to explain the root cause.

## Stack

Python 3.11+, uv, src layout (`src/cidoctor`), Typer + Rich, Pydantic v2,
defusedxml, rapidfuzz, httpx, google-genai, pytest, ruff, mypy.

## Install

```sh
# From source
git clone <repo>
cd ci-doctor
uv sync --all-groups
```

Or install from PyPI:

```sh
pip install cidoctor
```

## Quick start

```sh
# 1. Initialize the project
cidoctor init

# 2. Set your Gemini API key (get one from https://aistudio.google.com/)
export GEMINI_API_KEY=your-key

# 3. Generate simulated demo data (clearly marked as simulated)
cidoctor demo seed

# 4. Analyze a JUnit results file
cidoctor analyze test-results.xml --commit abc123 --branch main

# 5. Check for flaky tests
cidoctor flaky --since 14d

# 6. Explain the root cause (Gemini, default)
cidoctor explain latest

# 7. Use Ollama instead
cidoctor launch --model llama3

# 8. Post a PR comment (dry run first)
cidoctor comment latest --dry-run
```

## Configuration

Create a `cidoctor.toml`:

```toml
[cidoctor]
store_path = ".cidoctor/runs"
provider = "gemini"
model = "gemini-1.5-flash"
window_days = 14
min_runs = 10
threshold = 0.95
main_branch = "main"
max_log_chars = 400000
ollama_host = "http://localhost:11434"
```

## Commands

| Command | Description |
|---------|-------------|
| `cidoctor init` | Write config and create `.cidoctor/` directory |
| `cidoctor analyze <file>` | Analyze a JUnit results file |
| `cidoctor flaky` | Show flaky-test verdicts |
| `cidoctor explain <run-id\|latest>` | Explain root cause (Gemini by default) |
| `cidoctor launch [<run-id>]` | Pick a model and run the investigation agent |
| `cidoctor comment <run-id> [--dry-run]` | Post a PR comment |
| `cidoctor demo seed` | Generate simulated demo data |

## Security

- All XML parsing uses `defusedxml` (no XXE)
- Secrets (API keys, tokens) are redacted before logging or sending to AI providers
- No `shell=True` anywhere in the source
- File paths are validated to prevent traversal

## Project contract

See [AGENTS.md](AGENTS.md) for the full project contract, fixed data contracts,
and per-block implementation rules.
