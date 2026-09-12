"""피키클래스 조회, 로컬 음성 저장 및 전사 명령."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--list", action="store_true", help="내 강의 목록")
    modes.add_argument("--course-id", help="강의 식별자의 회차 목록")
    modes.add_argument("--devices", action="store_true", help="녹음 가능한 출력 장치")
    modes.add_argument("--transcribe", type=Path, help="로컬 미디어를 전사")
    modes.add_argument("--record", type=Path, help="선택한 출력 장치의 소리를 WAV로 저장")
    parser.add_argument("--device", type=int, help="--devices에서 확인한 루프백 장치 번호")
    parser.add_argument("--seconds", type=float, help="녹음 길이(초)")
    parser.add_argument("--transcribe-after", action="store_true", help="녹음 파일을 이어서 전사")
    parser.add_argument("--model", default="small", help="로컬 Whisper 모델, 기본 small")
    parser.add_argument("--output-dir", type=Path, help="전사 결과 폴더")
    parser.add_argument(
        "--inspect", action="store_true", help="선택한 강의의 첫 회차 입력 지원 여부"
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    def progress(value, message):
        print(f"{value * 100:5.1f}% {message}", flush=True)

    cancel = threading.Event()
    try:
        if args.devices or args.record:
            from pickiclass.audio_capture import LoopbackRecorder, list_loopback_devices

            if args.devices:
                for device in list_loopback_devices():
                    print(f"{device['index']}\t{device['name']}")
                return 0
            if args.device is None or args.seconds is None:
                parser.error("--record에는 --device와 --seconds가 필요합니다.")
            print("선택한 출력 장치의 다른 앱 소리도 포함됩니다. 녹음할 강의를 재생하세요.")
            source = LoopbackRecorder().record(
                args.record, args.seconds, args.device, progress, cancel
            )
            print(f"WAV 저장: {source}")
            if not args.transcribe_after:
                return 0
        else:
            source = args.transcribe
        if source:
            from pickiclass.transcription import Transcriber

            result = Transcriber(model_size=args.model).transcribe(
                source, output_dir=args.output_dir, on_progress=progress, cancel=cancel
            )
            for path in (result.txt_path, result.srt_path, result.json_path):
                print(path)
            return 0
        from pickiclass.client import PickiclassClient

        load_dotenv(Path(__file__).with_name(".env"))
        client = PickiclassClient()
        client.login(
            os.getenv("pickiclass_id") or os.getenv("PICKICLASS_ID", ""),
            os.getenv("pickiclass_pw") or os.getenv("PICKICLASS_PW", ""),
        )
        courses = client.list_my_courses()
        if args.list:
            for course in courses:
                print(f"{course.course_id}\t{course.title}")
            return 0
        course = next((course for course in courses if course.course_id == args.course_id), None)
        if course is None:
            parser.error("현재 수강 목록에서 해당 강의를 찾지 못했습니다.")
        lessons = client.list_lessons(course)
        print(f"{course.title}: {len(lessons)}회차")
        for lesson in lessons:
            print(f"{lesson.lesson_id}\t{lesson.duration_sec or '?'}초\t{lesson.title}")
        if args.inspect and lessons:
            source = client.get_video_source(course.course_id, lessons[0].lesson_id)
            print("일반 미디어 입력 확인:", "MP4/오디오" if source.mp4_url else "HLS")
        return 0
    except KeyboardInterrupt:
        cancel.set()
        print("중단했습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        # 외부 예외의 URL·요청 내용이 인증 토큰을 포함할 수 있어 알려진 오류만 표시한다.
        from pickiclass.audio_capture import AudioCaptureError
        from pickiclass.client import LoginError, SourceUnavailableError
        from pickiclass.transcription import TranscriptionError

        if isinstance(
            exc,
            (
                LoginError,
                SourceUnavailableError,
                TranscriptionError,
                AudioCaptureError,
                FileNotFoundError,
                FileExistsError,
                ValueError,
            ),
        ):
            print(str(exc), file=sys.stderr)
        else:
            print("작업을 완료하지 못했습니다. 입력과 연결 상태를 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
