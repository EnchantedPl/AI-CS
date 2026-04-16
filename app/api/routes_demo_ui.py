from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["demo-ui"])

_HTML_PATH = Path(__file__).resolve().parents[1] / "static" / "agent_console.html"


@router.get("/demo/agent-console", response_class=HTMLResponse)
async def agent_console_page() -> HTMLResponse:
    return HTMLResponse(content=_HTML_PATH.read_text(encoding="utf-8"))

