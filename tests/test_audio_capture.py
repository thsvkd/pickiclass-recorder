import importlib
import threading
import wave
from types import SimpleNamespace

import pytest


def api():
    return importlib.import_module('pickiclass.audio_capture')


class Stream:
    def __init__(self, *, silent=False, cancel=None):
        self.frames = 0
        self.silent = silent
        self.cancel = cancel
        self.closed = False

    def read(self, frames, **kwargs):
        self.frames += frames
        if self.cancel:
            self.cancel.set()
        return (b'\x00\x00' if self.silent else b'\x00\x10') * frames * 2

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class Audio:
    def __init__(self, stream, loopback=True):
        self.stream = stream
        self.loopback = loopback
        self.terminated = False
        self.opened = False

    def get_device_info_by_index(self, index):
        return {'index': 4, 'name': 'Test speakers', 'isLoopbackDevice': self.loopback,
                'maxInputChannels': 2, 'defaultSampleRate': 8000}

    def get_loopback_device_info_generator(self):
        yield self.get_device_info_by_index(4)

    def get_default_wasapi_loopback(self):
        return self.get_device_info_by_index(4)

    def open(self, **kwargs):
        assert kwargs['input_device_index'] == 4
        assert kwargs['input'] is True
        self.opened = True
        return self.stream

    def terminate(self):
        self.terminated = True


def fixture(monkeypatch, *, silent=False, cancel=None, loopback=True):
    stream = Stream(silent=silent, cancel=cancel)
    audio = Audio(stream, loopback)
    monkeypatch.setattr(api(), '_load_backend', lambda: SimpleNamespace(
        PyAudio=lambda: audio, paInt16=8,
    ))
    return stream, audio


def test_exact_duration_wav_and_cleanup(tmp_path, monkeypatch):
    stream, audio = fixture(monkeypatch)
    destination = tmp_path / 'lecture.wav'
    result = api().LoopbackRecorder().record(destination, 1.25, 4)
    assert result == destination
    with wave.open(str(result), 'rb') as wav:
        assert wav.getnframes() == 10000
        assert wav.getnchannels() == 2
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 8000
        assert wav.readframes(1) == b'\x00\x10\x00\x10'
    assert stream.frames == 10000
    assert stream.closed and audio.terminated
    assert list(tmp_path.iterdir()) == [destination]


def test_existing_file_untouched(tmp_path, monkeypatch):
    _stream, audio = fixture(monkeypatch)
    destination = tmp_path / 'lecture.wav'
    destination.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        api().LoopbackRecorder().record(destination, 1, 4)
    assert destination.read_bytes() == b'keep'
    assert not audio.opened


@pytest.mark.parametrize('duration', [0, -1, 0.9, 21601, float('inf'), float('nan')])
def test_duration_bounds(tmp_path, duration):
    with pytest.raises(ValueError):
        api().LoopbackRecorder().record(tmp_path / 'lecture.wav', duration, 4)
    assert not list(tmp_path.iterdir())


def test_microphone_is_rejected(tmp_path, monkeypatch):
    _stream, audio = fixture(monkeypatch, loopback=False)
    with pytest.raises(api().AudioCaptureError, match='루프백'):
        api().LoopbackRecorder().record(tmp_path / 'lecture.wav', 1, 4)
    assert not audio.opened
    assert audio.terminated
    assert not list(tmp_path.iterdir())


def test_silence_is_failure_and_removes_partial(tmp_path, monkeypatch):
    stream, audio = fixture(monkeypatch, silent=True)
    with pytest.raises(api().AudioCaptureError, match='소리'):
        api().LoopbackRecorder().record(tmp_path / 'lecture.wav', 1, 4)
    assert stream.closed and audio.terminated
    assert not list(tmp_path.iterdir())


def test_cancel_removes_partial(tmp_path, monkeypatch):
    cancel = threading.Event()
    stream, audio = fixture(monkeypatch, cancel=cancel)
    with pytest.raises(api().AudioCaptureCancelled):
        api().LoopbackRecorder().record(tmp_path / 'lecture.wav', 1, 4, cancel=cancel)
    assert stream.closed and audio.terminated
    assert not list(tmp_path.iterdir())


def test_stop_publishes_audible_wav_before_duration(tmp_path, monkeypatch):
    stream, audio = fixture(monkeypatch)
    stop = threading.Event()
    original = stream.read

    def read(frames, **kwargs):
        data = original(frames, **kwargs)
        if stream.frames >= 2048:
            stop.set()
        return data

    stream.read = read
    destination = tmp_path / 'lecture.wav'
    api().LoopbackRecorder().record(destination, 5, 4, stop=stop)
    with wave.open(str(destination), 'rb') as wav:
        assert 2048 <= wav.getnframes() < 40000
    assert stream.closed and audio.terminated


def test_device_listing(monkeypatch):
    _stream, audio = fixture(monkeypatch)
    assert api().list_loopback_devices() == [
        {'index': 4, 'name': 'Test speakers', 'channels': 2, 'sample_rate': 8000},
    ]
    assert audio.terminated


def test_default_loopback_device(monkeypatch):
    fixture(monkeypatch)
    assert api().default_loopback_device() == {
        'index': 4, 'name': 'Test speakers', 'channels': 2, 'sample_rate': 8000,
    }


def test_output_created_during_capture_is_preserved(tmp_path, monkeypatch):
    stream, audio = fixture(monkeypatch)
    destination = tmp_path / 'lecture.wav'
    original_read = stream.read
    def read(frames, **kwargs):
        destination.write_bytes(b'created by another task')
        return original_read(frames, **kwargs)
    stream.read = read
    with pytest.raises(FileExistsError):
        api().LoopbackRecorder().record(destination, 1, 4)
    assert destination.read_bytes() == b'created by another task'
    assert list(tmp_path.iterdir()) == [destination]
    assert stream.closed and audio.terminated


def test_audio_overflow_fails_without_partial_output(tmp_path, monkeypatch):
    stream, audio = fixture(monkeypatch)
    def read(frames, **kwargs):
        if kwargs.get('exception_on_overflow'):
            raise OSError('input overflow')
        return b'\x00\x10' * frames * 2
    stream.read = read
    with pytest.raises(api().AudioCaptureError):
        api().LoopbackRecorder().record(tmp_path / 'lecture.wav', 1, 4)
    assert not list(tmp_path.iterdir())
    assert stream.closed and audio.terminated
