from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from ai_visual_agent.domain import PromptVersion


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class PromptRegistry:
    def __init__(self, prompt_dir: Path = PROMPT_DIR) -> None:
        self.prompt_dir = prompt_dir

    def get(self, name: str, include_content: bool = True) -> PromptVersion:
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            raise ValueError(f"Invalid prompt name: {name}")
        path = self.prompt_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")

        content = path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return PromptVersion(
            name=name,
            version=f"{name}@{content_hash[:12]}",
            content_hash=content_hash,
            path=str(path),
            content=content if include_content else "",
        )

    def list(self, include_content: bool = False) -> list[PromptVersion]:
        prompts: list[PromptVersion] = []
        for path in sorted(self.prompt_dir.glob("*.md")):
            prompts.append(self.get(path.stem, include_content=include_content))
        return prompts


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    return PromptRegistry()


def get_prompt_version(name: str, include_content: bool = True) -> PromptVersion:
    return get_prompt_registry().get(name, include_content=include_content)
