import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs) -> bool:
        return False


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _chunk(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _resolve_endpoint(cli_endpoint: str) -> str:
    """CLI > LANGSMITH_ENDPOINT > LANGCHAIN_ENDPOINT > US default."""
    for cand in [
        (cli_endpoint or "").strip(),
        (os.getenv("LANGSMITH_ENDPOINT") or "").strip(),
        (os.getenv("LANGCHAIN_ENDPOINT") or "").strip(),
    ]:
        if cand:
            return cand
    return "https://api.smith.langchain.com"


def main() -> None:
    # Ensure standalone script can read .env without requiring manual export.
    load_dotenv(dotenv_path=".env", override=True)

    parser = argparse.ArgumentParser(description="Upload dataset_F.jsonl to LangSmith dataset.")
    parser.add_argument("--dataset-name", default="F", help="LangSmith dataset name")
    parser.add_argument("--input", default="data/eval/dataset_F.jsonl", help="local dataset jsonl path")
    parser.add_argument("--description", default="Eval dataset F: AQ/aftersales/risk/memory/adversarial", help="dataset description")
    parser.add_argument("--batch-size", type=int, default=50, help="example upload batch size")
    parser.add_argument(
        "--langsmith-endpoint",
        default="",
        help="LangSmith API URL；优先级高于环境变量",
    )
    args = parser.parse_args()

    # Delayed import so users can still view help without dependency installed.
    from langsmith import Client
    from langsmith.utils import LangSmithAuthError

    key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or ""
    endpoint = _resolve_endpoint(args.langsmith_endpoint)
    if not key:
        raise RuntimeError(
            "LANGSMITH_API_KEY is empty. Please set it in env or .env before uploading."
        )
    client = Client(api_url=endpoint, api_key=key)

    masked_key = f"{key[:8]}...{key[-4:]}" if len(key) >= 12 else "***"
    print(f"langsmith_endpoint={endpoint}")
    print(f"langsmith_api_key={masked_key}")

    input_path = Path(args.input).resolve()
    rows = _read_jsonl(input_path)
    if not rows:
        raise RuntimeError(f"no rows found in {input_path}")

    dataset = None
    try:
        for ds in client.list_datasets(dataset_name=args.dataset_name):
            if ds.name == args.dataset_name:
                dataset = ds
                break
    except LangSmithAuthError as exc:
        raise RuntimeError(
            "LangSmith authentication failed. Check:\n"
            "1) LANGSMITH_API_KEY is valid and not revoked;\n"
            "2) LANGSMITH_ENDPOINT matches your workspace region (US/EU);\n"
            "3) No stale LANGCHAIN_API_KEY overrides current key.\n"
            f"raw_error={exc}"
        ) from exc
    if dataset is None:
        dataset = client.create_dataset(dataset_name=args.dataset_name, description=args.description)
        print(f"created dataset: {dataset.name} ({dataset.id})")
    else:
        print(f"using existing dataset: {dataset.name} ({dataset.id})")

    examples: List[Dict[str, Any]] = []
    for row in rows:
        sample_id = str(row.get("sample_id", ""))
        group = row.get("group", "")
        request_overrides = row.get("request_overrides", {}) if isinstance(row.get("request_overrides"), dict) else {}
        scenario = row.get("scenario", "")
        stress_tokens_chars = int(row.get("stress_tokens_chars", 0) or 0)
        examples.append(
            {
                "inputs": {
                    "sample_id": sample_id,
                    "group": group,
                    "query": row.get("query", ""),
                    "history": row.get("history", []),
                    "request_overrides": request_overrides,
                    "scenario": scenario,
                    "stress_tokens_chars": stress_tokens_chars,
                },
                "outputs": {
                    "reference_answer": row.get("reference_answer", ""),
                    "expected_route_target": row.get("expected_route_target", ""),
                    "risk_label": row.get("risk_label", ""),
                    "must_handoff": bool(row.get("must_handoff", False)),
                },
                "metadata": {
                    "sample_id": sample_id,
                    "group": group,
                },
            }
        )

    uploaded = 0
    for batch in _chunk(examples, size=max(1, args.batch_size)):
        client.create_examples(dataset_id=dataset.id, examples=batch)
        uploaded += len(batch)
        print(f"uploaded={uploaded}/{len(examples)}")

    print(f"done dataset={args.dataset_name} rows={len(examples)}")


if __name__ == "__main__":
    main()
