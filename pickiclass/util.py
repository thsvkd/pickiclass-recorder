"""파일/디렉터리 이름 안전화 등 공용 유틸리티."""

from __future__ import annotations

import re

_UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n]')


def sanitize_filename(name: str, fallback: str) -> str:
    """파일/디렉터리 이름으로 안전하지 않은 문자를 제거하고 길이를 제한한다.

    결과가 빈 문자열이 되면 fallback을 사용한다.
    """
    cleaned = _UNSAFE_CHARS_RE.sub("", name or "")
    cleaned = cleaned.strip(" .")
    cleaned = cleaned[:100]
    return cleaned or fallback
