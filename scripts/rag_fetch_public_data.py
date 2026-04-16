import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _cleanup_generated_docs(output_dir: Path) -> None:
    for path in output_dir.glob("public_*.md"):
        path.unlink(missing_ok=True)


def _write_docs(output_dir: Path, records: List[Dict[str, str]], prefix: str) -> int:
    count = 0
    for idx, rec in enumerate(records):
        title = rec.get("title", f"{prefix}-{idx}")
        question = rec.get("question", "").strip()
        context = rec.get("context", "").strip()
        answer = rec.get("answer", "").strip()
        if not (question and context):
            continue
        content = (
            f"# 中文客服问答样本 {idx}\n\n"
            f"## 标题\n{title}\n\n"
            f"## 用户问题\n{question}\n\n"
            f"## 参考上下文\n{context}\n\n"
            f"## 标准答复\n{answer}\n"
        )
        (output_dir / f"{prefix}_{idx:04d}.md").write_text(content, encoding="utf-8")
        count += 1
    return count


def _load_sina_kefu(load_dataset) -> List[Dict[str, str]]:
    # Public Chinese customer service QA dataset.
    ds = load_dataset("a2231698193/sina-kefu-dataset", split="train")
    out: List[Dict[str, str]] = []
    for i, row in enumerate(ds):
        question = (row.get("Question") or row.get("question") or "").strip()
        answer = (row.get("Response") or row.get("response") or "").strip()
        cot = (row.get("Complex_CoT") or "").strip()
        if not question:
            continue
        out.append(
            {
                "title": f"sina-kefu-{i}",
                "question": question,
                "context": cot if cot else answer,
                "answer": answer,
            }
        )
    return out


def _load_cmrc(load_dataset, max_rows: int = 500) -> List[Dict[str, str]]:
    # Fallback / supplement: public Chinese QA dataset.
    ds = load_dataset("clue", "cmrc2018", split=f"train[:{max_rows}]")
    out: List[Dict[str, str]] = []
    for i, row in enumerate(ds):
        question = (row.get("question") or "").strip()
        context = (row.get("context") or "").strip()
        answers = row.get("answers", {})
        answer_list = answers.get("text", []) if isinstance(answers, dict) else []
        answer = answer_list[0].strip() if answer_list else ""
        if not (question and context):
            continue
        out.append(
            {
                "title": (row.get("title") or f"cmrc-{i}").strip(),
                "question": question,
                "context": context,
                "answer": answer,
            }
        )
    return out


def main() -> None:
    """Fetch Chinese public QA datasets and convert to local KB docs."""
    try:
        from datasets import load_dataset
    except Exception as exc:
        raise RuntimeError("Please install datasets first: pip install datasets") from exc

    output_dir = Path("data/kb")
    output_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_generated_docs(output_dir)

    total = 0
    try:
        kefu_records = _load_sina_kefu(load_dataset)
        written = _write_docs(output_dir, kefu_records, prefix="public_cs")
        total += written
        print(f"Loaded sina-kefu records: {written}")
    except Exception as exc:
        print(f"[warn] failed to load sina-kefu dataset: {exc}")

    try:
        cmrc_records = _load_cmrc(load_dataset, max_rows=500)
        written = _write_docs(output_dir, cmrc_records, prefix="public_cmrc")
        total += written
        print(f"Loaded cmrc records: {written}")
    except Exception as exc:
        print(f"[warn] failed to load cmrc dataset: {exc}")

    print(f"Generated Chinese public docs into {output_dir}, total files: {total}")


if __name__ == "__main__":
    main()

