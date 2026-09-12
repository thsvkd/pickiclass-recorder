"""Local media transcription; audio never leaves this process for an API.

The first run downloads model weights. Cancellation is cooperative between
decoded segments; model loading and an active inference call cannot be interrupted.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path


class TranscriptionError(RuntimeError):
    """A safe, user-facing transcription failure."""


class TranscriptionCancelled(TranscriptionError):
    pass


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    txt_path: Path
    srt_path: Path
    json_path: Path
    segments: tuple[TranscriptSegment, ...]


def _timestamp(seconds: float) -> str:
    milliseconds = int(seconds * 1000 + 0.5)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_int, milliseconds = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{seconds_int:02},{milliseconds:03}'


class Transcriber:
    def __init__(self, model_size='small', device='cpu', compute_type='int8', language='ko'):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(
        self,
        source: Path,
        output_dir: Path | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscriptionResult:
        """Write UTF-8 TXT/SRT/JSON without replacing any existing output.

        Progress is 0..1 and uses the original media timeline. Results are staged
        before publishing; ordinary errors roll back all outputs from this call.
        """
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f'입력 파일이 없습니다: {source}')
        directory = Path(output_dir).expanduser().resolve() if output_dir else source.parent
        paths = tuple(directory / f'{source.stem}.{suffix}' for suffix in ('txt', 'srt', 'json'))
        for path in paths:
            if path.exists() or path.is_symlink():
                raise FileExistsError(f'기존 전사 파일을 덮어쓰지 않습니다: {path}')

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise TranscriptionCancelled('전사를 취소했습니다.')

        def report(value, message):
            if on_progress:
                # Observer errors must not invalidate successfully saved files.
                try:
                    on_progress(value, message)
                except Exception:  # noqa: BLE001 - UI observers cannot fail the file transaction.
                    logging.getLogger(__name__).warning('전사 진행 표시를 갱신하지 못했습니다.')

        check_cancel()
        report(0.0, '전사 모델 준비 중 (첫 실행은 모델 다운로드 필요)')
        segments = []
        staged = []
        published = []
        try:
            model = self._load_model()
            check_cancel()
            raw_segments, info = model.transcribe(
                str(source), language=self.language, beam_size=5, vad_filter=True
            )
            duration = float(getattr(info, 'duration', 0) or 0)
            previous_progress = 0.0
            for raw in raw_segments:
                check_cancel()
                start, end = float(raw.start), float(raw.end)
                if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                    raise TranscriptionError('전사 엔진이 잘못된 시간 정보를 반환했습니다.')
                text = str(raw.text).strip()
                if text:
                    segments.append(TranscriptSegment(start, end, text))
                if math.isfinite(duration) and duration > 0:
                    previous_progress = max(previous_progress, min(end / duration, 0.99))
                report(previous_progress, '음성을 텍스트로 전사 중')
            check_cancel()
            if not segments:
                raise TranscriptionError('인식된 음성이 없습니다. 입력 파일의 음성을 확인하세요.')
            payloads = (
                ''.join(f'{segment.text}\n' for segment in segments),
                ''.join(
                    f'{index}\n{_timestamp(segment.start)} --> {_timestamp(segment.end)}\n'
                    f'{segment.text}\n\n'
                    for index, segment in enumerate(segments, 1)
                ),
                json.dumps(
                    {'language': getattr(info, 'language', self.language),
                     'segments': [asdict(segment) for segment in segments]},
                    ensure_ascii=False, indent=2, allow_nan=False,
                ) + '\n',
            )
            directory.mkdir(parents=True, exist_ok=True)
            for payload in payloads:
                check_cancel()
                with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', newline='\n', prefix='.transcript-',
                    suffix='.tmp', dir=directory, delete=False,
                ) as file:
                    staged.append(Path(file.name))
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
            for temporary, destination in zip(staged, paths, strict=True):
                check_cancel()
                # Hard-link publication is atomic and fails if destination exists.
                os.link(temporary, destination)
                published.append(destination)
            check_cancel()
        except (TranscriptionError, FileExistsError):
            for path in published:
                path.unlink(missing_ok=True)
            raise
        except Exception:  # noqa: BLE001 - do not expose decoder/model exception payloads.
            for path in published:
                path.unlink(missing_ok=True)
            raise TranscriptionError(
                '전사하지 못했습니다. 미디어 파일, 모델 다운로드 연결, 저장 권한을 확인하세요.'
            ) from None
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
        report(1.0, '전사 파일 저장 완료')
        return TranscriptionResult(*paths, segments=tuple(segments))
