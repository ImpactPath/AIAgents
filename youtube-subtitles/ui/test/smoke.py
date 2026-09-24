"""Playwright smoke test for the built MCP App view (../app/ui/menu.html).

A harness page embeds menu.html in a sandboxed iframe and plays the MCP Apps host over postMessage
(JSON-RPC 2.0, the envelopes the ext-apps PostMessageTransport sends). Run after `npm run build`:

    python3 test/smoke.py [--shots DIR]

Uses the preinstalled Chromium under /opt/pw-browsers (override with CHROMIUM_PATH).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
MENU = HERE.parent.parent / "app" / "ui" / "menu.html"

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
BASE = "https://subs.example.com"


def label_sets() -> dict:
    en = {
        "subtitles": "Subtitles", "format": "Format", "layout": "Text layout", "paragraphs": "Paragraphs",
        "sentences": "Sentences", "cues": "Original cues", "original": "original", "auto": "auto",
        "download": "Download", "preview": "Preview", "summarize": "Summarize", "translate": "Translate to Korean",
        "keypoints": "Key points", "copy": "Copy", "copied": "Copied", "openWeb": "Open web app",
        "selected": "Selected", "downloading": "Preparing file...",
        "downloadReady": "If the download did not start, open this link:", "loading": "Loading...",
        "error": "Something went wrong", "report": "Summary report (.md)", "addToChat": "Add to chat",
        "addedToChat": "Subtitles placed in the message box. Press send to add them to the chat.",
    }
    ko = {
        "subtitles": "자막", "format": "파일 형식", "layout": "텍스트 줄 정돈", "paragraphs": "문단",
        "sentences": "한 문장씩", "cues": "원본 줄", "original": "원본", "auto": "자동", "download": "다운로드",
        "preview": "미리 보기", "summarize": "요약", "translate": "한국어로 번역", "keypoints": "핵심 정리",
        "copy": "복사", "copied": "복사됨", "openWeb": "웹앱 열기", "selected": "선택됨",
        "downloading": "파일 준비 중...", "downloadReady": "다운로드가 시작되지 않으면 이 링크를 여세요:",
        "loading": "불러오는 중...", "error": "오류가 발생했습니다", "report": "요약 보고서 (.md)",
        "addToChat": "채팅에 넣기", "addedToChat": "자막을 입력창에 넣었습니다. 전송을 누르면 채팅에 추가됩니다.",
    }
    return {"en": en, "ko": ko}


TITLE = "Green growth in practice: lessons from ten years of national plans"
CHANNEL = "Global Green Growth Institute"
TRACKS = [
    {"lang": "ko", "name": "Korean", "auto": False, "translated": False, "kind": "original"},
    {"lang": "en", "name": "English", "auto": False, "translated": False, "kind": "original"},
    {"lang": "en-orig", "name": "English (auto-generated)", "auto": True, "translated": False, "kind": "auto"},
]

# get_video_info's result: structuredContent holds the model's fields and the view contract side by side.
SAMPLE_RESULT = {
    "content": [{"type": "text", "text": (
        f"Interactive menu shown for '{TITLE}' (3 tracks: ko, en, en-orig (auto)). The user is choosing a track, "
        "format and action in the menu. Wait for their choice; do not call get_subtitles or get_download_link "
        "until the user picks an action or asks explicitly."
    )}],
    "structuredContent": {
        # Fields for the model (the view ignores them).
        "video_id": "dQw4w9WgXcQ",
        "title": TITLE,
        "channel": CHANNEL,
        "duration_seconds": 1685,
        "duration": "28:05",
        "published": "2024-05-17",
        "url": VIDEO_URL,
        "original_language": "en",
        "options": {
            "formats": [
                {"id": "txt", "label": "Plain text"},
                {"id": "srt", "label": "SubRip (SRT), with timestamps"},
                {"id": "vtt", "label": "WebVTT (VTT), with timestamps"},
            ],
            "layouts": [
                {"id": "paragraphs", "label": "Paragraphs (sentences joined, blank line between paragraphs)"},
                {"id": "sentences", "label": "One sentence per line"},
                {"id": "cues", "label": "Original caption cues"},
            ],
            "actions": ["download_link", "summary", "translation", "key_points"],
        },
        "menu_hint": "If the interactive subtitle menu is shown, the user picks there: wait for their choice.",
        # Shared by both.
        "tracks": TRACKS,
        "recommended": {"lang": "en", "auto": False},
        # The view contract.
        "video": {
            "video_id": "dQw4w9WgXcQ",
            "title": TITLE,
            "channel": CHANNEL,
            "duration": "28:05",
            "duration_seconds": 1685,
            "published": "2024-05-17",
            "url": VIDEO_URL,
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
            "original_language": "en",
        },
        "formats": [{"id": "txt", "label": "TXT"}, {"id": "srt", "label": "SRT"}, {"id": "vtt", "label": "VTT"}],
        "layouts": [
            {"id": "paragraphs", "label": "Paragraphs"},
            {"id": "sentences", "label": "Sentences"},
            {"id": "cues", "label": "Original cues"},
        ],
        "defaults": {"fmt": "txt", "layout": "paragraphs"},
        "base_url": BASE,
        "download_template": BASE + "/api/download?url={video_id}&lang={lang}&auto={auto}&fmt={fmt}&layout={layout}&header=1",
        "labels": label_sets(),
    },
}

HOST_JS = r"""
const frame = document.getElementById('view');
window.hostLog = [];
window.hostErrors = [];
const send = (msg) => frame.contentWindow.postMessage(msg, '*');
const reply = (id, result) => send({ jsonrpc: '2.0', id, result });
const SUB_TEXT = 'Green growth in practice\nChannel: Global Green Growth Institute\n\n' +
  'Welcome everyone. Today we look at ten years of national green growth plans and what they taught us.\n\n' +
  'First, planning only works when finance ministries own the targets. Second, data systems matter more than slogans.';
window.addEventListener('message', (e) => {
  if (e.source !== frame.contentWindow) return;
  const m = e.data;
  if (!m || m.jsonrpc !== '2.0') { window.hostErrors.push('non JSON-RPC message'); return; }
  window.hostLog.push(m);
  switch (m.method) {
    case 'ui/initialize':
      reply(m.id, {
        protocolVersion: m.params.protocolVersion,
        hostInfo: { name: 'smoke-harness', version: '1.0.0' },
        hostCapabilities: window.HOST_CAPS,
        hostContext: window.HOST_CONTEXT,
      });
      break;
    case 'ui/notifications/initialized':
      send({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: window.TOOL_RESULT });
      break;
    case 'ui/notifications/size-changed':
      frame.style.height = m.params.height + 'px';
      break;
    case 'tools/call': {
      const a = m.params.arguments || {};
      if (a.user_confirmed !== true) {  // the server refuses get_subtitles without user_confirmed=true
        reply(m.id, { isError: true, content: [{ type: 'text', text: 'The user has not chosen yet.' }] });
        break;
      }
      const content = [{ type: 'text', text: SUB_TEXT }];
      if (a.attach) {
        const uri = `subtitles://video/dQw4w9WgXcQ/${a.lang}/${a.auto}/${a.fmt}/${a.layout}`;
        content.push({ type: 'resource', resource: { uri, mimeType: 'text/plain', text: SUB_TEXT } });
        content.push({ type: 'resource_link', uri, name: `Green growth in practice [${a.lang}].${a.fmt}`, mimeType: 'text/plain' });
      }
      setTimeout(() => reply(m.id, { content }), 150);
      break;
    }
    case 'ui/request-display-mode':
      reply(m.id, { mode: m.params.mode });
      break;
    case 'ui/download-file':
    case 'ui/message':
    case 'ui/update-model-context':
    case 'ui/open-link':
      reply(m.id, {});
      break;
    default:
      if (m.id !== undefined && m.method) reply(m.id, {});
  }
});
"""


def js_literal(value) -> str:
    """JSON for embedding inside an inline <script> (no premature </script>)."""
    return json.dumps(value).replace("</", "<\\/")


SUB_TEXT_TAIL = "Second, data systems matter more than slogans."

HOST_CAPS = {"openLinks": {}, "downloadFile": {}, "serverTools": {}, "message": {}, "updateModelContext": {"text": {}}}


def harness_html(menu_html: str, theme: str, locale: str, width: int, result: dict | None = None,
                 caps: dict | None = None, ctx_extra: dict | None = None) -> str:
    # Mimic a host CSP inside the view: no eval, no external scripts, images only from YouTube's CDN.
    csp = (
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src https://i.ytimg.com https://*.ytimg.com data:\">"
    )
    view = menu_html.replace("<head>", "<head>" + csp, 1)
    host_bg = "#262624" if theme == "dark" else "#f5f4ef"
    ctx = {"theme": theme, "locale": locale, "displayMode": "inline", "platform": "web", **(ctx_extra or {})}
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        f"body{{margin:0;padding:16px;background:{host_bg}}}"
        f"iframe{{display:block;width:{width}px;height:120px;border:1px solid #8884;border-radius:12px;overflow:hidden}}"
        "</style></head><body>"
        f"<iframe id='view' sandbox='allow-scripts' title='menu'></iframe>"
        "<script>"
        f"window.HOST_CONTEXT={js_literal(ctx)};window.TOOL_RESULT={js_literal(result or SAMPLE_RESULT)};"
        f"window.HOST_CAPS={js_literal(HOST_CAPS if caps is None else caps)};"
        + HOST_JS
        + f"document.getElementById('view').srcdoc={js_literal(view)};"
        "</script></body></html>"
    )


def chromium_path() -> str:
    if os.environ.get("CHROMIUM_PATH"):
        return os.environ["CHROMIUM_PATH"]
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    if not found:
        sys.exit("Chromium not found under /opt/pw-browsers; set CHROMIUM_PATH")
    return found[-1]


THUMB_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#1f7a4d'/>"
    "<stop offset='1' stop-color='#0b3d2e'/></linearGradient></defs><rect width='480' height='360' fill='url(#g)'/>"
    "<circle cx='360' cy='110' r='60' fill='#f4d35e' opacity='.9'/><path d='M0 290 Q120 200 240 270 T480 250 V360 H0Z' fill='#2fa36b'/></svg>"
)


class Run:
    def __init__(self, page, theme: str, locale: str, width: int, result: dict | None = None, ready: str = "#menu",
                 caps: dict | None = None, ctx_extra: dict | None = None):
        self.page = page
        self.errors: list[str] = []
        page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.route("https://i.ytimg.com/**", lambda r: r.fulfill(status=200, content_type="image/svg+xml", body=THUMB_SVG))
        page.set_viewport_size({"width": width + 32, "height": 900})
        page.set_content(harness_html(MENU.read_text(encoding="utf-8"), theme, locale, width, result, caps, ctx_extra))
        self.frame = page.frame_locator("#view")
        self.frame.locator(ready).wait_for(state="visible", timeout=10000)

    def log(self) -> list[dict]:
        return self.page.evaluate("window.hostLog")

    def methods(self) -> list[str]:
        return [m.get("method") for m in self.log() if m.get("method") != "ui/notifications/size-changed"]

    def wait_for_method(self, method: str, count: int = 1) -> None:
        self.page.wait_for_function(
            "([m, n]) => window.hostLog.filter((x) => x.method === m).length >= n", arg=[method, count], timeout=5000
        )

    def settle(self) -> None:
        self.page.wait_for_timeout(400)  # let size-changed resize the iframe


passed: list[str] = []


def check(cond: bool, name: str) -> None:
    if not cond:
        raise AssertionError(name)
    passed.append(name)


def dark_korean(browser, shots: Path) -> None:
    page = browser.new_page()
    run = Run(page, "dark", "ko-KR", 400)
    f = run.frame
    init = next(m for m in run.log() if m.get("method") == "ui/initialize")
    check(init["params"]["appInfo"] == {"name": "YouTube Subtitles", "version": "1.0.0"}, "ui/initialize sent with appInfo")
    check(f.locator("html").get_attribute("data-theme") == "dark", "dark theme applied from hostContext")

    chips = f.locator("#chips input[type=radio]")
    check(chips.count() == 3, "three track chips")
    check([chips.nth(i).get_attribute("data-lang") for i in range(3)] == ["ko", "en", "en-orig"], "chips in track order")
    check([chips.nth(i).is_checked() for i in range(3)] == [False, True, False], "recommended chip (en) checked")
    check(f.locator("label[for=track-1]").inner_text().replace("\n", " ").split() == ["English", "원본", "en"],
          "chip text: name, kind, code")
    check(f.locator("#tracks-legend").inner_text() == "자막", "Korean legend for ko-KR")
    check(f.locator("#btn-download").inner_text().strip() == "다운로드", "Korean Download label")
    check("선택됨" in f.locator("#selected-line").inner_text(), "Korean Selected label")
    check(f.locator("#layout-group").is_visible(), "layout control visible for TXT")
    run.settle()
    page.locator("#view").screenshot(path=str(shots / "menu-400-dark-ko.png"))

    f.locator("label[for=fmt-srt]").click()
    check(f.locator("#fmt-srt").is_checked(), "SRT selected")
    check(not f.locator("#layout-group").is_visible(), "layout control hidden for SRT")

    f.locator("#btn-download").click()
    run.wait_for_method("ui/download-file")
    seq = [m for m in run.methods() if m in ("tools/call", "ui/download-file", "ui/open-link")]
    check(seq == ["tools/call", "ui/download-file"], "Download: tools/call then ui/download-file, no open-link")
    call = next(m for m in run.log() if m.get("method") == "tools/call")
    args = call["params"]["arguments"]
    check(call["params"]["name"] == "get_subtitles" and args["attach"] is True and args["include_header"] is True,
          "tools/call get_subtitles with attach and include_header")
    check(args["user_confirmed"] is True, "Download sends user_confirmed true")
    check((args["url"], args["lang"], args["auto"], args["fmt"]) == (VIDEO_URL, "en", False, "srt"),
          "tools/call arguments follow the selection")
    dl = next(m for m in run.log() if m.get("method") == "ui/download-file")
    block = dl["params"]["contents"][0]
    check(block["type"] == "resource" and block["resource"]["text"].startswith("Green growth"),
          "ui/download-file carries the embedded resource")
    check(block["resource"]["uri"].endswith(".srt"), "download uri named after the file")
    f.locator("#download-info").wait_for(state="visible")
    url = f.locator("#download-url").inner_text()
    check(url == BASE + "/api/download?url=dQw4w9WgXcQ&lang=en&auto=false&fmt=srt&layout=cues&header=1",
          "download link built from download_template")

    f.locator("#btn-summarize").click()
    run.wait_for_method("ui/message")
    msg = next(m for m in run.log() if m.get("method") == "ui/message")
    text = msg["params"]["content"][0]["text"]
    check(msg["params"]["role"] == "user" and "get_subtitles" in text and "summarize" in text and "language the user has been writing in" in text,
          "Summarize sends ui/message role user")
    check("user_confirmed=true" in text, "Summarize prompt asks for user_confirmed=true")
    f.locator("#notice").wait_for(state="visible")

    check(f.locator("#btn-report").inner_text().strip() == "요약 보고서 (.md)", "Korean report label")
    f.locator("#btn-report").click()
    run.wait_for_method("ui/message", 2)
    msg = [m for m in run.log() if m.get("method") == "ui/message"][-1]
    text = msg["params"]["content"][0]["text"]
    check(msg["params"]["role"] == "user" and "get_subtitles" in text and ".md" in text,
          "Summary report sends ui/message with get_subtitles and .md")
    check("user_confirmed=true" in text and "language the user has been writing in" in text and "not the transcript" in text,
          "Summary report prompt: user_confirmed, user's language, own writing")

    check(f.locator("#btn-addchat").inner_text().strip() == "채팅에 넣기", "Korean Add to chat label")
    calls_before = len([m for m in run.log() if m.get("method") == "tools/call"])
    f.locator("#btn-addchat").click()
    run.wait_for_method("ui/message", 3)
    calls = [m for m in run.log() if m.get("method") == "tools/call"]
    check(len(calls) == calls_before + 1, "Add to chat makes one tools/call")
    a = calls[-1]["params"]["arguments"]
    check(calls[-1]["params"]["name"] == "get_subtitles" and a["user_confirmed"] is True and a["attach"] is False
          and a["max_chars"] == 200000 and (a["fmt"], a["layout"]) == ("txt", "paragraphs"),
          "Add to chat calls get_subtitles as TXT paragraphs with user_confirmed true")
    seq = [m for m in run.methods() if m in ("tools/call", "ui/message")]
    check(seq[-2:] == ["tools/call", "ui/message"], "Add to chat: tools/call then ui/message")
    check("ui/update-model-context" not in run.methods(), "Add to chat does not rely on ui/update-model-context")
    msg = [m for m in run.log() if m.get("method") == "ui/message"][-1]
    text = msg["params"]["content"][0]["text"]
    check(msg["params"]["role"] == "user" and text.startswith(f"Here are the subtitles of '{TITLE}' (en)")
          and "do not call get_subtitles again" in text and text.endswith(SUB_TEXT_TAIL),
          "Add to chat sends the transcript as a user message")
    check("as a file named 'Green_growth_in_practice_lessons_from_ten_years_of_national_plans_subtitle_en.txt'" in text
          and "this chat's files" in text, "Add to chat asks the model to save the transcript as a chat file")
    f.locator("#notice").get_by_text("자막을 입력창에 넣었습니다. 전송을 누르면 채팅에 추가됩니다.").wait_for(state="visible")

    # Prompts always point at a transcript already in the conversation first and fetch only if it is missing.
    f.locator("#btn-report").click()
    run.wait_for_method("ui/message", 4)
    text = [m for m in run.log() if m.get("method") == "ui/message"][-1]["params"]["content"][0]["text"]
    check(text.startswith(f"If the subtitles of '{TITLE}' (en) are already in this conversation")
          and "do not call get_subtitles again" in text and "Otherwise fetch them with get_subtitles" in text
          and ".md" in text, "prompts reuse a transcript already in the chat and fetch otherwise")
    passed.append("Add to chat shows the Korean confirmation")
    check(not run.errors, "no console errors (dark, ko-KR)")
    page.close()


def light_english(browser, shots: Path) -> None:
    page = browser.new_page()
    run = Run(page, "light", "en-US", 400)
    f = run.frame
    check(f.locator("html").get_attribute("data-theme") == "light", "light theme applied from hostContext")
    check(f.locator("#tracks-legend").inner_text() == "Subtitles", "English labels for en-US")
    run.settle()
    page.locator("#view").screenshot(path=str(shots / "menu-400-light-en.png"))

    f.locator("#btn-preview").click()
    f.locator("#preview").wait_for(state="visible")
    call = [m for m in run.log() if m.get("method") == "tools/call"][-1]
    check(call["params"]["arguments"]["attach"] is False and call["params"]["arguments"]["max_chars"] == 20000,
          "Preview calls get_subtitles with attach false, max_chars 20000")
    check(call["params"]["arguments"]["user_confirmed"] is True, "Preview sends user_confirmed true")
    check(f.locator("#preview-text").inner_text().startswith("Green growth"), "Preview shows the text")
    run.settle()
    page.locator("#view").screenshot(path=str(shots / "menu-400-light-en-preview.png"))
    f.locator("#btn-preview").click()
    check(not f.locator("#preview").is_visible(), "Preview toggles closed")
    check(not run.errors, "no console errors (light, en-US)")
    page.close()


def add_to_chat_without_context_capability(browser) -> None:
    page = browser.new_page()
    caps = {k: v for k, v in HOST_CAPS.items() if k != "updateModelContext"}
    run = Run(page, "light", "en-US", 400, caps=caps)
    f = run.frame
    f.locator("#btn-addchat").click()
    run.wait_for_method("ui/message")
    msg = next(m for m in run.log() if m.get("method") == "ui/message")
    text = msg["params"]["content"][0]["text"]
    check(msg["params"]["role"] == "user" and text.startswith(f"Here are the subtitles of '{TITLE}' (en)")
          and text.endswith(SUB_TEXT_TAIL), "Add to chat works without the updateModelContext capability")
    f.locator("#notice").get_by_text("Subtitles placed in the message box.").wait_for(state="visible")
    passed.append("Add to chat shows the English confirmation")
    check(not run.errors, "no console errors (Add to chat without updateModelContext)")
    page.close()


def narrow(browser, shots: Path) -> None:
    page = browser.new_page()
    run = Run(page, "light", "ko-KR", 360)
    run.settle()
    sw, cw = run.frame.locator("html").evaluate("(e) => [e.scrollWidth, e.clientWidth]")
    check(sw <= cw, "no horizontal scroll at 360px")
    boxes = run.frame.locator(".actions .btn:visible").evaluate_all(
        "(bs) => bs.map((b) => { const r = b.getBoundingClientRect(); return [r.left, r.right]; })")
    check(all(left >= 0 and right <= cw for left, right in boxes), "action buttons fit inside 360px")
    page.locator("#view").screenshot(path=str(shots / "menu-360-light-ko.png"))
    check(not run.errors, "no console errors (360px)")
    page.close()


def display_modes(browser, shots: Path) -> None:
    # Without fullscreen in availableDisplayModes (Claude Desktop today) the expand button never shows.
    page = browser.new_page()
    run = Run(page, "light", "en-US", 400)
    check(not run.frame.locator("#btn-expand").is_visible(), "expand button hidden when the host offers no fullscreen")
    page.close()

    page = browser.new_page()
    run = Run(page, "light", "ko-KR", 400, ctx_extra={"availableDisplayModes": ["inline", "fullscreen"]})
    f = run.frame
    btn = f.locator("#btn-expand")
    btn.wait_for(state="visible")
    check(btn.get_attribute("aria-label") == "크게 보기", "expand button shown with a Korean label when fullscreen is offered")
    btn.click()
    run.wait_for_method("ui/request-display-mode")
    req = [m for m in run.log() if m.get("method") == "ui/request-display-mode"][-1]
    check(req["params"] == {"mode": "fullscreen"}, "expand requests the fullscreen display mode")
    f.locator("html[data-display='fullscreen']").wait_for(state="attached")  # the sandboxed frame is opaque to the parent
    check(btn.get_attribute("aria-label") == "채팅 크기로", "button flips to collapse after the host grants fullscreen")
    run.settle()
    page.locator("#view").screenshot(path=str(shots / "menu-400-fullscreen-ko.png"))
    btn.click()
    run.wait_for_method("ui/request-display-mode", 2)
    req = [m for m in run.log() if m.get("method") == "ui/request-display-mode"][-1]
    check(req["params"] == {"mode": "inline"}, "collapse requests the inline display mode")
    check(not run.errors, "no console errors (display modes)")
    page.close()


def tool_error(browser) -> None:
    page = browser.new_page()
    result = {"isError": True, "content": [{"type": "text", "text": "Video unavailable: dQw4w9WgXcQ"}]}
    run = Run(page, "light", "en-US", 400, result=result, ready="#alert")
    check("Video unavailable" in run.frame.locator("#alert-text").inner_text(), "tool error shown as inline alert")
    check(not run.frame.locator("#menu").is_visible(), "menu stays hidden on tool error")
    page.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default=str(HERE.parent / "dist" / "shots"))
    shots = Path(parser.parse_args().shots)
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path())
        try:
            dark_korean(browser, shots)
            light_english(browser, shots)
            add_to_chat_without_context_capability(browser)
            narrow(browser, shots)
            display_modes(browser, shots)
            tool_error(browser)
        finally:
            browser.close()
    for name in passed:
        print(f"ok  {name}")
    print(f"{len(passed)} checks passed; screenshots in {shots}")


if __name__ == "__main__":
    main()
