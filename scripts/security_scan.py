"""Read-only, value-masking credential checks shared by release and deployment validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SecurityFinding:
    label: str
    path: str
    line: int
    masked: str

    def message(self) -> str:
        return f"{self.label} found in {self.path}:{self.line} ({self.masked})"


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "OpenAI-style API key",
        re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
        "sk-…",
    ),
    (
        "GitHub token",
        re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
        "gh…",
    ),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "-----BEGIN … PRIVATE KEY-----",
    ),
)

PUBLIC_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "absolute macOS user path",
        re.compile("/" r"Users/[^/\s]+/"),
        "/" + "Users/…/",
    ),
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_article_slug(text: str, match: re.Match[str]) -> bool:
    token = match.group(0)
    if not re.fullmatch(r"sk-(?:[a-z]+-){2,}[a-z]+", token):
        return False
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start : match.start()]
    return re.search(r"https?://[^\s]*$", prefix) is not None


def scan_text(relative: str, text: str, *, include_public_paths: bool = False) -> list[SecurityFinding]:
    patterns = SECRET_PATTERNS + (PUBLIC_PATH_PATTERNS if include_public_paths else ())
    findings: list[SecurityFinding] = []
    for label, pattern, masked in patterns:
        for match in pattern.finditer(text):
            if label == "OpenAI-style API key" and _is_article_slug(text, match):
                continue
            findings.append(
                SecurityFinding(
                    label=label,
                    path=relative,
                    line=_line_number(text, match.start()),
                    masked=masked,
                )
            )
    return findings


def scan_files(
    root: Path,
    relative_paths: Iterable[str],
    *,
    include_public_paths: bool = False,
    public_path_allowlist: set[str] | None = None,
) -> list[SecurityFinding]:
    root = root.resolve()
    allowlist = public_path_allowlist or set()
    findings: list[SecurityFinding] = []
    for relative in sorted(set(relative_paths)):
        path = root / relative
        if not path.is_file():
            findings.append(
                SecurityFinding(
                    label="security scan unavailable",
                    path=relative,
                    line=0,
                    masked="missing or non-regular file",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(
                SecurityFinding(
                    label="security scan unavailable",
                    path=relative,
                    line=0,
                    masked="unreadable UTF-8 content",
                )
            )
            continue
        for finding in scan_text(relative, text, include_public_paths=include_public_paths):
            if finding.label == "absolute macOS user path" and relative in allowlist:
                continue
            findings.append(finding)
    return findings
