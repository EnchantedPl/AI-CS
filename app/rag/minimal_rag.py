import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class RagChunk:
    file_name: str
    chunk_id: str
    text: str


def _tokenize(text: str) -> List[str]:
    # Keep it simple: chinese phrase blocks + english/number tokens.
    return re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text.lower())


def _score(query_tokens: List[str], chunk_tokens: List[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    qset = set(query_tokens)
    cset = set(chunk_tokens)
    overlap = len(qset.intersection(cset))
    return overlap / max(1, len(qset))


class MinimalRagRetriever:
    """A tiny no-dependency RAG retriever for local demo."""

    def __init__(self, kb_dir: str = "data/kb") -> None:
        self.kb_dir = Path(kb_dir)
        self._chunks: List[RagChunk] = []
        self._loaded = False

    def _split_text(self, text: str) -> List[str]:
        # Paragraph-first split, then fallback fixed window.
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paras:
            return []
        out: List[str] = []
        for para in paras:
            if len(para) <= 240:
                out.append(para)
            else:
                for i in range(0, len(para), 220):
                    out.append(para[i : i + 240])
        return out

    def _load_chunks(self) -> None:
        self._chunks = []
        if not self.kb_dir.exists():
            self._loaded = True
            return
        files = sorted(self.kb_dir.glob("*.md")) + sorted(self.kb_dir.glob("*.txt"))
        for file_path in files:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for idx, chunk in enumerate(self._split_text(text)):
                self._chunks.append(
                    RagChunk(
                        file_name=file_path.name,
                        chunk_id=f"{file_path.stem}-{idx}",
                        text=chunk,
                    )
                )
        self._loaded = True

    def retrieve(self, query: str, top_k: int = 3) -> Dict[str, object]:
        if not self._loaded:
            self._load_chunks()
        q_tokens = _tokenize(query)
        ranked = []
        for chunk in self._chunks:
            score = _score(q_tokens, _tokenize(chunk.text))
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda x: x[0], reverse=True)
        selected = ranked[:top_k]

        chunks = [
            {
                "score": round(score, 4),
                "file_name": chunk.file_name,
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
            }
            for score, chunk in selected
        ]
        citations = [f"{c['file_name']}#{c['chunk_id']}" for c in chunks]
        context = "\n".join([c["text"] for c in chunks])
        return {
            "enabled": True,
            "top_k": top_k,
            "chunks": chunks,
            "citations": citations,
            "context": context,
        }


RAG_RETRIEVER = MinimalRagRetriever()

