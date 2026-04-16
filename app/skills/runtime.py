import json
from pathlib import Path
from typing import Any, Dict, List


SKILL_REGISTRY_DIR = Path(__file__).resolve().parent / "registry"
SKILL_DOCS_DIR = Path(__file__).resolve().parent / "docs"


def load_skill_definition(skill_name: str) -> Dict[str, Any]:
    p = SKILL_REGISTRY_DIR / f"{skill_name}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_skill_doc(skill_name: str) -> str:
    p = SKILL_DOCS_DIR / f"{skill_name}.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def progressive_skills(step_idx: int) -> List[Dict[str, Any]]:
    # progressive disclosure: policy first, plan later
    names = ["refund_policy_skill"]
    if step_idx >= 1:
        names.append("aftersales_plan_skill")
    out: List[Dict[str, Any]] = []
    for n in names:
        d = load_skill_definition(n)
        if d:
            d = dict(d)
            d["doc"] = load_skill_doc(n)
            out.append(d)
    return out
