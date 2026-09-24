# YouTube Subtitles 이용 매뉴얼

유튜브 영상 주소를 넣으면 자막을 내려받는 웹앱과, 같은 서버가 제공하는 MCP(Model Context Protocol) 커넥터의 사용 설명서입니다. 웹 브라우저에서 쓰는 방법, Claude Desktop 등 AI 앱에서 쓰는 방법, Mac mini 서버를 관리하는 방법, 문제가 생겼을 때의 대처 순서로 되어 있습니다.

## 1. 한눈에 보기

| 항목 | 내용 |
| --- | --- |
| 웹앱 주소 | `https://dukwoos-mac-mini.tailb8572b.ts.net/` |
| MCP 주소 | `https://dukwoos-mac-mini.tailb8572b.ts.net/mcp` (API 키 필요) |
| 실행 위치 | Mac mini의 LaunchAgent `com.youtube-subtitles`, 포트 7860, Tailscale Funnel로 공개 |
| 소스 코드 | GitHub `ImpactPath/AIAgents`의 `youtube-subtitles/` 폴더, 브랜치 `main` |
| 지원 형식 | TXT(기본), SRT, VTT |
| 지원 자막 | 업로더가 올린 자막(Original)과 영상 원어의 자동 생성 자막(auto) |

집 네트워크의 Mac에서 돌기 때문에 유튜브가 데이터센터 IP를 막는 문제를 피할 수 있고, Tailscale Funnel 덕분에 휴대폰이나 외부 PC 어디서나 같은 주소로 접속할 수 있습니다. Mac이 켜져 있고 잠들지 않아야 합니다.

## 2. 웹앱 사용법

### 2.1 자막 내려받기

1. 브라우저에서 웹앱 주소를 엽니다. 휴대폰에서도 같은 주소를 쓰면 됩니다.
2. 유튜브 영상 주소를 입력칸에 붙여 넣습니다. 입력칸 옆의 붙여넣기 버튼을 누르면 클립보드의 주소가 바로 들어갑니다. `youtube.com/watch?v=`, `youtu.be/`, shorts, live, embed 주소와 11자리 영상 ID 모두 됩니다.
3. 조회 버튼을 누르면 썸네일, 제목, 채널, 길이, 배포일이 나타납니다. 썸네일이나 제목을 누르면 유튜브 영상 페이지가 열립니다.
4. Subtitles 목록에서 자막 트랙을 고릅니다. 기본 선택은 추천 트랙입니다.
5. Format에서 TXT, SRT, VTT 중 하나를 고릅니다. TXT를 고르면 Text layout이 나타납니다.
6. Download를 누르면 파일이 저장되고, Preview를 누르면 아래 미리 보기 상자에 전체 내용이 나타납니다. 미리 보기의 Copy 버튼으로 전체 텍스트를 복사할 수 있습니다.

오른쪽 위의 EN/KO 버튼으로 화면 언어를 바꾸고, 그 옆 버튼으로 밝은 테마와 어두운 테마를 바꿉니다.

### 2.2 자막 트랙의 종류

| 표시 | 뜻 |
| --- | --- |
| `original` | 업로더가 직접 올린 자막입니다. 가장 정확하므로 있으면 첫 번째로 추천됩니다. |
| `auto` | 유튜브가 영상의 원래 음성 언어로 자동 생성한 자막입니다. 언어 코드 뒤에 `-orig`가 붙는 경우가 많습니다(예: `en-orig`). |

한국어 자막이 있으면 원어 다음 두 번째 자리에 고정해 보여 줍니다. 유튜브가 다른 언어로 기계 번역한 자막은 유튜브가 요청을 자주 거부(HTTP 429)하므로 목록에서 제외했습니다. 다른 언어가 필요하면 원어 자막을 받아 AI에게 번역을 맡기는 편이 빠르고 확실합니다.

### 2.3 파일 형식과 텍스트 정돈

| 형식 | 용도 |
| --- | --- |
| TXT | 읽거나 AI에 넣기 좋은 순수 텍스트입니다. 시간 정보가 없습니다. |
| SRT | 대부분의 영상 플레이어와 편집기가 읽는 자막 파일입니다. 시간 정보가 있습니다. |
| VTT | 웹 플레이어용 자막 파일입니다. 시간 정보가 있습니다. |

TXT의 Text layout은 줄바꿈을 어떻게 정돈할지 정합니다.

| 레이아웃 | 결과 |
| --- | --- |
| Paragraphs(기본) | 문장을 이어 붙이고, 2초 이상 쉬는 곳이나 약 600자마다 문단을 나눕니다. 읽기에 가장 편합니다. |
| Sentences | 한 문장을 한 줄에 둡니다. 인용이나 번역 대조에 좋습니다. |
| Original cues | 유튜브 자막의 원래 줄 그대로입니다. |

자동 생성 자막에는 구두점이 없는 경우가 있어 문장 나누기가 완벽하지 않을 수 있습니다. 그런 영상은 Original cues를 쓰면 됩니다.

### 2.4 내려받은 파일의 구조

모든 파일은 맨 앞에 메타데이터 섹션이 있고 그다음 자막 본문이 이어집니다. 메타데이터에는 Title, Channel, Duration, Published, URL, Subtitles(트랙 이름과 언어, original 또는 auto 표시)가 들어갑니다. TXT는 평문으로, VTT는 NOTE 블록으로, SRT는 0초 위치의 첫 번째 큐로 넣어 어떤 플레이어에서도 파일이 열립니다.

파일 이름은 `영상제목.언어코드.txt` 형식이고 자동 생성 자막이면 `.auto`가 붙습니다(예: `제목.en-orig.auto.txt`). 제목에 파일 이름으로 쓸 수 없는 문자가 있으면 정리되고, 영어가 아닌 제목은 일부 환경에서 영상 ID로 대체될 수 있습니다.

## 3. Claude Desktop에서 MCP로 쓰기

### 3.1 커넥터 등록

1. Claude Desktop의 Settings에서 Connectors로 들어가 Add custom connector를 누릅니다.
2. 이름은 `Youtube Subtitles`처럼 알아보기 쉬운 것으로 정합니다. 이 이름을 대화에서 직접 부르게 되므로 짧을수록 편합니다.
3. URL에 `https://dukwoos-mac-mini.tailb8572b.ts.net/mcp`를 넣습니다.
4. 헤더에 `X-API-Key`를 추가하고 값에 서버의 API 키를 넣습니다. 헤더를 넣을 수 없는 클라이언트라면 URL 뒤에 `?key=API키`를 붙이는 방식도 됩니다.
5. 저장하면 Connected 표시가 뜹니다.

claude.ai 웹과 모바일 앱도 같은 Connectors 설정에서 등록합니다. Claude Code는 `claude mcp add --transport http youtube-subtitles "https://dukwoos-mac-mini.tailb8572b.ts.net/mcp?key=API키"`로 등록합니다. ChatGPT는 Settings의 Connectors에서 Developer mode로 만들거나, Custom GPT Action에 `https://dukwoos-mac-mini.tailb8572b.ts.net/openapi.json`을 가져오면 됩니다.

### 3.2 첫 메시지 쓰는 법

새 대화에서는 커넥터 이름을 링크와 함께 씁니다. 예를 들어 `Youtube Subtitles로 이 영상 자막 메뉴 보여줘: https://youtu.be/영상ID` 또는 `Use YouTube Subtitles for this: https://youtu.be/영상ID`입니다. 그러면 Claude가 곧바로 `get_video_info`를 호출하고 채팅 안에 메뉴 카드가 나타납니다.

링크만 보내면 Claude Desktop이 "Your connectors" 카드를 띄우고 멈출 수 있습니다. Desktop은 화면을 그리는 커넥터 도구를 서드파티 앱으로 분류해 대화마다 한 번 사용자 동의를 요구하기 때문입니다. 카드의 Use를 누르는 것만으로는 실행되지 않으므로 `커넥터를 사용해줘`처럼 메시지를 한 번 더 보내야 합니다. 첫 메시지에 커넥터 이름을 넣으면 이 단계가 생략됩니다. 같은 대화에서는 그 뒤로 링크만 보내도 됩니다.

원하는 작업이 이미 분명하면 메뉴 없이 바로 시킬 수도 있습니다. 예를 들어 `이 영상 자막 받아서 한국어로 요약해줘: 링크`라고 하면 Claude가 추천 트랙을 TXT로 가져와 바로 요약합니다.

### 3.3 메뉴 카드의 구성

메뉴 카드 위쪽에는 썸네일, 제목, 채널, 길이, 배포일이 있고 제목을 누르면 유튜브가 열립니다. 오른쪽 위의 화살표 아이콘은 확대 버튼입니다. 누르면 카드가 전체 화면으로 커지고 다시 누르면 채팅 크기로 돌아옵니다.

그 아래에 웹앱과 같은 세 가지 선택이 있습니다. 동작 버튼은 가장 많이 쓰는 Add to chat이 맨 왼쪽의 빨간 버튼이고, 그다음이 Download입니다. Subtitles에서 트랙, Format에서 TXT/SRT/VTT, Text layout에서 문단, 한 문장씩, 원본 줄을 고릅니다. 카드의 언어는 Desktop의 표시 언어를 따라 한국어 또는 영어로 나타납니다.

### 3.4 버튼별 동작

| 버튼 | 동작 | 자막 재조회 |
| --- | --- | --- |
| Add to chat(채팅에 넣기) | 자막 전문을 안내문과 함께 입력창에 넣습니다. 전송하면 자막이 대화에 들어가고, 이후 질문은 도구 호출 없이 바로 답합니다. 안내문에는 자막을 `제목_subtitle_언어.txt`(자동 생성이면 `_auto`가 붙음) 파일로 저장해 대화의 파일 목록에 올리라는 지시도 들어 있어, 파일 생성이 가능한 환경에서는 파일도 함께 만들어집니다. | 처음 한 번만 |
| Download(다운로드) | 고른 트랙과 형식으로 파일을 만들어 컴퓨터에 저장합니다. 저장이 안 되면 카드에 나타나는 링크를 누릅니다. 채팅에는 파일 카드가 남지 않습니다. | 서버 캐시 사용 |
| Preview(미리 보기) | 카드 안에 전체 텍스트를 보여 주고 복사 버튼을 제공합니다. | 서버 캐시 사용 |
| Summarize(요약) | 요약 요청문을 입력창에 채웁니다. 전송하면 Claude가 자막을 가져와 요약합니다. | 서버 캐시 사용 |
| Translate to Korean(한국어로 번역) | 한국어 번역 요청문을 입력창에 채웁니다. 길면 나누어 번역합니다. | 서버 캐시 사용 |
| Key points(핵심 정리) | 핵심 요점을 시간 표시와 함께 정리하라는 요청문을 채웁니다. | 서버 캐시 사용 |
| Summary report (.md)(요약 보고서) | 개요, 핵심 논지, 인용 요지, 주제 타임라인, 시사점으로 구성된 보고서를 Claude가 직접 써서 `.md` 파일로 내놓게 하는 요청문을 채웁니다. 채팅에는 세 줄 요약이 함께 나옵니다. | 서버 캐시 사용 |
| Open web app(웹앱 열기) | 브라우저에서 웹앱을 엽니다. | 없음 |

입력창을 채우는 버튼을 누르면 Desktop이 붉은 경고 배너("Use caution before running this prompt")를 보여 줍니다. 앱이 입력창을 채울 때 Desktop이 항상 붙이는 문구이므로 정상입니다. 내용을 확인하고 전송하면 됩니다.

자막 재조회에 대해서는 두 가지 장치가 있습니다. 서버는 조회한 영상 정보와 내려받은 자막 본문을 1시간 동안 기억하므로, 미리 보기 뒤에 요약을 눌러도 유튜브에는 다시 요청하지 않습니다. 또 요약, 번역, 핵심 정리, 보고서 요청문은 항상 "자막이 이미 이 대화에 있으면 그것을 쓰고, 없을 때만 가져오라"는 조건문으로 시작하므로, Add to chat으로 자막을 넣어 둔 뒤에는 도구 호출 자체가 생략됩니다. 한 영상으로 여러 질문을 할 계획이면 Add to chat을 먼저 누르는 것이 가장 경제적입니다.

### 3.5 메뉴가 없는 클라이언트

메뉴 카드를 그리지 못하는 클라이언트(ChatGPT, Claude Code 등)에서는 서버가 같은 내용을 글로 돌려주고, AI가 두 단계로 묻습니다. 먼저 무엇을 할지(파일 다운로드, 요약, 번역, 핵심 정리)를 묻고, 다운로드를 고른 경우에만 트랙과 형식을 묻습니다. 파일을 요청하면 AI가 공개 다운로드 링크를 보여 주며, 이 링크는 어느 기기에서나 열립니다.

### 3.6 알아 둘 제한

- 메뉴 카드는 Claude가 그리는 아티팩트가 아니라 커넥터 앱 화면이므로 아티팩트 패널로 옮길 수 없습니다. 확대 버튼이 그 대안입니다.
- Claude는 자막 전문을 자기 답변으로 옮겨 적지 않습니다. 전문이 필요하면 Download, Preview, Add to chat 중 하나를 쓰면 됩니다.
- Download로 받은 파일과 도구가 첨부한 자막 리소스는 Desktop 채팅에 파일 카드로 표시되지 않습니다.

## 4. 서버 관리 (Mac mini)

### 4.1 자주 쓰는 명령

| 할 일 | 명령 |
| --- | --- |
| 상태 확인 | `curl -s http://127.0.0.1:7860/healthz` |
| 재시작 | `launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles` |
| 로그 보기 | `tail -n 40 ~/Library/Logs/youtube-subtitles.log` |
| 최신 코드 반영 | `cd /Users/dj/AIAgents && git pull && launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles` |
| 서비스 제거 | `launchctl bootout gui/$(id -u)/com.youtube-subtitles && rm ~/Library/LaunchAgents/com.youtube-subtitles.plist` |

상태 확인에서 `{"status":"ok", ...}`가 나오면 정상입니다. 로그의 각 줄에는 시각이 찍히므로 Desktop에서 본 현상과 대조할 수 있습니다. `MCP tools/call get_video_info` 같은 줄은 어떤 도구가 호출됐는지를 보여 줍니다.

### 4.2 API 키 바꾸기

1. 새 키를 만듭니다: `openssl rand -base64 24 | tr -d '/+=' | cut -c1-24`. 길이 제한은 없지만 무작위 영문과 숫자 20자 이상을 권합니다.
2. 설치 스크립트를 새 키로 다시 실행합니다: `cd /Users/dj/AIAgents/youtube-subtitles && scripts/mac/install_launch_agent.sh 새키 7860 https://dukwoos-mac-mini.tailb8572b.ts.net`.
3. Claude Desktop의 커넥터 설정에서 `X-API-Key` 값을 새 키로 바꿉니다. 옛 키는 서버가 재시작되는 순간부터 거부됩니다.

스크립트가 `Bootstrap failed: 5: Input/output error`로 끝나면 옛 서비스가 아직 정리 중인 것입니다. 설정 파일은 이미 저장됐으므로 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.youtube-subtitles.plist` 다음에 재시작 명령을 실행하면 됩니다.

### 4.3 Tailscale Funnel

공개 주소는 Tailscale Funnel이 포트 7860을 외부로 열어 만든 것입니다. 로그인할 때 Tailscale이 자동 시작되면 재부팅 뒤에도 유지됩니다. 주소가 안 열리면 Tailscale 앱이 실행 중인지 확인하고, 필요하면 `/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg 7860`을 다시 실행합니다. Mac은 시스템 설정에서 자동 잠자기를 끄고 자동 로그인을 켜 두어야 사람이 없어도 서비스가 살아 있습니다.

### 4.4 쿠키

가정용 IP에서는 보통 쿠키 없이 동작합니다. 유튜브가 "Sign in to confirm you're not a bot"을 돌려주기 시작하면 쿠키가 필요합니다. 보조 유튜브 계정으로 시크릿 창에서 로그인한 뒤 "Get cookies.txt LOCALLY" 확장으로 Netscape 형식의 `cookies.txt`를 내보내고, 그 창을 닫습니다. 파일을 Mac에 두고 환경 변수 `YTDLP_COOKIES`에 경로를 넣거나, 파일 내용을 `YTDLP_COOKIES_CONTENT`에 넣은 뒤 재시작합니다. 같은 계정으로 다른 곳에서 로그아웃하면 쿠키가 무효가 되므로 주의합니다. 쿠키는 몇 주에서 몇 달 뒤 만료되며 그때 다시 내보내면 됩니다.

### 4.5 주요 환경 변수

| 변수 | 뜻 |
| --- | --- |
| `MCP_API_KEY` | MCP 접근 키입니다. 비어 있으면 `/mcp`가 누구에게나 열립니다. |
| `PUBLIC_BASE_URL` | 공개 주소입니다. 도구가 돌려주는 다운로드 링크의 앞부분이 됩니다. |
| `YTDLP_COOKIES`, `YTDLP_COOKIES_CONTENT` | 쿠키 파일 경로 또는 내용입니다. |
| `YTDLP_PROXY` | yt-dlp가 쓸 프록시 주소입니다. |
| `CACHE_TTL_SECONDS` | 영상 정보와 자막 본문을 기억하는 시간(초)입니다. 기본 3600(1시간), 최소 60입니다. |
| `POT_PROVIDER_URL` | PO 토큰 제공기 주소입니다. Mac에서는 필요하지 않습니다. |

## 5. 문제 해결

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| 링크를 보냈는데 메뉴 대신 "Your connectors" 카드나 질문 목록이 나옴 | Desktop의 커넥터 동의 단계 | 첫 메시지에 커넥터 이름을 넣거나, `커넥터를 사용해줘`라고 한 번 더 보냅니다. |
| 커넥터가 401 오류 | API 키 불일치 | 커넥터 헤더의 키와 서버의 키가 같은지 확인합니다. |
| 웹앱 주소가 열리지 않음 | Mac이 잠들었거나 Tailscale 또는 서비스가 꺼짐 | Mac을 깨우고 상태 확인 명령과 Tailscale 실행 여부를 확인합니다. |
| "Sign in to confirm you're not a bot" | 유튜브의 봇 검사 | 4.4의 쿠키 절차를 따릅니다. |
| 자막이 있는 영상인데 "자막 없음" | 유튜브가 PO 토큰을 요구 | 로그에 PO 토큰 경고가 있으면 README의 안내대로 PO 토큰 제공기를 띄웁니다. Mac에서는 드뭅니다. |
| 한국어 등 번역 자막에서 429 오류 | 기계 번역 자막의 요청 제한 | 원어 자막을 받아 AI에게 번역을 맡깁니다. |
| Add to chat 뒤에도 Claude가 자막을 모름 | 입력창의 메시지를 전송하지 않음 | 입력창에 채워진 자막 메시지를 전송합니다. |
| 로컬 실행에서 `No module named 'app'` | 실행 위치가 다름 | `youtube-subtitles/` 폴더 안에서 uvicorn을 실행합니다. |
| 로그에 `pot:bgutil ... 127.0.0.1:4416` 경고 | PO 토큰 제공기가 꺼져 있음 | 자막이 정상이면 무시해도 됩니다. |

## 6. 개인정보와 이용 시 유의점

서버는 파일을 디스크에 저장하지 않고 영상 정보와 자막 본문을 메모리에 1시간 동안만 둡니다. 쿠키는 서버에만 두고 채팅에 붙여 넣지 않습니다. API 키가 화면 캡처 등으로 노출됐다고 생각되면 4.2의 절차로 바로 바꿉니다. 자막은 사용 권한이 있는 영상에 대해서만 내려받고, 유튜브 서비스 약관을 지켜야 합니다.
