# 내장 브라우저 실행 검증

## 구현

- pywebview 6.2.1 / WebView2, 기존 Flet 화면에서 앱 소유 재생 창 실행.
- requests의 pickiclass.com 세션 쿠키만 stdin 파이프로 전달. 비밀번호나 쿠키를 명령행·파일에 기록하지 않음.
- WebView2 CookieManager로 세션 전달. private_mode 사용, 외부 브라우저 열기 해제.
- 공식 Kollus Controller 3.0.7을 SRI 검증과 함께 로드. 정상 play 요청과 상태 이벤트만 사용.
- 미디어 복호화, 보호 설정 변경, iframe 내부 접근은 구현하지 않음.

## 실제 결과

- 내장 창 생성 및 21회차/플레이어 iframe 로딩 확인.
- Controller loaded 다음 progress 수신: 2.852135 → 5.775537 → 8.705949 → 11.881835 → 14.803209초.
- 전체 재생 길이 665.173초. 이후 pause, 위치 16.343303초.
- 별도 CLI 20초/장치77 녹음 시 무음 오류. WAV 게시 및 전사 없음.
- 일시정지와 녹음 시점이 겹쳐서 실제 재생 음성의 캡처 가능 여부는 판정 불가.
- 네이티브 화면 확인 도구는 foreground window did not report a process id 오류.
- 로그아웃 링크는 상세 페이지에 없어서 초기 authenticated=false 판정은 인증 실패 근거가 아님.
  이후 진단은 login_form 존재 여부로 변경함. 실제 플레이어 진행 신호는 확인됨.

## 남은 완료 조건

### 사용자 화면으로 확인한 정정

사용자는 영상이 약 3초 후 중단됐다고 보고했고, 화면에 오류 1002가 표시됐다.
메시지는 화면 녹화 또는 캡처 프로그램 감지이며, 감지 대상 이름은 Chrome Remote Desktop이다.
따라서 위 progress 이벤트는 지속 재생 성공의 증거가 아니다. 내장 브라우저에서도
플레이어의 프로그램 감지 정책이 적용되어 이번 재생 검증은 실패했다.
무음의 직접 원인이나 다른 환경에서의 녹음 가능 여부까지 이 화면만으로 확정하지 않는다.
원격 접근 도구나 확장 프로그램을 자동 종료·삭제하거나 감지 우회를 시도하지 않는다.

1. 내장 재생 시작 전 녹음 장치 준비, pause/error/close 감지 시 중단 처리.
2. 정상 출력 장치의 실제 강의 음성 확보와 전사 결과 확인.
3. 선택 회차 큐/원래 시간 기준 전사/완료 UI 통합.
4. 파일과 전사가 실제 생성된 경우에만 완료 처리.

## 근거

- https://pywebview.flowrl.com/api/
- https://docs.kollus.com/dev-guide/vod/player/web-player-controller/
