"""화면에 표시할 시간/용량 문자열 포맷 헬퍼."""

from __future__ import annotations


def format_duration(total_sec: int | None) -> str:
    """강의 하나의 재생시간을 사람이 읽기 쉬운 문자열로 바꾼다."""
    if total_sec is None:
        return "재생시간 정보 없음"
    h, rem = divmod(int(total_sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}시간 {m}분"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"


def format_hours_minutes(total_sec: float) -> str:
    """합산된 시간을 'N시간 M분' 형태로 바꾼다."""
    total_sec = max(0, int(total_sec))
    h, rem = divmod(total_sec, 3600)
    m, _ = divmod(rem, 60)
    if h and m:
        return f"{h}시간 {m}분"
    if h:
        return f"{h}시간"
    return f"{m}분"
