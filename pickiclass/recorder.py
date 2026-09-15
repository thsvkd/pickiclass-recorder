"""강의 영상을 다운로드하고 1.5배속으로 변환하는 레코더."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum, auto
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

import av
import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from pickiclass.audio_capture import AudioCaptureCancelled
from pickiclass.client import PickiclassClient, ProtectedMediaError, SourceUnavailableError
from pickiclass.models import Course, Lesson, Progress, Summary
from pickiclass.playback import capture_protected_lesson
from pickiclass.util import sanitize_filename

_MAX_ATTEMPTS = 3  # 최초 시도 1회 + 재시도 2회
_HTTP_TIMEOUT = (3, 1)


class _Cancelled(Exception):
    """cancel 이벤트가 set되어 처리를 중단할 때 내부적으로 사용한다."""


class _MP4Availability(Enum):
    AVAILABLE = auto()
    UNAVAILABLE = auto()
    UNKNOWN = auto()


class Recorder:
    def __init__(
        self,
        client: PickiclassClient,
        output_dir: Path,
        speed: float = 1.5,
        keep_original: bool = False,
        workers: int = 2,
        capture_device_index: int | None = None,
    ) -> None:
        self.client = client
        self.output_dir = Path(output_dir)
        self.speed = speed
        self.keep_original = keep_original
        self.workers = max(1, workers)
        self.capture_device_index = capture_device_index
        self._transcriber = None
        self._transcribe_lock = threading.Lock()
        self._capture_lock = threading.Lock()

    def run(
        self,
        course: Course,
        lessons: list[Lesson],
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> Summary:
        done = skipped = failed = 0
        errors: list[tuple[str, str]] = []
        lock = threading.Lock()

        workers = 1 if self.capture_device_index is not None else self.workers
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._process_lesson, course, lesson, on_progress, cancel): lesson
                for lesson in lessons
            }
            for future in as_completed(futures):
                lesson = futures[future]
                try:
                    status, message = future.result()
                except Exception as exc:  # 예상치 못한 예외에 대한 방어
                    status, message = "error", str(exc)
                with lock:
                    if status == "done":
                        done += 1
                    elif status == "skipped":
                        skipped += 1
                    elif status == "error":
                        failed += 1
                        errors.append((lesson.title, message))
                    # 'cancelled'는 집계하지 않는다.

        return Summary(done=done, skipped=skipped, failed=failed, errors=errors)

    # -- 강의 1개 처리 -----------------------------------------------------

    def _process_lesson(
        self,
        course: Course,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> tuple[str, str]:
        if cancel.is_set():
            return ("cancelled", "취소됨")

        final_path = self._final_path(course, lesson)
        if any(self._outputs_complete(self._final_path(course, lesson, suffix)) for suffix in (".mp4", ".wav")):
            on_progress(
                Progress(lesson=lesson, stage="skipped", percent=100.0, message="이미 다운로드됨")
            )
            return ("skipped", "이미 다운로드됨")

        final_path.parent.mkdir(parents=True, exist_ok=True)

        last_error = "알 수 없는 오류"
        for _attempt in range(_MAX_ATTEMPTS):
            if cancel.is_set():
                return ("cancelled", "취소됨")
            try:
                self._process_lesson_once(course, lesson, final_path, on_progress, cancel)
                on_progress(Progress(lesson=lesson, stage="done", percent=100.0, message="완료"))
                return ("done", "")
            except _Cancelled:
                self._cleanup_partials(course, lesson, final_path)
                return ("cancelled", "취소됨")
            except (SourceUnavailableError, FileExistsError) as exc:
                last_error = str(exc)
                self._cleanup_partials(course, lesson, final_path)
                break
            except Exception as exc:
                last_error = str(exc)
                self._cleanup_partials(course, lesson, final_path)
                continue

        on_progress(Progress(lesson=lesson, stage="error", percent=0.0, message=last_error))
        return ("error", last_error)

    def _process_lesson_once(
        self,
        course: Course,
        lesson: Lesson,
        final_path: Path,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        raw_path = self._raw_path(course, lesson, final_path)
        transcript_dir = self._transcript_dir(final_path)
        if any((transcript_dir / final_path.with_suffix(ext).name).exists()
               for ext in (".txt", ".srt", ".json")):
            raise FileExistsError(
                "기존 전사 파일 일부가 있습니다. 파일을 보관한 뒤 다른 저장 폴더에서 "
                "다시 처리하세요. 기존 파일은 덮어쓰지 않았습니다."
            )
        final_exists = final_path.is_file() and final_path.stat().st_size > 0
        raw_exists = raw_path.is_file() and raw_path.stat().st_size > 0
        if final_exists and not raw_exists:
            raise SourceUnavailableError(
                "배속 영상은 있지만 원본 전사 입력이 없습니다. 원본 파일을 로컬 전사로 "
                "처리하거나 다른 저장 폴더에서 다시 다운로드하세요. 기존 영상은 보존했습니다."
            )
        if not raw_exists:
            on_progress(Progress(lesson, "fetching", 0, "영상 정보 조회 중"))
            try:
                source = self.client.get_video_source(course.course_id, lesson.lesson_id)
            except ProtectedMediaError:
                if self.capture_device_index is None:
                    raise SourceUnavailableError(
                        "암호화된 강의입니다. 출력 장치를 선택한 뒤 앱 내장 재생으로 소리를 저장하세요."
                    ) from None
                self._process_captured_lesson(course, lesson, on_progress, cancel)
                return
            if source is None or not (source.mp4_url or source.hls_url):
                raise SourceUnavailableError("처리 가능한 일반 미디어 주소가 없습니다.")
            if cancel.is_set():
                raise _Cancelled()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_part = _part_path(raw_path)
            downloaded = bool(source.mp4_url) and self._download_mp4(
                source.mp4_url, raw_part, lesson, on_progress, cancel
            )
            if not downloaded:
                if not source.hls_url:
                    raise SourceUnavailableError("영상 파일 다운로드에 실패했습니다.")
                self._download_hls(
                    source.hls_url, raw_part, lesson.duration_sec, lesson, on_progress, cancel
                )
            raw_part.replace(raw_path)
        if not final_exists:
            self._convert(
                raw_path, final_path, lesson.duration_sec, "converting",
                f"{self.speed}배속 변환 중", lesson, on_progress, cancel,
            )
        transcript_dir.mkdir(parents=True, exist_ok=True)
        self._transcribe_file(raw_path, transcript_dir, lesson, on_progress, cancel)
        if not self.keep_original:
            raw_path.unlink(missing_ok=True)

    @staticmethod
    def _transcript_dir(final_path: Path) -> Path:
        return final_path.parent / "전사 (원본 시간)"

    def _transcribe_file(self, source, output_dir, lesson, on_progress, cancel):
        from pickiclass.transcription import Transcriber, TranscriptionCancelled

        with self._transcribe_lock:
            if cancel.is_set():
                raise _Cancelled()
            if self._transcriber is None:
                self._transcriber = Transcriber()
            try:
                self._transcriber.transcribe(
                    source,
                    output_dir=output_dir,
                    cancel=cancel,
                    on_progress=lambda value, message: on_progress(
                        Progress(lesson, "transcribing", value * 100, message)
                    ),
                )
            except TranscriptionCancelled:
                raise _Cancelled() from None

    def _outputs_complete(self, final_path: Path) -> bool:
        if not (final_path.is_file() and final_path.stat().st_size > 0):
            return False
        folder = self._transcript_dir(final_path)
        return all(
            (folder / final_path.with_suffix(suffix).name).is_file()
            and (folder / final_path.with_suffix(suffix).name).stat().st_size > 0
            for suffix in (".txt", ".srt", ".json")
        )

    def _process_captured_lesson(
        self,
        course: Course,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        final_path = self._final_path(course, lesson, ".wav")
        raw_path = self._raw_path(course, lesson, final_path)
        transcript_dir = self._transcript_dir(final_path)
        if any((transcript_dir / final_path.with_suffix(ext).name).exists()
               for ext in (".txt", ".srt", ".json")):
            raise FileExistsError(
                "기존 전사 파일 일부가 있습니다. 파일을 보관한 뒤 다른 저장 폴더에서 "
                "다시 처리하세요. 기존 파일은 덮어쓰지 않았습니다."
            )
        final_exists = final_path.is_file() and final_path.stat().st_size > 0
        raw_exists = raw_path.is_file() and raw_path.stat().st_size > 0
        if not raw_exists:
            on_progress(Progress(lesson, "recording", 0, "앱 내장 재생 소리를 저장 중"))
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_part = _part_path(raw_path)
            try:
                with self._capture_lock:
                    capture_protected_lesson(
                        self.client,
                        course.course_id,
                        lesson,
                        raw_part,
                        self.capture_device_index,
                        on_progress=lambda value, message: on_progress(
                            Progress(lesson, "recording", value * 100, message)
                        ),
                        cancel=cancel,
                    )
            except AudioCaptureCancelled:
                raw_part.unlink(missing_ok=True)
                raise _Cancelled() from None
            raw_part.replace(raw_path)
        if not final_exists:
            if abs(self.speed - 1.0) < 1e-6:
                shutil.copy2(raw_path, final_path)
            else:
                self._convert_audio(
                    raw_path, final_path, lesson.duration_sec, lesson, on_progress, cancel,
                )
        transcript_dir.mkdir(parents=True, exist_ok=True)
        self._transcribe_file(raw_path, transcript_dir, lesson, on_progress, cancel)
        if not self.keep_original and raw_path.resolve() != final_path.resolve():
            raw_path.unlink(missing_ok=True)

    def _convert_audio(
        self,
        input_source: Path,
        final_path: Path,
        total_sec: float | None,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        final_part = _part_path(final_path)
        on_progress(Progress(lesson, "converting", 0, f"음성 {self.speed}배속 변환 중"))
        self._transcode(
            input_source, final_part, total_sec, "converting", lesson, on_progress, cancel,
            video=False,
        )
        final_part.replace(final_path)

    def _process_youtube(
        self,
        url: str,
        course: Course,
        lesson: Lesson,
        final_path: Path,
        total_sec: float | None,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        raw_path = self._raw_path(course, lesson, final_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_part = _part_path(raw_path)
        self._download_youtube(url, raw_part, lesson, on_progress, cancel)
        raw_part.replace(raw_path)
        try:
            if total_sec is None:
                total_sec = self._probe_stream_duration(str(raw_path))
            self._convert(
                raw_path,
                final_path,
                total_sec,
                "converting",
                f"YouTube 영상 {self.speed}배속 변환 중",
                lesson,
                on_progress,
                cancel,
            )
        finally:
            if not self.keep_original:
                raw_path.unlink(missing_ok=True)

    @staticmethod
    def _is_mp4_response(response: requests.Response) -> bool:
        if response.status_code not in (200, 206):
            return False
        content_type = response.headers.get("content-type", "").lower()
        return not content_type or content_type.startswith(
            ("video/mp4", "application/octet-stream", "audio/")
        )

    def _check_mp4(self, url: str) -> _MP4Availability:
        try:
            with self.client.session.get(
                url,
                headers={"Range": "bytes=0-0"},
                stream=True,
                timeout=_HTTP_TIMEOUT,
            ) as response:
                if response.status_code in (401, 403, 404, 405, 410):
                    return _MP4Availability.UNAVAILABLE
                if not self._is_mp4_response(response):
                    if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                        return _MP4Availability.UNAVAILABLE
                    return _MP4Availability.UNKNOWN
                for chunk in response.iter_content(chunk_size=1):
                    if chunk:
                        return _MP4Availability.AVAILABLE
                return _MP4Availability.UNAVAILABLE
        except requests.RequestException:
            return _MP4Availability.UNKNOWN

    def _download_mp4(
        self,
        url: str,
        destination: Path,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> bool:
        """MP4를 바로 저장한다. 제공되지 않거나 전송 실패면 False를 반환한다."""
        try:
            with self.client.session.get(url, stream=True, timeout=_HTTP_TIMEOUT) as response:
                if not self._is_mp4_response(response):
                    destination.unlink(missing_ok=True)
                    return False
                total = int(response.headers.get("content-length", 0))
                downloaded = 0
                on_progress(
                    Progress(
                        lesson=lesson, stage="downloading", percent=0.0, message="MP4 다운로드 중"
                    )
                )
                with destination.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if cancel.is_set():
                            raise _Cancelled()
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        percent = downloaded / total * 100 if total else 0.0
                        on_progress(
                            Progress(
                                lesson=lesson,
                                stage="downloading",
                                percent=min(100.0, percent),
                                message="MP4 다운로드 중",
                            )
                        )
                if downloaded == 0 or (total and downloaded != total):
                    destination.unlink(missing_ok=True)
                    return False
                if self._probe_stream_duration(str(destination)) is None:
                    destination.unlink(missing_ok=True)
                    return False
                return True
        except requests.RequestException:
            destination.unlink(missing_ok=True)
            if cancel.is_set():
                raise _Cancelled() from None
            return False

    def _download_hls(
        self,
        url: str,
        destination: Path,
        total_sec: float | None,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        on_progress(
            Progress(lesson=lesson, stage="downloading", percent=0.0, message="HLS 다운로드 중")
        )
        prepared = self.client.session.prepare_request(requests.Request("GET", url))
        if prepared.headers.get("Cookie") or prepared.headers.get("Authorization"):
            raise SourceUnavailableError(
                "쿠키 인증이 필요한 HLS는 현재 지원하지 않습니다. "
                "인증값을 다른 미디어 서버에 전달하지 않았습니다."
            )
        headers = ""
        for name in ("User-Agent", "Referer"):
            value = prepared.headers.get(name)
            if value and "\r" not in value and "\n" not in value:
                headers += f"{name}: {value}\r\n"
        if cancel.is_set():
            raise _Cancelled()
        report = self._progress_reporter(total_sec, "downloading", lesson, on_progress)
        with (
            av.open(url, container_options={"headers": headers} if headers else {}, timeout=30)
            as inp,
            av.open(str(destination), "w") as out,
        ):
            # 스트림 복사(-c copy). ADTS AAC는 mp4 muxer가 aac_adtstoasc를 자동으로 끼운다.
            in_streams = [s for s in inp.streams if s.type in ("video", "audio")]
            copies = {s.index: out.add_stream_from_template(s) for s in in_streams}
            # ffmpeg CLI처럼 시작 시각을 0으로 당긴다 (HLS/TS는 보통 1.4초 등에서 시작).
            start = Fraction(inp.start_time or 0, av.time_base)
            shifts = {s.index: round(start / s.time_base) for s in in_streams}
            for packet in inp.demux(in_streams):
                if cancel.is_set():
                    raise _Cancelled()
                if packet.dts is None:  # demux 끝의 flush 패킷
                    continue
                shift = shifts[packet.stream.index]
                packet.dts -= shift
                if packet.pts is not None:
                    packet.pts -= shift
                    report(float(packet.pts * packet.time_base))
                packet.stream = copies[packet.stream.index]
                out.mux(packet)

    def _download_youtube(
        self,
        url: str,
        destination: Path,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        if cancel.is_set():
            raise _Cancelled()

        def progress_hook(status: dict) -> None:
            cancel_if_requested()
            state = status.get("status")
            if state == "downloading":
                downloaded = status.get("downloaded_bytes") or 0
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                percent = downloaded / total * 100 if total else 0.0
                on_progress(
                    Progress(
                        lesson=lesson,
                        stage="downloading",
                        percent=min(100.0, percent),
                        message="YouTube 다운로드 중",
                    )
                )
            elif state == "finished":
                on_progress(
                    Progress(
                        lesson=lesson,
                        stage="downloading",
                        percent=100.0,
                        message="YouTube 다운로드 완료",
                    )
                )

        def cancel_if_requested(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if cancel.is_set():
                raise DownloadCancelled("사용자가 다운로드를 취소했습니다.")

        on_progress(
            Progress(lesson=lesson, stage="downloading", percent=0.0, message="YouTube 다운로드 중")
        )
        with TemporaryDirectory(
            prefix=f".ytdlp_{lesson.lesson_id}_", dir=destination.parent
        ) as temp_dir:
            options = {
                # ffmpeg 없이 받으므로 영상+음성이 합쳐진 단일 파일만 고른다.
                "format": "b[ext=mp4]/b",
                "format_sort": ["vcodec:h264", "lang", "quality", "res", "fps", "acodec:aac"],
                "outtmpl": str(Path(temp_dir) / "video.%(ext)s"),
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [cancel_if_requested],
                "match_filter": cancel_if_requested,
                "noplaylist": True,
                "socket_timeout": 30,
                "retries": 3,
                "fragment_retries": 3,
                "extractor_retries": 3,
                "quiet": True,
                "noprogress": True,
                "no_warnings": True,
            }
            try:
                with YoutubeDL(options) as downloader:
                    info = downloader.extract_info(url, download=True)
                    downloaded_path = Path(
                        info.get("filepath") or downloader.prepare_filename(info)
                    )
            except DownloadCancelled:
                raise _Cancelled() from None
            except DownloadError as exc:
                raise RuntimeError(f"YouTube 다운로드 실패: {exc}") from exc

            if cancel.is_set():
                raise _Cancelled()
            if not downloaded_path.exists():
                raise RuntimeError("YouTube 다운로드가 완료됐지만 결과 파일을 찾지 못했습니다.")
            destination.unlink(missing_ok=True)
            downloaded_path.replace(destination)

    def _convert(
        self,
        input_source: str | Path,
        final_path: Path,
        total_sec: float | None,
        stage: str,
        message: str,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        final_part = _part_path(final_path)
        on_progress(Progress(lesson=lesson, stage=stage, percent=0.0, message=message))
        self._transcode(
            input_source, final_part, total_sec, stage, lesson, on_progress, cancel, video=True
        )
        final_part.replace(final_path)

    # -- 경로 계산 -----------------------------------------------------

    def _final_path(self, course: Course, lesson: Lesson, suffix: str = ".mp4") -> Path:
        safe_course = sanitize_filename(course.title, f"course_{course.course_id}")
        safe_section = sanitize_filename(lesson.section_title, f"section_{lesson.section_index}")
        safe_title = sanitize_filename(lesson.title, f"lesson_{lesson.lesson_id}")
        section_dir = (
            self.output_dir / safe_course / f"{lesson.section_index + 1:02d}_{safe_section}"
        )
        return section_dir / f"{lesson.index_in_section + 1:02d}_{safe_title}{suffix}"

    def _raw_path(self, course: Course, lesson: Lesson, final_path: Path) -> Path:
        if self.keep_original:
            return final_path.parent / "원본" / final_path.name
        return final_path.parent / ".source" / final_path.name

    def _cleanup_partials(self, course: Course, lesson: Lesson, final_path: Path) -> None:
        for path in (
            final_path,
            self._final_path(course, lesson, ".mp4"),
            self._final_path(course, lesson, ".wav"),
        ):
            _part_path(path).unlink(missing_ok=True)
            _part_path(self._raw_path(course, lesson, path)).unlink(missing_ok=True)
        # 완성된 원본은 전사 재시도에 필요하므로 보존한다.

    # -- PyAV 실행 -----------------------------------------------------

    def _transcode(
        self,
        source: str | Path,
        destination: Path,
        total_sec: float | None,
        stage: str,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
        *,
        video: bool,
    ) -> None:
        """PyAV로 배속 변환한다. video면 H.264/AAC, 아니면 음성만 PCM(s16le)으로 쓴다."""
        if cancel.is_set():
            raise _Cancelled()
        report = self._progress_reporter(total_sec, stage, lesson, on_progress)
        with av.open(str(source)) as inp, av.open(str(destination), "w") as out:
            # 입력 스트림 index -> (출력 스트림, atempo 그래프. 영상은 None)
            pipes = {}
            if video and inp.streams.video:
                in_video = inp.streams.video[0]
                out_video = out.add_stream("libx264", options={"crf": "20", "preset": "veryfast"})
                out_video.width, out_video.height = in_video.width, in_video.height
                out_video.pix_fmt = "yuv420p"
                out_video.codec_context.time_base = in_video.time_base
                pipes[in_video.index] = (out_video, None)
            if inp.streams.audio:
                in_audio = inp.streams.audio[0]
                out_audio = out.add_stream(
                    "aac" if video else "pcm_s16le",
                    rate=in_audio.rate,
                    layout=in_audio.layout.name,
                )
                if video:
                    out_audio.bit_rate = 128_000
                pipes[in_audio.index] = (out_audio, self._atempo_graph(in_audio))
            if not pipes:
                raise RuntimeError("변환할 영상·음성 스트림이 없습니다.")

            for packet in inp.demux([s for s in inp.streams if s.index in pipes]):
                if cancel.is_set():
                    raise _Cancelled()
                out_stream, graph = pipes[packet.stream.index]
                for frame in packet.decode():
                    if frame.time is not None:
                        report(frame.time)
                    self._encode(out, out_stream, graph, frame)
            for out_stream, graph in pipes.values():
                self._encode(out, out_stream, graph, None)

    def _encode(self, out, stream, graph, frame) -> None:
        """프레임에 배속을 적용해 인코딩·mux한다. frame이 None이면 남은 데이터를 비운다."""
        if graph is None:
            if frame is not None:
                if frame.pts is None:
                    return
                frame.pts = round(frame.pts / self.speed)  # ffmpeg setpts=PTS/speed
            frames = [frame]
        else:
            graph.push(frame)
            frames = []
            while True:
                try:
                    frames.append(graph.pull())
                except (av.BlockingIOError, av.EOFError):
                    break
            if frame is None:
                frames.append(None)
        for item in frames:
            out.mux(stream.encode(item))

    def _atempo_graph(self, stream) -> av.filter.Graph:
        graph = av.filter.Graph()
        chain = [
            graph.add_abuffer(template=stream),
            *(graph.add("atempo", str(factor)) for factor in _atempo_factors(self.speed)),
            graph.add("abuffersink"),
        ]
        for src, dst in pairwise(chain):
            src.link_to(dst)
        graph.configure()
        return graph

    _STAGE_LABELS = {
        "downloading": "내려받는 중",
        "streaming": "내려받으며 변환 중",
        "converting": "변환 중",
    }

    @classmethod
    def _progress_reporter(
        cls,
        total_sec: float | None,
        stage: str,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
    ) -> Callable[[float], None]:
        """처리한 입력 시각(초)을 받아 0.5초에 한 번씩 진행률을 알린다."""
        last = 0.0

        def report(elapsed: float) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last < 0.5:
                return
            last = now
            if total_sec:
                percent = max(0.0, min(100.0, elapsed / total_sec * 100))
                on_progress(Progress(lesson=lesson, stage=stage, percent=percent, message=""))
            else:
                # 총 길이를 알 수 없는 경우: 멈춘 것처럼 보이지 않도록 경과 시간을 알려준다.
                label = cls._STAGE_LABELS.get(stage, stage)
                message = f"{label} ({_format_elapsed(elapsed)})"
                on_progress(Progress(lesson=lesson, stage=stage, percent=0.0, message=message))

        return report

    @staticmethod
    def _probe_stream_duration(source: str) -> float | None:
        try:
            with av.open(source) as container:
                return container.duration / av.time_base if container.duration else None
        except Exception:
            return None


def _part_path(path: Path) -> Path:
    """중단된 파일이 완성본으로 오인되지 않도록 확장자 앞에 .part를 끼워넣는다.

    (예: foo.mp4 -> foo.part.mp4. PyAV가 출력 포맷을 확장자로 추론하므로
    foo.mp4.part처럼 끝에 붙이면 muxer를 찾지 못해 실패한다.)
    """
    return path.with_name(f"{path.stem}.part{path.suffix}")


def _format_elapsed(seconds: float) -> str:
    """경과 시간을 '3분 12초' 형태의 사람이 읽기 쉬운 문자열로 변환한다."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}시간 {minutes}분 {secs}초"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _atempo_factors(speed: float) -> list[float]:
    """atempo는 0.5~2.0만 지원하므로 범위를 벗어나면 체인으로 분해한다."""
    if 0.5 <= speed <= 2.0:
        return [speed]
    factors: list[float] = []
    remaining = speed
    if remaining > 2.0:
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
    else:
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
    factors.append(remaining)
    return factors
