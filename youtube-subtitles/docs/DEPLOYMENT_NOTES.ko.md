# 클라우드 배포 기록: Hugging Face Spaces와 Render

이 문서는 YouTube Subtitles 앱을 무료 클라우드에 올리려고 시도한 과정과 결과를 기록한 것입니다. 최종 선택은 Mac mini에서 직접 실행하고 Tailscale Funnel로 공개하는 방식이었고, 그 이유와 함께 나중에 클라우드로 되돌아갈 때 필요한 절차, 파일, 주의점을 남겨 둡니다. 배포용 파일(`render.yaml`, `.github/workflows/sync-hf-space.yml`, `scripts/deploy_hf_space.py`, `Dockerfile`, `start.sh`)은 저장소에 그대로 유지되어 있어 언제든 다시 쓸 수 있습니다.

## 1. 요약

| 항목 | Hugging Face Spaces | Render | Mac mini + Tailscale Funnel (최종) |
| --- | --- | --- | --- |
| 비용 | Docker Space는 PRO 구독 필요 | 무료 (15분 유휴 시 잠듦) | 무료 (전기와 Tailscale 무료 계정) |
| 배포 방식 | Docker SDK Space, GitHub Action 또는 스크립트로 동기화 | Blueprint(`render.yaml`)로 Docker 서비스 자동 생성 | LaunchAgent로 uvicorn 상시 실행 |
| 결과 | 계정 제한(402)으로 생성 불가, 중단 | 배포는 성공했으나 유튜브가 데이터센터 IP를 차단해 자막 수집 실패 | 안정 동작, MCP 커넥터까지 확인 |
| 남은 자산 | 스크립트와 Action, README 절차 | Blueprint와 Docker 이미지(PO 토큰 제공기 포함) | 설치 스크립트, 이용 매뉴얼 |

핵심 교훈은 하나입니다. 유튜브는 데이터센터 IP에서 오는 요청을 봇 검사, PO 토큰 요구, API 페이지 403의 세 단계로 막으며, 쿠키와 PO 토큰 제공기로 앞의 두 단계는 넘길 수 있어도 마지막 단계는 넘기지 못했습니다. 가정용 IP에서는 이 세 가지가 모두 나타나지 않았습니다.

## 2. Hugging Face Spaces

### 2.1 준비한 것

- README 맨 앞에 Space 프런트매터(`sdk: docker`, `app_port: 7860`)를 넣어 `youtube-subtitles/` 폴더가 그대로 Space 저장소가 되도록 했습니다.
- Dockerfile은 Spaces가 요구하는 포트 7860과 uid 1000을 사용합니다.
- 터미널 배포 스크립트 `scripts/deploy_hf_space.py`: Space 생성(Docker SDK, 기본 비공개), `MCP_API_KEY` 비밀 설정(없으면 생성해 한 번 출력), `PUBLIC_BASE_URL` 변수 설정, 폴더 업로드, 웹 주소와 MCP 주소 출력까지 한 번에 합니다. `--cookies-file`, `--public`, `--mcp-key` 옵션이 있습니다.
- GitHub Action `sync-hf-space.yml`: main에 푸시될 때 `youtube-subtitles/`만 잘라(`git subtree split`) Space 저장소의 main으로 강제 푸시합니다. 저장소 시크릿 `HF_TOKEN`(쓰기 토큰)과 변수 `HF_SPACE`(`사용자명/스페이스명`)가 없으면 알림만 남기고 건너뜁니다.
- 쿠키는 파일을 올릴 수 없는 환경을 위해 환경 변수 `YTDLP_COOKIES_CONTENT`(파일 내용 전체)를 받는 기능을 추가했습니다. 프로세스마다 한 번 0600 권한의 임시 파일로 써서 yt-dlp에 넘기고, 시크릿 UI가 줄바꿈을 `\n` 문자로 눌러 놓아도 되돌립니다.

### 2.2 실제 진행과 결과

브라우저 자동화로 Hugging Face에 로그인해 Space를 만들려 했으나, 무료 계정에서는 Docker Space 생성이 거부되었습니다. API 응답은 402 Payment Required였고, 화면에서도 PRO 구독을 요구했습니다. 무료를 전제로 했으므로 여기서 중단했습니다.

### 2.3 나중에 다시 쓸 때

1. PRO 구독이 있는 계정에서 https://huggingface.co/settings/tokens 에서 write 토큰을 만듭니다.
2. 가장 빠른 길은 터미널입니다: `pip install -U "huggingface_hub[cli]"`, `hf auth login`, `python scripts/deploy_hf_space.py --space 사용자명/youtube-subtitles`.
3. GitHub 동기화를 쓰려면 저장소 Settings의 Secrets and variables에서 `HF_TOKEN` 시크릿과 `HF_SPACE` 변수를 넣습니다. 그 뒤 main 푸시마다 Space가 다시 빌드됩니다. Actions 탭에서 수동 실행도 됩니다.
4. Space의 Settings에서 `YTDLP_COOKIES_CONTENT`를 Secret으로 넣으면 봇 검사를 넘길 수 있지만, 3절의 Render 경험상 데이터센터 IP의 한계는 같을 가능성이 큽니다.
5. 동기화는 강제 푸시이므로 코드는 GitHub에서만 고치고 Space 안에서 편집하지 않습니다.

## 3. Render

### 3.1 준비한 것

- 저장소 루트의 `render.yaml`(Blueprint): 무료 플랜의 Docker 웹 서비스, 싱가포르 리전, `rootDir: youtube-subtitles`, 헬스체크 `/healthz`, main 푸시 시 자동 배포, `MCP_API_KEY` 자동 생성.
- `PUBLIC_BASE_URL`은 생략 가능합니다. 앱이 Render가 주는 `RENDER_EXTERNAL_URL`을 `/openapi.json`과 다운로드 링크에 씁니다.
- 브라우저 자동화로 Render 대시보드의 New > Blueprint 흐름을 진행했고, 사용자가 Blueprint 이름을 넣고 Apply를 눌러 서비스가 만들어졌습니다. 첫 빌드는 5분에서 8분 걸렸고 `https://youtube-subtitles-해시.onrender.com` 주소를 받았습니다.

### 3.2 부딪힌 문제와 대응 (시간순)

1. **봇 검사**: 첫 요청부터 "Sign in to confirm you're not a bot"이 돌아왔습니다. 대응으로 쿠키 지원을 넣었습니다.
2. **읽기 전용 Secret File**: Render의 Secret File은 읽기 전용 경로에 놓이는데 yt-dlp는 쿠키 파일에 쓰기를 시도해 실패했습니다. 앱이 쿠키 파일을 쓰기 가능한 임시 위치로 복사해 넘기도록 고쳤습니다. 이후에는 파일 대신 `YTDLP_COOKIES_CONTENT` 환경 변수를 썼습니다.
3. **"자막 없음"**: 자막이 분명히 있는 영상에서 자막 목록이 비어 나왔습니다. 원인은 PO 토큰이었습니다. 데이터센터 IP나 로그인 쿠키가 있는 요청에 유튜브가 자막 주소에 `exp=xpe` 또는 `xpv` 표시를 붙이고, yt-dlp는 PO 토큰이 없으면 그런 트랙을 모두 버립니다. 대응으로 Docker 이미지에 bgutil PO 토큰 제공기(Node 서비스, 127.0.0.1:4416, 메모리 약 100 MB)와 yt-dlp 플러그인을 넣고, `start.sh`가 제공기를 먼저 띄운 뒤 uvicorn을 실행하도록 했습니다. `/healthz`가 `pot_provider.reachable`을 알려 주고, 제공기가 없으면 API가 "자막 없음" 대신 PO 토큰 문제를 지목하는 메시지를 돌려줍니다. 이 단계는 성공해 자막이 잠시 나왔습니다.
4. **API 페이지 403**: 곧이어 유튜브가 데이터센터 IP에서 오는 요청 자체를 403으로 거부했습니다. 쿠키와 PO 토큰으로도 넘을 수 없는 단계였습니다.
5. **쿠키 무효화**: 같은 구글 계정으로 다른 곳에서 로그아웃하면 서버의 쿠키가 즉시 무효가 됩니다. 시크릿 창에서 보조 계정으로 로그인해 내보내고 창을 닫는 절차, 보조 계정 쿠키로 교체하는 시도까지 했지만 4번 문제는 그대로였습니다.
6. **이용 한도 우려**: 하루 몇 회까지 안전한지에 대한 확정 답은 없습니다. 유튜브가 공개한 수치가 없고, 차단은 횟수보다 IP 종류와 요청 형태에 더 좌우되는 것으로 관찰되었습니다.

### 3.3 결론

Render 배포 자체는 정상이었고 Blueprint, Docker 이미지, PO 토큰 제공기, 쿠키 처리까지 모두 동작했습니다. 실패한 것은 유튜브가 데이터센터 IP를 차단하는 마지막 단계였습니다. 그래서 상시 켜 둘 수 있는 Mac mini에서 앱을 직접 실행하고, Tailscale Funnel로 안정된 공개 HTTPS 주소를 얻는 방식으로 옮겼습니다. 가정용 IP에서는 쿠키도 PO 토큰 제공기도 필요 없었습니다.

### 3.4 나중에 다시 쓸 때

1. Render 대시보드에서 New > Blueprint, 저장소 선택, 브랜치 main, Apply. `MCP_API_KEY`는 서비스의 Environment 탭에서 읽습니다. MCP 주소는 `서비스주소/mcp?key=그값`입니다.
2. 봇 검사가 나오면 Environment에 `YTDLP_COOKIES_CONTENT`를 넣습니다. 보조 계정을 시크릿 창에서 로그인해 "Get cookies.txt LOCALLY" 확장으로 내보낸 내용을 씁니다.
3. 403이 계속되면 데이터센터 IP 차단이므로 `YTDLP_PROXY`에 주거용 프록시를 넣는 방법이 남습니다. 무료 범위를 벗어나므로 이번에는 시도하지 않았습니다.
4. 무료 플랜은 15분 유휴 후 잠들어 첫 요청이 30초에서 60초 걸립니다. main에 푸시할 때마다 자동으로 다시 배포됩니다.

## 4. 이 과정에서 코드에 남은 기능

- `YTDLP_COOKIES`(파일 경로)와 `YTDLP_COOKIES_CONTENT`(파일 내용) 두 가지 쿠키 입력, 읽기 전용 경로 대응.
- PO 토큰 제공기 연동(`POT_PROVIDER_URL`, 기본 127.0.0.1:4416)과 상태 보고, PO 토큰 때문에 자막이 사라졌을 때의 진단 메시지.
- Docker 다단계 빌드(Node 22로 제공기 빌드, python 3.11-slim 실행), `start.sh`.
- 기계 번역 자막의 429에 대한 재시도(2, 4, 8초)와 안내, 이후 기계 번역 자막을 목록에서 제외.
- Render의 `RENDER_EXTERNAL_URL` 인식, `/openapi.json`의 서버 주소 자동 설정.

이 기능들은 Mac에서도 그대로 들어 있으며, 가정용 IP에서 언젠가 봇 검사가 시작되면 같은 절차로 대응할 수 있습니다.
