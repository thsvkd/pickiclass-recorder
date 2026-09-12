import importlib
import json
import threading
from types import SimpleNamespace

import pytest


def api():
    return importlib.import_module('pickiclass.transcription')


class Model:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, source, **kwargs):
        return iter(self.segments), SimpleNamespace(duration=80.0, language='ko')


def segment(start=0.0, end=1.0, text=' 안녕하세요 '):
    return SimpleNamespace(start=start, end=end, text=text)


def setup(tmp_path, segments):
    source = tmp_path / 'lecture.wav'
    source.write_bytes(b'fixture')
    transcriber = api().Transcriber()
    transcriber._model = Model(segments)
    return source, transcriber


def test_outputs_original_timestamps_and_unicode(tmp_path):
    source, transcriber = setup(tmp_path, [segment(59.9996, 65.125), segment(70, 72, ' 두 번째 문장 ' )])
    progress = []
    result = transcriber.transcribe(source, on_progress=lambda value, text: progress.append(value))
    assert result.txt_path.read_text('utf-8') == '안녕하세요\n두 번째 문장\n'
    assert result.srt_path.read_text('utf-8') == '1\n00:01:00,000 --> 00:01:05,125\n안녕하세요\n\n2\n00:01:10,000 --> 00:01:12,000\n두 번째 문장\n\n'
    data = json.loads(result.json_path.read_text('utf-8'))
    assert data['segments'][0] == {'start': 59.9996, 'end': 65.125, 'text': '안녕하세요'}
    assert data['language'] == 'ko'
    assert progress[-1] == 1.0
    assert progress == sorted(progress)


def test_existing_output_is_preserved(tmp_path):
    source, transcriber = setup(tmp_path, [segment()])
    existing = tmp_path / 'lecture.srt'
    existing.write_text('existing', encoding='utf-8')
    with pytest.raises(FileExistsError):
        transcriber.transcribe(source)
    assert existing.read_text('utf-8') == 'existing'
    assert not (tmp_path / 'lecture.txt').exists()


def test_no_speech_leaves_no_outputs(tmp_path):
    source, transcriber = setup(tmp_path, [segment(text='   ')])
    with pytest.raises(api().TranscriptionError, match='음성'):
        transcriber.transcribe(source)
    assert list(tmp_path.iterdir()) == [source]


def test_cancel_during_iteration_leaves_no_outputs(tmp_path):
    cancellation = threading.Event()
    def segments():
        yield segment()
        cancellation.set()
        yield segment(2, 3)
    source, transcriber = setup(tmp_path, segments())
    with pytest.raises(api().TranscriptionCancelled):
        transcriber.transcribe(source, cancel=cancellation)
    assert list(tmp_path.iterdir()) == [source]


def test_decoder_failure_is_sanitized_and_cleans_files(tmp_path):
    def segments():
        yield segment()
        raise RuntimeError('SECRET_AUTH_VALUE')
    source, transcriber = setup(tmp_path, segments())
    with pytest.raises(api().TranscriptionError) as error:
        transcriber.transcribe(source)
    assert 'SECRET_AUTH_VALUE' not in str(error.value)
    assert list(tmp_path.iterdir()) == [source]


def test_invalid_timestamps_rejected(tmp_path):
    source, transcriber = setup(tmp_path, [segment(float('nan'), 2)])
    with pytest.raises(api().TranscriptionError):
        transcriber.transcribe(source)
    assert list(tmp_path.iterdir()) == [source]


def test_output_publication_failure_rolls_back_all_outputs(tmp_path, monkeypatch):
    source, transcriber = setup(tmp_path, [segment()])
    module = api()
    original = module.os.link
    calls = 0
    def failing_link(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('disk error')
        return original(src, dst)
    monkeypatch.setattr(module.os, 'link', failing_link)
    with pytest.raises(module.TranscriptionError):
        transcriber.transcribe(source)
    assert list(tmp_path.iterdir()) == [source]


def test_cancel_before_work_does_not_load_model(tmp_path):
    source, transcriber = setup(tmp_path, [])
    transcriber._model = None
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(api().TranscriptionCancelled):
        transcriber.transcribe(source, cancel=cancellation)
    assert transcriber._model is None
    assert list(tmp_path.iterdir()) == [source]
