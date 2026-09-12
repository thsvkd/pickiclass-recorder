"""Record an explicitly selected Windows output loopback as PCM16 WAV.

Loopback includes every application audible on the selected output device.
This module never selects a microphone or starts/stops another application's playback.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
import threading
import wave
from array import array
from collections.abc import Callable
from pathlib import Path


class AudioCaptureError(RuntimeError):
    pass


class AudioCaptureCancelled(AudioCaptureError):
    pass


def _load_backend():
    if sys.platform != 'win32':
        raise AudioCaptureError('Windows에서만 출력 장치 루프백 녹음을 지원합니다.')
    import pyaudiowpatch

    return pyaudiowpatch


def _cleanup(action):
    try:
        action()
    except Exception:  # noqa: BLE001 - cleanup must not hide the original capture failure.
        logging.getLogger(__name__).warning('오디오 장치 자원 정리 중 오류가 발생했습니다.')


def list_loopback_devices() -> list[dict]:
    audio = None
    try:
        audio = _load_backend().PyAudio()
        return [_public_device(device) for device in audio.get_loopback_device_info_generator()
                if device.get('isLoopbackDevice') and int(device['maxInputChannels']) > 0]
    except AudioCaptureError:
        raise
    except Exception:  # noqa: BLE001 - expose a bounded device error, not backend diagnostics.
        raise AudioCaptureError('출력 루프백 장치 목록을 읽지 못했습니다.') from None
    finally:
        if audio is not None:
            _cleanup(audio.terminate)


def default_loopback_device() -> dict | None:
    audio = None
    try:
        backend = _load_backend()
        audio = backend.PyAudio()
        device = audio.get_default_wasapi_loopback()
        if device and device.get('isLoopbackDevice') and int(device['maxInputChannels']) > 0:
            return _public_device(device)
        return None
    except AudioCaptureError:
        raise
    except Exception:  # noqa: BLE001 - missing default is not a fatal listing error.
        return None
    finally:
        if audio is not None:
            _cleanup(audio.terminate)


def _public_device(device: dict) -> dict:
    return {
        'index': int(device['index']),
        'name': str(device['name']),
        'channels': int(device['maxInputChannels']),
        'sample_rate': int(device['defaultSampleRate']),
    }


class LoopbackRecorder:
    def record(
        self,
        destination: Path,
        duration_sec: float,
        device_index: int,
        on_progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
        stop: threading.Event | None = None,
    ) -> Path:
        duration = float(duration_sec)
        if not math.isfinite(duration) or not 1 <= duration <= 6 * 60 * 60:
            raise ValueError('녹음 시간은 1초 이상 6시간 이하여야 합니다.')
        if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
            raise ValueError('출력 루프백 장치를 명시적으로 선택해야 합니다.')
        destination = Path(destination).expanduser().resolve()
        if destination.suffix.lower() != '.wav':
            raise ValueError('녹음 파일 확장자는 .wav여야 합니다.')
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f'기존 파일을 덮어쓰지 않습니다: {destination}')

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise AudioCaptureCancelled('녹음을 취소했습니다.')

        def report(value, message):
            if on_progress:
                try:
                    on_progress(value, message)
                except Exception:  # noqa: BLE001 - observer failures cannot break capture.
                    logging.getLogger(__name__).warning('녹음 진행 표시를 갱신하지 못했습니다.')

        check_cancel()
        audio = stream = temporary = None
        try:
            backend = _load_backend()
            audio = backend.PyAudio()
            info = audio.get_device_info_by_index(device_index)
            if not info.get('isLoopbackDevice') or int(info['maxInputChannels']) < 1:
                raise AudioCaptureError('마이크가 아닌 출력 루프백 장치를 선택해야 합니다.')
            channels, sample_rate = int(info['maxInputChannels']), int(info['defaultSampleRate'])
            if sample_rate <= 0:
                raise AudioCaptureError('선택한 루프백 장치의 샘플 속도가 유효하지 않습니다.')
            total_frames = int(duration * sample_rate + 0.5)
            if total_frames * channels * 2 > 0xFFFFFFFF - 36:
                raise AudioCaptureError('WAV 파일 크기 제한을 넘습니다. 녹음 시간을 줄여주세요.')
            check_cancel()
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix='.capture-', suffix='.tmp', dir=destination.parent, delete=False,
            ) as file:
                temporary = Path(file.name)
            stream = audio.open(
                format=backend.paInt16, channels=channels, rate=sample_rate,
                input=True, input_device_index=device_index, frames_per_buffer=1024,
            )
            report(0.0, '선택한 출력 장치의 소리를 저장 중')
            recorded = audible_samples = 0
            with wave.open(str(temporary), 'wb') as output:
                output.setnchannels(channels)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                while recorded < total_frames:
                    check_cancel()
                    if stop is not None and stop.is_set():
                        break
                    count = min(1024, total_frames - recorded)
                    # Overflow must fail explicitly instead of silently dropping speech.
                    chunk = stream.read(count, exception_on_overflow=True)
                    check_cancel()
                    if len(chunk) != count * channels * 2:
                        raise AudioCaptureError('오디오 입력이 중단되었습니다. 파일을 저장하지 않았습니다.')
                    samples = array('h', chunk)
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    audible_samples += sum(abs(sample) >= 32 for sample in samples)
                    output.writeframesraw(chunk)
                    recorded += count
                    report(min(recorded / total_frames, 0.99), '출력 소리 저장 중')
            check_cancel()
            if audible_samples < sample_rate * channels * 0.01:
                raise AudioCaptureError('저장할 소리가 감지되지 않았습니다. 재생과 출력 장치를 확인하세요.')
            # Windows rename fails if a competing writer created the destination.
            # This also supports FAT/exFAT, unlike hard-link publication.
            if os.name == 'nt':
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        except (AudioCaptureError, FileExistsError):
            raise
        except Exception:  # noqa: BLE001 - never expose backend exception payloads to UI.
            raise AudioCaptureError(
                '출력 소리를 저장하지 못했습니다. 장치 사용 상태와 저장 권한을 확인하세요.'
            ) from None
        finally:
            if stream is not None:
                _cleanup(stream.stop_stream)
                _cleanup(stream.close)
            if audio is not None:
                _cleanup(audio.terminate)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        report(1.0, 'WAV 파일 저장 완료')
        return destination
