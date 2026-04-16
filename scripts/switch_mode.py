import argparse
import sys
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def _read_env_lines(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f".env not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _upsert_env(lines: List[str], updates: Dict[str, str]) -> List[str]:
    pending = dict(updates)
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        for key, value in pending.items():
            out.append(f"{key}={value}")
    return out


def _mode_updates(mode: str) -> Dict[str, str]:
    # local: local LLM + local embedding
    # cloud: cloud LLM + cloud embedding
    # mix: local LLM + cloud embedding (demo latency-friendly default)
    mapping = {
        "local": {"LLM_MODE": "local", "EMBEDDING_MODE": "local"},
        "cloud": {"LLM_MODE": "cloud", "EMBEDDING_MODE": "cloud"},
        "mix": {"LLM_MODE": "local", "EMBEDDING_MODE": "cloud"},
    }
    if mode not in mapping:
        raise ValueError(f"Unsupported mode={mode}. expected one of: local, cloud, mix")
    return mapping[mode]


def main() -> None:
    parser = argparse.ArgumentParser(description="Switch LLM/Embedding runtime mode in .env.")
    parser.add_argument(
        "--mode",
        choices=["local", "cloud", "mix"],
        required=True,
        help="local=all local, cloud=all cloud, mix=local llm + cloud embedding",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show resulting key-values without writing file.",
    )
    args = parser.parse_args()

    updates = _mode_updates(args.mode)
    lines = _read_env_lines(ENV_PATH)
    new_lines = _upsert_env(lines, updates)

    print("switch target:")
    for key, value in updates.items():
        print(f"  {key}={value}")

    if args.dry_run:
        return

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"updated: {ENV_PATH}")
    print("next steps:")
    print("  1) restart FastAPI service")
    print("  2) call /debug/llm-health and /chat for verification")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"switch_mode failed: {exc}")
        sys.exit(1)
