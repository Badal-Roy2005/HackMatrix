# CI Doctor

CI Doctor investigates failing CI runs. It parses JUnit XML, fingerprints
failures, detects flaky tests from stored run history, and asks Gemini
(primary) or Ollama to explain the root cause.

## Install

```sh
uv sync
```

## Usage

```sh
cidoctor --version
```

See the [PRD](prd.md) and [project contract](AGENTS.md) for the full
roadmap and design contracts.
