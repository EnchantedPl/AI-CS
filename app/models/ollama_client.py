from typing import Any, Dict

from app.models.litellm_client import generate_answer_with_litellm


def generate_answer_with_ollama(
    *,
    query: str,
    route_target: str,
    rag_context: str,
    tool_result: Dict[str, Any],
) -> str:
    """Backward-compatible wrapper. Internally routed by LiteLLM."""
    result = generate_answer_with_litellm(
        query=query,
        route_target=route_target,
        rag_context=rag_context,
        tool_result=tool_result,
    )
    return result["answer"]

