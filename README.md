# 피키클래스 파일 저장·전사

[pudufu-recorder](https://github.com/thsvkd/pudufu-recorder)의 Flet 화면을 기반으로 만든 피키클래스용 앱입니다.

피키클래스 영상 강의는 Kollus 암호화가 걸려 있어 일반 MP4로 내려받을 수 없습니다.
저장·전사를 누르면 앱이 소유한 WebView2 창에서 공식 플레이어를 재생하고,
선택한 출력 장치의 소리를 WAV로 저장한 뒤 로컬 Whisper로 전사합니다.

암호화 강의 재생에는 공식 Kollus Player V3 에이전트가 필요합니다. 처음 저장할 때 Kollus가 설치·업데이트 창을 띄우면 설치하세요.

원격 제어·화면 녹화 프로그램이 켜져 있으면 Kollus 오류 1002로 재생이 중단됩니다.
실측으로 확인된 감지 대상은 캡처 도구(SnippingTool), Steam, 그리고 Chrome Remote Desktop Host가 설치된 상태의 Chrome입니다.
Host가 설치돼 있으면 원격 접속 중이 아니어도 Chrome이 켜져 있는 동안 감지되므로, 저장 중에는 Chrome을 완전히 종료하세요.
Chrome을 닫아도 CRD 구성 요소 `remote_assistance_host.exe`가 혼자 남아 감지될 수 있으니 작업 관리자에서 종료하세요.
저장을 시작하기 전에 앱이 실행 중인 감지 대상을 확인해 안내하고, 재생 중 중단되면 Kollus가 감지한 프로그램 이름을 실패 메시지에 함께 표시합니다(Kollus 로그 `%TEMP%\KollusAgent.log` 기준).

[최신 설치 파일 받기](https://github.com/thsvkd/pickiclass-recorder/releases/latest)

## 설치

[최신 릴리스](https://github.com/thsvkd/pickiclass-recorder/releases/latest)에서 Windows 설치기를 받습니다.

- **Windows**: `PickiclassRecorder-win-Setup.exe`

설치하면 바로 실행됩니다. 이후 버전은 앱이 GitHub 릴리스에서 받아 업데이트합니다.

## 소스에서 실행

Windows에서 Python 3.12 또는 3.13과 [uv](https://docs.astral.sh/uv/)를 사용합니다.

```powershell
uv sync
uv run python main.py
```

`.env`의 `pickiclass_id`, `pickiclass_pw`를 로그인 화면에 불러옵니다. 앱의 기억하기 옵션은 아이디만 저장합니다.
처음 실행할 때 Flet 데스크톱 실행 파일이 준비됩니다. 전사는 첫 실행에서 Whisper small 모델을 내려받습니다.

## 구현한 흐름

- 기존 앱의 로그인·내 강의·회차 선택 화면을 유지합니다.
- 회차 선택에서 출력 장치를 고른 뒤 **저장·전사 시작**을 누르면, 암호화 회차는 앱 내장 재생 창을 열고 공식 Kollus Controller로 재생합니다.
- 재생이 실제로 진행되는 동안 선택한 출력 루프백을 WAV로 저장합니다. 무음·조기 일시정지·오류 1002는 성공으로 처리하지 않습니다.
- 암호화 회차의 결과 파일은 WAV입니다. 배속 변환은 음성에만 적용하고, 전사는 원래 속도 파일을 사용합니다.
- 원래 속도(1배속) 파일은 기본으로 `원본` 하위 폴더에 함께 보관합니다. **원본(1배속)도 함께 보관**을 끄면 전사 후 지웁니다.
- 일반 미디어 입력이 제공되는 회차는 원본 파일 저장 → 배속 변환 → 원래 속도 음성 전사로 처리합니다.
- 다운로드한 강의의 전사문은 `전사 (원본 시간)` 하위 폴더에 저장합니다. SRT는 원래 강의 시간 기준입니다.
- 로그인 화면과 강의 목록의 **로컬 파일 전사**에서 기존 영상·음성을 TXT·SRT·JSON으로 만듭니다.
- 같은 화면에서 출력 장치를 직접 선택해 소리를 WAV로 저장하고, 이어서 전사할 수 있습니다.
- 선택한 출력 장치를 사용하는 다른 앱 소리도 녹음됩니다. 마이크 입력은 선택할 수 없습니다.
- 기존 WAV·전사 결과는 덮어쓰지 않습니다.

전사는 로컬 CPU에서 수행하며 음성을 외부 API로 업로드하지 않습니다. 모델 준비 및 음성 인식 중 취소는 현재 구간이 끝난 뒤 반영될 수 있습니다.
전사 출력은 NTFS 로컬 폴더를 권장합니다. 현재 원자적 파일 게시에 하드링크를 사용하므로 일부 NAS·FAT 파일시스템에서는 실패할 수 있습니다.

## CLI

```powershell
uv run python cli.py --list
uv run python cli.py --course-id "목록에서 확인한 식별자"
uv run python cli.py --devices
uv run python cli.py --transcribe "C:\media\lesson.wav"
uv run python cli.py --record "C:\media\lesson.wav" --device 77 --seconds 600 --transcribe-after
```

장치 번호 77은 예시입니다. 반드시 `--devices`에 표시된 현재 번호를 사용하세요.
녹음은 지정한 시간 동안 실제 재생되는 소리를 저장하므로 재생시간이 필요합니다.

## 확인된 제약

4개 영상 강의의 첫 회차에서 Kollus 암호화 정책이 확인되었습니다. 일반 MP4로 직접 다운로드하는 경로는 확인되지 않았습니다.
내장 재생은 공식 플레이어 제어만 사용합니다. 보호 정책 우회, 원격 프로그램 강제 종료, 화면 녹화는 구현하지 않습니다.
Windows의 보호된 오디오 경로가 사용되는 경우 일반 루프백 녹음도 제한될 수 있습니다.
Kollus의 공식 오프라인 파일은 암호화 상태를 유지하므로 전사 입력으로 취급하지 않습니다.
Windows 설치 파일은 GitHub Releases에 올립니다. macOS 설치기는 아직 만들지 않았습니다.

## 검증

```powershell
uv run pytest
uv run ruff check pickiclass ui main.py cli.py tests
```

테스트는 합성 HTML·오디오와 격리된 파일을 사용합니다. 실제 계정이나 실제 강의를 자동으로 녹음하지 않습니다.
[조사 근거](docs/research/2026-09-12-feasibility.md)와 [구현 계획](docs/implementation-plan.md)을 함께 참고하세요.

## 출처

화면 및 영상 처리 기반: [thsvkd/pudufu-recorder](https://github.com/thsvkd/pudufu-recorder/tree/073514e436a15c8d8c63386e020c2d155ecbf29d), 원본 README에 표시된 MIT 라이선스.
