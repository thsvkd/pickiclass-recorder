"""PICKICLASS_UI_DEMO=1 로 실행할 때 코어 없이 화면을 확인하기 위한 가짜 데이터/구현.

실제 pickiclass.client / pickiclass.ffmpeg_tool / pickiclass.recorder 와 동일한 인터페이스를
흉내 내되, 네트워크나 ffmpeg 없이 짧은 지연/스레드만으로 동작한다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from ui.core_bridge import Course, Lesson, LoginError, Progress, Summary

_SECTION_TITLES = [
    "PART 1. 오리엔테이션",
    "PART 2. 기초 다지기",
    "PART 3. 실전 프로젝트",
    "PART 4. 마무리",
]
_SECTION_COUNTS = [1, 3, 4, 2]  # 총 10개


# 실제 피키클래스는 강의에 따라 재생시간을 아예 렌더링하지 않는 경우가 있다
# (예: [631] MBTI 강의, [139] 인간을 분석하는 6가지 도구 강의는 전부 재생시간이 없음).
# duration_sec 은 표시/예상시간 계산용 부가 정보일 뿐 영상 유무와 무관하므로,
# 마지막 데모 강의는 목차 전체가 재생시간 정보 없음인 경우를 그대로 재현해 회귀를 잡는다.
_ALL_UNKNOWN_DURATION_COURSE_ID = "demo-course-3"


def make_demo_courses() -> list[Course]:
    """가짜 강의 4개 (마지막 1개는 목차 전체가 재생시간 정보 없음)."""
    titles = [
        "[데모] 왕초보 파이썬 완전정복",
        "[데모] 실무 엑셀 자동화",
        "[데모] 유튜브 영상 편집 기초",
        "[데모] 재생시간 정보가 전혀 없는 강의",
    ]
    return [
        Course(course_id=f"demo-course-{i}", title=title, entry_lesson_id=f"demo-course-{i}-l0")
        for i, title in enumerate(titles)
    ]


def make_demo_lessons(course: Course) -> list[Lesson]:
    """가짜 강의 목차 10개 (섹션 4개로 그룹, 일부는 재생시간 정보 없음)."""
    all_unknown = course.course_id == _ALL_UNKNOWN_DURATION_COURSE_ID
    lessons: list[Lesson] = []
    idx = 0
    # strict=True: 두 목록의 길이가 어긋나면 조용히 잘리는 대신 즉시 드러나게 한다.
    for section_index, (section_title, count) in enumerate(
        zip(_SECTION_TITLES, _SECTION_COUNTS, strict=True)
    ):
        for i in range(count):
            has_known_duration = not all_unknown and idx % 5 != 0  # 5개 중 1개는 재생시간 정보 없음
            duration = 300 + (idx * 47) % 900 if has_known_duration else None
            lessons.append(
                Lesson(
                    lesson_id=f"{course.course_id}-l{idx}",
                    title=f"{i + 1}강. 샘플 강의 제목이 여기에 표시됩니다 ({idx + 1}/10)",
                    duration_sec=duration,
                    section_index=section_index,
                    section_title=section_title,
                    index_in_section=i,
                    global_index=idx,
                )
            )
            idx += 1
    return lessons


class DemoClient:
    """PickiclassClient 를 흉내 내는 가짜 클라이언트."""

    def login(self, email: str, password: str) -> None:
        time.sleep(0.5)
        if not email or not password:
            raise LoginError("이메일 또는 비밀번호가 올바르지 않습니다.")

    def list_my_courses(self) -> list[Course]:
        time.sleep(0.4)
        return make_demo_courses()

    def list_lessons(self, course: Course) -> list[Lesson]:
        time.sleep(0.4)
        return make_demo_lessons(course)


def demo_find_ffmpeg() -> tuple[Path, Path] | None:
    """데모에서는 ffmpeg가 없는 상태로 시작해 설치 화면도 확인할 수 있게 한다."""
    return None


def demo_install_ffmpeg(on_progress: Callable[[float], None] | None = None) -> tuple[Path, Path]:
    for i in range(1, 11):
        time.sleep(0.15)
        if on_progress:
            on_progress(i / 10)
    fake_dir = Path.home() / ".pickiclass-demo"
    return fake_dir / "ffmpeg", fake_dir / "ffprobe"


class DemoRecorder:
    """Recorder 를 흉내 내는 가짜 레코더. 진행 화면/실패 재시도 UI 확인용."""

    def __init__(
        self,
        client: DemoClient,
        ffmpeg: Path,
        ffprobe: Path,
        output_dir: Path,
        speed: float = 1.5,
        keep_original: bool = False,
        workers: int = 2,
        capture_device_index: int | None = None,
    ) -> None:
        self.client = client
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.output_dir = output_dir
        self.speed = speed
        self.keep_original = keep_original
        self.workers = max(1, workers)
        self.capture_device_index = capture_device_index

    def run(
        self,
        course: Course,
        lessons: list[Lesson],
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> Summary:
        queue = list(lessons)
        # 4개 중 1개는 데모용으로 실패시켜 실패 목록/재시도 UI를 확인할 수 있게 한다.
        fail_ids = {lesson.lesson_id for i, lesson in enumerate(queue) if i % 4 == 3}

        pos = 0
        pos_lock = threading.Lock()
        result_lock = threading.Lock()
        counters = {"done": 0, "skipped": 0, "failed": 0}
        errors: list[tuple[str, str]] = []

        def worker() -> None:
            nonlocal pos
            while True:
                with pos_lock:
                    if pos >= len(queue) or cancel.is_set():
                        return
                    lesson = queue[pos]
                    pos += 1

                stages = [("fetching", 0.2), ("downloading", 1.0), ("converting", 0.6)]
                for stage, duration in stages:
                    if cancel.is_set():
                        return
                    steps = 5
                    for step in range(1, steps + 1):
                        if cancel.is_set():
                            return
                        time.sleep(duration / steps)
                        on_progress(
                            Progress(lesson=lesson, stage=stage, percent=step / steps * 100)
                        )

                if lesson.lesson_id in fail_ids:
                    message = "데모 오류: 네트워크 응답 시간 초과"
                    on_progress(Progress(lesson=lesson, stage="error", percent=0, message=message))
                    with result_lock:
                        counters["failed"] += 1
                        errors.append((lesson.title, message))
                else:
                    # duration_sec 유무는 다운로드 가능 여부와 무관하므로 정상 처리한다.
                    on_progress(Progress(lesson=lesson, stage="done", percent=100))
                    with result_lock:
                        counters["done"] += 1

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(self.workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return Summary(
            done=counters["done"],
            skipped=counters["skipped"],
            failed=counters["failed"],
            errors=errors,
        )


# ----------------------------------------------------------------------
# 자동 업데이트(Velopack) 데모 시나리오
# ----------------------------------------------------------------------
class DemoUpdateInfo:
    """pickiclass.velopack_update.check() 가 반환하는 UpdateInfo 를 흉내 낸 가짜 객체."""

    def __init__(self, version: str) -> None:
        self.version = version


_DEMO_CURRENT_VERSION = "1.0.0"
_DEMO_NEW_VERSION = "1.1.0"


def demo_run_startup_maintenance() -> None:
    time.sleep(0.1)


def demo_is_installed() -> bool:
    # 데모에서는 배포판(설치된 앱)인 것처럼 흉내 내어 업데이트 UI 시나리오를 태워본다.
    return True


def demo_current_version() -> str | None:
    return _DEMO_CURRENT_VERSION


def demo_check_update() -> DemoUpdateInfo | None:
    time.sleep(0.5)
    return DemoUpdateInfo(_DEMO_NEW_VERSION)


def demo_target_version(info: DemoUpdateInfo) -> str:
    return info.version


def demo_download_update(
    info: DemoUpdateInfo, progress_cb: Callable[[float], None] | None = None
) -> None:
    for i in range(1, 11):
        time.sleep(0.15)
        if progress_cb:
            progress_cb(i / 10)


def demo_apply_and_restart(info: DemoUpdateInfo) -> None:
    # 실제로는 앱을 재시작하며 돌아오지 않지만, 데모에서 재시작해버리면 이후 화면을
    # 계속 확인할 수 없으므로 여기서는 아무 것도 하지 않는다 (호출부에서 안내만 표시).
    pass
