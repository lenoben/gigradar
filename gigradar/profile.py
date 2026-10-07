"""My freelancer profile: free-text skill areas (profile.md) + structured settings ([profile]).

profile.md format: every `## Heading` starts one skill-area section; the semantic scorer
matches a job against each section and keeps the best one. Text above the first `##`
and `<!-- comments -->` are ignored, so the template can carry instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class ProfileError(Exception):
    """profile.md is missing, unreadable or has no usable sections."""


@dataclass(frozen=True)
class ProfileSection:
    heading: str
    text: str


@dataclass(frozen=True)
class Profile:
    path: Path
    sections: list[ProfileSection]
    skills: list[str]
    min_hourly: float | None       # hourly jobs whose max rate is below this fail the hard rules
    min_fixed: float | None        # fixed jobs whose budget is below this fail the hard rules
    tiers: list[str]               # allowed tiers; empty = any
    exclude_keywords: list[str]


def parse_sections(markdown: str) -> list[ProfileSection]:
    """Split profile markdown into `## ` sections. Raises ProfileError if none are usable."""
    sections: list[ProfileSection] = []
    heading: str | None = None
    body: list[str] = []

    def close() -> None:
        if heading is None:
            return  # preamble above the first `##`
        text = "\n".join(body).strip()
        if not text:
            raise ProfileError(f"section '{heading}' is empty")
        if any(s.heading == heading for s in sections):
            raise ProfileError(f"duplicate section '{heading}'")
        sections.append(ProfileSection(heading=heading, text=text))

    for line in _COMMENT.sub("", markdown).splitlines():
        if line.startswith("## "):
            close()
            heading, body = line[3:].strip(), []
            if not heading:
                raise ProfileError("a '## ' line needs a heading")
        else:
            body.append(line)
    close()
    if not sections:
        raise ProfileError("no '## ' sections found (one per skill area)")
    return sections


def read_sections(path: Path) -> list[ProfileSection]:
    try:
        markdown = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileError(f"{path} not found (copy profile.example.md)") from exc
    except UnicodeDecodeError as exc:
        raise ProfileError(f"{path.name} is not UTF-8: {exc}") from exc
    try:
        return parse_sections(markdown)
    except ProfileError as exc:
        raise ProfileError(f"{path.name}: {exc}") from exc
