import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.hybrid_retriever import RETRIEVER, to_json


def run_query(query: str, domain: str, mode: str) -> None:
    result = RETRIEVER.retrieve(query=query, domain=domain, retrieval_mode=mode)
    print(f"\n=== mode={mode} domain={domain} ===")
    print(to_json(result))


def main() -> None:
    query = "退款多久到账"
    domain = "aftersales"
    for mode in ["vector", "keyword", "hybrid"]:
        run_query(query=query, domain=domain, mode=mode)


if __name__ == "__main__":
    main()

