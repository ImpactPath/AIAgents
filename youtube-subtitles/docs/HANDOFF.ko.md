# 세션 인수인계 프롬프트: YouTube Subtitles 웹앱과 MCP 커넥터

아래 내용을 새 Claude Code 세션(저장소 ImpactPath/AIAgents 연결)의 첫 메시지로 그대로 붙여 넣으세요. 이어서 하고 싶은 작업을 마지막 줄에 덧붙이면 됩니다.

---

## 작업 방식

Fable이 기획과 감독을 맡고, 단순 구현은 Opus 서브에이전트에 맡길지 먼저 물어봐 주세요. 답변은 한국어로 하되 제 질문을 영어로 먼저 옮겨 적고, 문서와 코드 주석에 em dash를 쓰지 마세요. 진단은 추측보다 로그와 스크린샷 같은 증거를 먼저 모으고, 가설은 한 번에 하나만 적용하며, 확인되지 않은 것은 추정이라고 표시하세요. API 키와 쿠키 값은 절대 채팅에 적지 마세요.

## 프로젝트

유튜브 링크를 넣으면 자막을 내려받는 FastAPI 웹앱과, 같은 서버가 제공하는 MCP 커넥터입니다. 저장소 ImpactPath/AIAgents의 `youtube-subtitles/` 폴더, 브랜치 main이 기준이고, 작업 브랜치 `claude/youtube-subtitle-downloader-t9prnn`을 main과 같은 상태로 함께 푸시합니다. 이력은 다시 쓰지 마세요.

서버는 Mac mini에서 LaunchAgent `com.youtube-subtitles`로 실행되고 Tailscale Funnel로 `https://dukwoos-mac-mini.tailb8572b.ts.net` 에 공개되어 있습니다. 웹앱은 `/`, MCP는 `/mcp`이며 인증은 `X-API-Key` 헤더, `Authorization: Bearer`, URL의 `?key=` 세 가지를 받습니다. 변경을 Mac에 적용하는 명령은 `cd /Users/dj/AIAgents && git pull && launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles`이고, 로그는 `tail -n 40 ~/Library/Logs/youtube-subtitles.log`입니다.

## 먼저 읽을 문서 (이 순서로)

1. `docs/USER_GUIDE.ko.md`: 현재 기능과 사용법, 호스트별 등록 절차, 문제 해결 표.
2. `docs/MCP_LESSONS.ko.md`: 시행착오 18건, 호스트별 확인된 동작 표, 제작 가이드라인과 점검표. 새 기능을 설계하기 전에 4절과 5절을 꼭 읽으세요.
3. `README.md`: 기술 문서(API, MCP, 환경 변수, 배포, 주의점).
4. `docs/DEPLOYMENT_NOTES.ko.md`: 클라우드 배포 시도 기록. 클라우드로 옮길 생각이 있을 때만.
5. `docs/NEXT_SESSION_CHATGPT.ko.md`: ChatGPT에서 메뉴 카드를 띄우는 후속 과제 프롬프트. 그 작업을 할 때만.
6. `docs/examples/summary-report-example.ko.md`: 요약 보고서 버튼이 목표로 하는 결과물의 기준 예시.
7. `docs/ARCHITECTURE.ko.md`: 부품과 요청 흐름을 도식으로 정리한 구조 설명. 전체 그림이 필요할 때.

## 코드 지도

- `app/main.py` FastAPI 앱, REST API, `/mcp` 라우팅과 키 검사, 시각이 찍히는 로그.
- `app/youtube.py` yt-dlp 호출, 트랙 선별(Original은 업로더 자막, 자동 자막은 원어 `-orig`만, 기계 번역 자막 제외), 1시간 캐시(`CACHE_TTL_SECONDS`).
- `app/convert.py` 자막 파싱과 TXT, SRT, VTT 변환, 문단 정돈, 메타데이터 헤더.
- `app/mcp_server.py` MCP 서버. 도구 `get_video_info`(메뉴 앱이 붙어 있음), `get_subtitles`, `get_download_link`. 뒤의 두 도구는 `user_confirmed=true`와 프로세스 전역 조회 기록 게이트로 보호. 안내문은 조건문 없이 "링크가 오면 무조건 `get_video_info` 먼저".
- `ui/src/menu.ts`, `ui/src/menu.css`, `ui/index.html` 메뉴 카드 소스. 빌드 결과 `app/ui/menu.html`은 커밋 대상이며 `cd ui && npm run build`로 다시 만듭니다. 버튼: Add to chat(맨 왼쪽, 기본), Download, Preview, Summarize, Translate to Korean, Key points, Summary report (.md), Open web app, 헤더의 확대 버튼.
- 테스트: `python -m pytest -q tests`(209개), `cd ui && python test/smoke.py`(59개 검사, Playwright 호스트 하네스). 둘 다 통과한 뒤에만 푸시합니다.

## 확인된 호스트 동작 (요약)

- Claude Desktop과 claude.ai 웹: 메뉴 카드, 다운로드, 미리 보기, 채팅에 넣기, 요약 보고서 .md, 확대 버튼 모두 동작. Desktop은 화면 있는 도구를 서드파티 앱으로 분류해 대화마다 옵트인을 요구하므로 첫 메시지에 커넥터 이름을 함께 쓰면 바로 열립니다. `ui/update-model-context`는 모델에 전달되지 않아 쓰지 않고, 모델이 봐야 하는 내용은 `ui/message`로 입력창에 넣습니다. 채팅에 파일 카드는 생기지 않습니다.
- ChatGPT 웹(Plus): 설정의 Plugins에서 Create MCP App, URL에 `?key=`, 인증 없음으로 등록하고 대화에서 플러그인을 켜면 텍스트 메뉴와 두 단계 질문 경로로 동작. 메뉴 카드는 안 그려집니다. Codex 앱의 MCP 설정은 별개입니다.
- 서버 변경 뒤 확인은 새 카드(링크 다시 보내기)나 새 대화에서 합니다. 안내문이나 도구 정의를 바꿨을 때는 새 대화가 필요합니다.

## 요약 보고서의 현재 구조 (자주 손보는 부분)

Video info(다섯 줄 목록), Overview(두 문단), Key takeaways(5~7), Key arguments(8~10, 번호 소제목), Notable quotes (paraphrased)(6~8), Timeline of topics, 그리고 공공 정책, 기후, 에너지, 개발, 기술 거버넌스, 경제, 국제 협력 주제에서만 Key implications(정확히 6개, 한국과 국제기구가 대략 절반씩, 그중 두 개 이상에서 GGGI 명시)와 Key discussion points(6~7개 열린 질문). 20분 영상 기준 1,500~2,500단어, 선택 절 포함 시 3,000단어까지. 답변 끝에 자막 대조 점검을 제안하는 예/아니오 질문을 붙이고, 동의하면 수정본 v2를 냅니다. 이 구조는 `ui/src/menu.ts`의 `promptText` 함수 안 report 분기에 있습니다.

## 마무리 규칙

변경 뒤에는 두 테스트를 돌리고, main과 작업 브랜치에 푸시하고, README와 `docs/USER_GUIDE.ko.md`를 맞추고, 새로 배운 호스트 동작이나 시행착오가 있으면 `docs/MCP_LESSONS.ko.md`에 추가하세요. 요청하면 `1-app`, `2-deploy`, `3-docs`, `MANIFEST.ko.md` 구성의 zip을 만들어 주세요(`git archive` 결과에 저장소 루트의 `render.yaml`과 `.github/workflows/sync-hf-space.yml`, 문서 사본을 더한 구성).

## 이번에 할 일

(여기에 적으세요)
