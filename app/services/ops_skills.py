from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class OpsSkill:
    name: str
    description: str
    categories: tuple[str, ...]
    body: str
    path: Path

    def prompt_context(self) -> str:
        return f"[运维 Skill: {self.name}]\n{self.description}\n{self.body.strip()}"


class OpsSkillLibrary:
    """Small, auditable file-backed Skill library for incident diagnosis."""

    @staticmethod
    def root() -> Path:
        return Path(__file__).resolve().parents[2] / "skills" / "ops"

    @classmethod
    def list_skills(cls) -> list[OpsSkill]:
        skills: list[OpsSkill] = []
        root = cls.root()
        if not root.exists():
            return skills
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                skills.append(cls._load(path))
            except (OSError, ValueError):
                continue
        return skills

    @classmethod
    def context_for(cls, category: str, incident_text: str = "") -> str:
        category = (category or "UNKNOWN").upper()
        text = (incident_text or "").lower()
        ranked: list[tuple[int, OpsSkill]] = []
        category_names = {"DATABASE", "CACHE", "API", "NETWORK", "DEPLOYMENT", "RESOURCE", "SECURITY", "MIDDLEWARE", "UNKNOWN"}
        for skill in cls.list_skills():
            declared = set(skill.categories)
            category_match = category in declared and category in category_names
            keyword_match = any(
                keyword.lower() in text
                for keyword in skill.categories
                if keyword.upper() not in category_names | {"ALL"}
            )
            score = 3 if category_match else 2 if keyword_match else 1 if "ALL" in declared else 0
            if score:
                ranked.append((score, skill))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        selected = [item for _, item in ranked[:3]]
        return "\n\n".join(item.prompt_context() for item in selected)

    @staticmethod
    def _load(path: Path) -> OpsSkill:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            raise ValueError(f"Missing frontmatter: {path}")
        end = raw.find("\n---", 4)
        if end < 0:
            raise ValueError(f"Invalid frontmatter: {path}")
        metadata: dict[str, str] = {}
        for line in raw[4:end].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip("\"'")
        name = metadata.get("name", path.parent.name)
        description = metadata.get("description", "Operational diagnostic guidance")
        categories = tuple(item.strip().upper() for item in metadata.get("categories", "ALL").split(",") if item.strip())
        return OpsSkill(name, description, categories, raw[end + 4:].strip(), path)
