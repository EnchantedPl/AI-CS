import os
import threading
from typing import List, Tuple

from app.models.litellm_client import embed_texts_with_litellm

_SHARED_LOCAL_MODELS = {}
_SHARED_LOCAL_MODELS_LOCK = threading.Lock()


def get_shared_local_embedding_model(model_name: str):
    """Process-level singleton for local embedding models.

    Multiple components (cache/memory/rag) all need embeddings. Reusing the same
    model instance avoids repeated HuggingFace model loading and startup timeouts.
    """
    target = (model_name or "").strip()
    if not target:
        target = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model = _SHARED_LOCAL_MODELS.get(target)
    if model is not None:
        return model

    # Guard model initialization to avoid concurrent HuggingFace startup races.
    with _SHARED_LOCAL_MODELS_LOCK:
        model = _SHARED_LOCAL_MODELS.get(target)
        if model is None:
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            _SHARED_LOCAL_MODELS[target] = HuggingFaceEmbedding(model_name=target)
            model = _SHARED_LOCAL_MODELS[target]
    return model


class EmbeddingRuntime:
    def __init__(self) -> None:
        self._local_model = None
        self._local_model_name = None

    def _active_mode(self) -> str:
        return os.getenv("EMBEDDING_MODE", "cloud").strip().lower()

    def _active_provider(self) -> str:
        if self._active_mode() == "local":
            return os.getenv("LOCAL_EMBEDDING_PROVIDER", "huggingface")
        return os.getenv("CLOUD_EMBEDDING_PROVIDER", "litellm")

    def _active_model(self) -> str:
        if self._active_mode() == "local":
            return os.getenv(
                "LOCAL_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            )
        return os.getenv("CLOUD_EMBEDDING_MODEL", "openai/text-embedding-v3")

    def _active_dim(self) -> int:
        if self._active_mode() == "local":
            return int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))
        return int(os.getenv("CLOUD_EMBEDDING_DIM", "1024"))

    def _get_local_model(self):
        name = self._active_model()
        if self._local_model is None or self._local_model_name != name:
            self._local_model = get_shared_local_embedding_model(name)
            self._local_model_name = name
        return self._local_model

    def embed_texts(self, texts: List[str]) -> Tuple[List[List[float]], str]:
        provider = self._active_provider()
        model_name = self._active_model()
        if provider == "huggingface":
            model = self._get_local_model()
            return [model.get_text_embedding(t) for t in texts], model_name
        result = embed_texts_with_litellm(texts)
        return result["embeddings"], str(result.get("model") or model_name)

    def embed_query(self, query: str) -> Tuple[List[float], str]:
        vectors, model_name = self.embed_texts([query])
        return vectors[0], model_name

    def active_vector_column(self) -> str:
        return "embedding_local" if self._active_mode() == "local" else "embedding_cloud"

    def active_mode(self) -> str:
        return self._active_mode()

    def active_dim(self) -> int:
        return self._active_dim()
