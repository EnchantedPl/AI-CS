import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.hybrid_retriever import RETRIEVER, to_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest kb documents into pgvector.")
    parser.add_argument("--kb-dir", default="data/kb", help="KB documents directory")
    parser.add_argument("--target-chunks", type=int, default=2000, help="Max chunks to ingest")
    parser.add_argument(
        "--reset-table",
        action="store_true",
        help="Truncate kb table before ingest (recommended when changing dataset language)",
    )
    args = parser.parse_args()

    status = RETRIEVER.health()
    print("health(before):")
    print(to_json(status))

    if args.reset_table:
        reset_result = RETRIEVER.reset_table()
        print("reset table:")
        print(to_json(reset_result))

    result = RETRIEVER.ingest_kb(kb_dir=args.kb_dir, target_chunks=args.target_chunks)
    print("ingest result:")
    print(to_json(result))

    status_after = RETRIEVER.health()
    print("health(after):")
    print(to_json(status_after))


if __name__ == "__main__":
    main()

