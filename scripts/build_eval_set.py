import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _extract_section(text: str, title: str) -> Optional[str]:
    pattern = rf"## {re.escape(title)}\n(.*?)(?:\n## |\Z)"
    m = re.search(pattern, text, flags=re.S)
    if not m:
        return None
    return m.group(1).strip()


def _domain_from_query(query: str) -> str:
    lowered = query.lower()
    if any(k in lowered for k in ["退款", "退货", "refund", "return"]):
        return "aftersales"
    if any(k in lowered for k in ["价格", "规格", "参数", "price", "spec"]):
        return "product_info"
    if any(k in lowered for k in ["法律", "投诉", "risk", "legal", "合规"]):
        return "risk_query"
    return "faq"


def _parse_cmrc_file(path: Path) -> Optional[Dict[str, Any]]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    query = _extract_section(content, "用户问题")
    answer = _extract_section(content, "标准答复")
    if not query or not answer:
        return None
    return {
        "query": query,
        "gold_answer": answer,
        "gold_doc_ids": [path.name],
        "domain": _domain_from_query(query),
        "source_type": "cmrc",
    }


def _parse_customer_service_demo(path: Path) -> List[Dict[str, Any]]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    section_map = {
        "退款进度": "退款申请通过后多久到账？",
        "退货规则": "七天无理由退货有哪些条件？",
        "商品信息咨询": "商品价格和库存在哪里看？",
        "风险与合规": "涉及法律投诉的问题应该怎么处理？",
    }
    samples: List[Dict[str, Any]] = []
    for section, query in section_map.items():
        answer = _extract_section(content, section)
        if not answer:
            continue
        samples.append(
            {
                "query": query,
                "gold_answer": answer,
                "gold_doc_ids": [path.name],
                "domain": _domain_from_query(query),
                "source_type": "customer_service_demo",
            }
        )
    return samples


def build_eval_set(kb_dir: Path, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    demo_file = kb_dir / "customer_service_demo.md"
    if demo_file.exists():
        rows.extend(_parse_customer_service_demo(demo_file))

    cmrc_files = sorted(kb_dir.glob("public_cmrc_*.md"))
    for f in cmrc_files:
        item = _parse_cmrc_file(f)
        if item:
            rows.append(item)
        if limit > 0 and len(rows) >= limit:
            break

    if limit > 0:
        rows = rows[:limit]
    return rows


def _resolve_gold_chunks(rows: List[Dict[str, Any]], topn: int) -> List[Dict[str, Any]]:
    try:
        import psycopg
    except Exception:
        return rows

    pg_host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    pg_port = int(os.getenv("POSTGRES_PORT", "5433"))
    pg_db = os.getenv("POSTGRES_DB", "ai_cs")
    pg_user = os.getenv("POSTGRES_USER", "postgres")
    pg_password = os.getenv("POSTGRES_PASSWORD", "postgres")
    table = os.getenv("RAG_TABLE_NAME", "kb_chunks")
    kb_version = os.getenv("KB_VERSION", "v1")

    sql = f"""
        SELECT chunk_id
        FROM {table}
        WHERE is_active = TRUE
          AND kb_version = %s
          AND source_name = %s
          AND (
              content ILIKE %s
              OR content ILIKE %s
          )
        LIMIT %s;
    """
    sql_fallback = f"""
        SELECT chunk_id
        FROM {table}
        WHERE is_active = TRUE
          AND kb_version = %s
          AND source_name = %s
        LIMIT %s;
    """

    with psycopg.connect(
        host=pg_host,
        port=pg_port,
        dbname=pg_db,
        user=pg_user,
        password=pg_password,
        autocommit=True,
    ) as conn, conn.cursor() as cur:
        for row in rows:
            source_names = row.get("gold_doc_ids", []) or []
            source_name = source_names[0] if source_names else ""
            answer = str(row.get("gold_answer", "") or "").strip()
            query = str(row.get("query", "") or "").strip()
            answer_snippet = answer[:32] if answer else ""
            query_snippet = query[:24] if query else ""
            ids: List[str] = []
            if source_name:
                cur.execute(
                    sql,
                    (
                        kb_version,
                        source_name,
                        f"%{answer_snippet}%",
                        f"%{query_snippet}%",
                        topn,
                    ),
                )
                ids = [str(r[0]) for r in cur.fetchall()]
                if not ids:
                    cur.execute(sql_fallback, (kb_version, source_name, topn))
                    ids = [str(r[0]) for r in cur.fetchall()]
            row["gold_chunk_ids"] = ids
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build eval_set.jsonl from KB markdown files.")
    parser.add_argument("--kb-dir", default="data/kb", help="KB markdown directory")
    parser.add_argument("--output", default="data/eval/eval_set.jsonl", help="output jsonl path")
    parser.add_argument("--limit", type=int, default=500, help="max rows to output")
    parser.add_argument(
        "--align-chunk-gold",
        action="store_true",
        help="Align gold labels to DB chunk granularity using source_name+content matching.",
    )
    parser.add_argument("--chunk-gold-topn", type=int, default=3, help="Max gold chunks per sample")
    args = parser.parse_args()

    kb_dir = (PROJECT_ROOT / args.kb_dir).resolve()
    output = (PROJECT_ROOT / args.output).resolve()
    rows = build_eval_set(kb_dir, args.limit)
    if args.align_chunk_gold:
        rows = _resolve_gold_chunks(rows, topn=max(1, args.chunk_gold_topn))
    write_jsonl(output, rows)

    print(f"kb_dir={kb_dir}")
    print(f"rows={len(rows)}")
    if args.align_chunk_gold:
        covered = sum(1 for r in rows if r.get("gold_chunk_ids"))
        print(f"chunk_gold_covered={covered}")
    print(f"output={output}")


if __name__ == "__main__":
    main()
