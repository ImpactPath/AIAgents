# 다음 세션용 프롬프트: ChatGPT와 Claude 양쪽에서 동작하는 MCP 구성

아래 내용을 새 Claude Code 세션(저장소 ImpactPath/AIAgents 연결)의 첫 메시지로 그대로 붙여 넣으면 됩니다. 패키지 zip을 올릴 필요는 없습니다. 코드와 문서가 모두 저장소 main에 있습니다.

---

## 작업 방식

Fable이 기획과 감독을 맡고, 단순 구현은 Opus 서브에이전트에 맡길지 먼저 물어봐 주세요. 답변은 한국어로 하되 제 질문을 영어로 먼저 옮겨 적고, 문서와 코드 주석에 em dash를 쓰지 마세요. 진단은 추측보다 로그와 스크린샷 같은 증거를 먼저 모으고, 가설은 한 번에 하나만 적용하세요. API 키는 절대 채팅에 적지 마세요.

## 저장소와 사전 읽기

저장소 ImpactPath/AIAgents의 `youtube-subtitles/` 폴더, 브랜치 main입니다. 작업 브랜치 `claude/youtube-subtitle-downloader-t9prnn`도 main과 같은 상태이며 두 브랜치에 함께 푸시합니다. 이력은 다시 쓰지 마세요(Mac에 배포된 복사본이 `git pull`로 갱신됩니다).

시작 전에 이 순서로 읽어 주세요: `docs/MCP_LESSONS.ko.md`(지난 세션의 시행착오와 가이드라인, 특히 5절 호스트별 동작 표), `README.md`의 MCP 절, `docs/USER_GUIDE.ko.md` 3절, `app/mcp_server.py`, `ui/src/menu.ts`, `tests/test_mcp.py`, `ui/test/smoke.py`.

## 현재 상태 (확인된 사실)

- 서버는 Mac mini에서 LaunchAgent로 실행되고 Tailscale Funnel로 `https://dukwoos-mac-mini.tailb8572b.ts.net` 에 공개되어 있습니다. MCP 엔드포인트는 `/mcp`, 인증은 `X-API-Key` 헤더, `Authorization: Bearer`, URL의 `?key=` 세 가지를 받습니다. 키 없이 오면 401입니다.
- Claude Desktop과 claude.ai 웹에서는 정상 동작합니다. `get_video_info`에 MCP 앱 메뉴 카드가 붙어 있고, `get_subtitles`와 `get_download_link`는 `user_confirmed=true`와 프로세스 전역의 조회 기록 게이트로 보호됩니다. 메뉴를 그리지 못하는 클라이언트를 위해 서버가 텍스트 메뉴와 두 단계 질문을 돌려주는 경로가 이미 있습니다(클라이언트가 초기화 때 앱 지원을 선언하지 않으면 그 경로).
- 로그는 `~/Library/Logs/youtube-subtitles.log`에 시각과 함께 남고, `MCP client connected: 이름 버전, protocol, extensions, apps=` 줄로 어떤 클라이언트가 붙었는지, `MCP tools/call 이름` 줄로 어떤 도구가 불렸는지 알 수 있습니다.
- 테스트: `python -m pytest -q tests`(209개), `cd ui && npm run build && python test/smoke.py`(56개 검사). 메뉴 화면을 고치면 `app/ui/menu.html`을 다시 빌드해 커밋해야 합니다.

## 문제

ChatGPT 웹(Plus, Apps & Connectors의 Developer mode, 화면에서는 plugin이라고 부름)에 커넥터를 설치했는데, 대화에서 "YouTube Subtitles MCP is not exposed as a callable connector in this chat session"이라며 도구를 호출하지 못합니다. 그 전에 ChatGPT는 같은 URL에 대해 401을 받았다고 했습니다. Codex 앱의 MCP 설정 화면(Streamable HTTP, Headers 입력 가능)으로도 등록을 시도했습니다.

가장 유력한 원인 후보는 두 가지이며 아직 확인되지 않았습니다. 첫째, ChatGPT 커넥터의 URL에 `?key=API키`가 빠져 있어 초기화가 401로 실패했고 그래서 도구 목록이 비어 있다. 둘째, ChatGPT는 Developer mode 커넥터를 대화마다 켜 줘야 하는데(입력창의 + 메뉴에서 커넥터 선택) 그 단계를 거치지 않았다.

## 목표

1. 진단: 제가 ChatGPT에서 링크를 보낸 직후 Mac 로그 40줄(`tail -n 40 ~/Library/Logs/youtube-subtitles.log`)을 보내면, ChatGPT 클라이언트가 초기화에 도달했는지, 어떤 이름과 프로토콜 버전으로 왔는지, 401이 있었는지 판정해 주세요. 필요한 스크린샷(커넥터 설정 화면, 대화의 도구 사용 기록)을 구체적으로 요청하세요.
2. ChatGPT 채팅 모드에서 텍스트 경로가 끝까지 동작하게 하기: 링크를 보내면 `get_video_info`가 호출되고, 두 단계 질문 뒤 `get_subtitles` 또는 `get_download_link`가 성공하며, 다운로드 링크가 클릭 가능하게 나와야 합니다. 서버 쪽에서 ChatGPT에 맞춰 고칠 것이 있으면(도구 설명, 안내문, 인증 방식, 세션 처리) 고치되 Claude 쪽 동작은 바꾸지 마세요.
3. 가능하면 ChatGPT에서도 메뉴가 보이게 하기: ChatGPT는 Claude의 MCP 앱 규격이 아니라 자체 Apps SDK 규격(도구 메타데이터 `openai/outputTemplate`, MIME `text/html+skybridge` 리소스, 화면 안의 `window.openai` API)을 씁니다. 같은 `get_video_info` 도구에 두 규격의 메타데이터와 화면 리소스를 함께 붙여 한 서버가 양쪽을 지원하는 구조를 설계하고, 먼저 최소 화면으로 ChatGPT에서 실제로 그려지는지 확인한 뒤 기능을 붙이세요. ChatGPT에서 이 기능이 Plus 플랜에 열려 있지 않다고 확인되면 그 사실을 문서에 적고 2번까지만 마무리하세요.
4. 문서: `docs/USER_GUIDE.ko.md`에 ChatGPT와 Codex 등록 절차를 추가하고, `docs/MCP_LESSONS.ko.md` 5절 표의 ChatGPT 열을 "미검증"에서 확인된 내용으로 바꾸며, 새로 겪은 시행착오는 2절 표에 추가하세요. README의 MCP 절도 맞추세요.
5. 마무리: 테스트 통과 확인 뒤 main과 작업 브랜치에 푸시하고, 제가 Mac에서 실행할 명령(`cd /Users/dj/AIAgents && git pull && launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles`)을 알려 주세요. 요청하면 `1-app`, `2-deploy`, `3-docs`, `MANIFEST.ko.md` 구성의 zip을 다시 만들어 주세요.

## 제약

- Claude Desktop과 claude.ai 웹에서 지금 되는 것(메뉴 카드, 다운로드, 미리 보기, 채팅에 넣기, 요약 보고서, 확대 버튼)은 그대로 되어야 합니다.
- 유튜브 관련 코드(`app/youtube.py`, `app/convert.py`)는 건드릴 이유가 없습니다.
- 확인되지 않은 것을 확인된 것처럼 적지 마세요. 추정은 추정이라고 표시하세요.

---

## 참고: 두 플랫폼 호환에 대한 사전 판단

프로토콜 층(도구, 리소스, 스트리밍 HTTP, 세션)은 이미 양쪽이 같은 서버를 쓸 수 있습니다. 다른 것은 세 가지입니다. 화면 규격이 서로 다르므로 메뉴를 양쪽에서 보이게 하려면 두 규격을 함께 실어야 합니다. 인증은 ChatGPT 웹 커넥터가 사용자 정의 헤더를 지원하지 않으므로 URL의 `?key=` 방식이나 OAuth를 써야 합니다. ChatGPT의 deep research 모드는 `search`와 `fetch`라는 이름의 도구를 요구하지만 일반 채팅 모드는 그렇지 않으므로 이번 범위에서는 채팅 모드만 다룹니다.
