"""Filesystem locations used across the package."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
POLICY_DIR = SYNTHETIC_DIR / "policies"
DB_PATH = SYNTHETIC_DIR / "medicaidiq.duckdb"
KNOWLEDGE_PATH = SYNTHETIC_DIR / "knowledge_store.json"
RUNS_DIR = REPO_ROOT / "runs"
