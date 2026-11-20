from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".json", ".txt", ".ini",
    ".ps1", ".sh", ".html", ".js", ".css", ".svg", ".toml",
}
SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub personal access token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", "target", "dist", "build", "htmlcov"}


def main() -> int:
    errors: list[str] = []

    if (ROOT / ".env").exists():
        errors.append(".env exists; remove local secrets and keep only .env.example")

    data_dir = ROOT / "data"
    if data_dir.exists():
        runtime_files = [p for p in data_dir.rglob("*") if p.is_file() and p.name != ".gitkeep"]
        if runtime_files:
            errors.append("data/ contains runtime files: " + ", ".join(str(p.relative_to(ROOT)) for p in runtime_files))

    if not (ROOT / ".env.example").is_file():
        errors.append(".env.example is missing")
    if not (ROOT / "data" / ".gitkeep").is_file():
        errors.append("data/.gitkeep is missing")

    report_path = ROOT / "docs" / "evaluation" / "rag-holdout-42-report.json"
    dataset_path = ROOT / "tests" / "fixtures" / "nexusops-rag-holdout-42.json"
    if report_path.is_file() and dataset_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            if report.get("datasetSha256") != dataset_hash:
                errors.append("RAG report datasetSha256 does not match the bundled 42-question holdout dataset")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"RAG evaluation report could not be validated: {exc}")
    else:
        errors.append("RAG holdout report or its matching fixture is missing")

    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        if path.name.startswith(".env.") and path.name != ".env.example":
            errors.append(f"unexpected environment file: {relative}")
        if path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".xlsx", ".gguf", ".pt", ".pth", ".zip"}:
            errors.append(f"generated/local artifact should not be committed: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES or path.name == ".env.example":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"possible {label} found in {relative}")

    if errors:
        print("Release hygiene check failed:")
        for error in errors:
            print(f"[FAIL] {error}")
        return 1

    print("[PASS] No local .env or runtime data files found.")
    print("[PASS] No known private-key/API-token patterns found in scanned source files.")
    print("[PASS] GitHub release hygiene checks completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
