"""공개 데이터 모델. GUI가 이 시그니처에 의존하므로 이름/필드를 임의로 바꾸지 말 것."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Course:
    course_id: str
    title: str
    entry_lesson_id: str


@dataclass(frozen=True)
class Lesson:
    lesson_id: str
    title: str
    # 사이트가 표시한 재생시간(초). 표시/예상 소요시간 계산용 부가 정보일 뿐,
    # 영상 유무와는 무관하다(강의에 따라 사이트가 재생시간 자체를 렌더링하지
    # 않는 경우가 있어 None일 수 있다). 실제 영상 유무는 client.get_video_uid()
    # 반환값으로만 판정한다.
    duration_sec: int | None
    section_index: int  # 0부터
    section_title: str
    index_in_section: int  # 0부터
    global_index: int  # 0부터


@dataclass(frozen=True)
class VideoSource:
    uid: str
    mp4_url: str | None
    hls_url: str | None
    youtube_url: str | None = None


@dataclass
class Progress:
    lesson: Lesson
    # pending|fetching|downloading|recording|streaming|converting|transcribing|done|skipped|error
    stage: str
    percent: float  # 0~100, 현재 stage 기준
    message: str = ""


@dataclass
class Summary:
    done: int
    skipped: int
    failed: int
    errors: list[tuple[str, str]] = field(default_factory=list)  # (강의 제목, 에러 메시지)
