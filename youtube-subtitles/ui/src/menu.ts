// View for the get_video_info tool (MCP Apps). Bundled by build.mjs into ../app/ui/menu.html.
import { App } from "@modelcontextprotocol/ext-apps";
import type { McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";

// ---------- Contract (structuredContent of get_video_info) ----------
// get_video_info's structuredContent also carries fields for the model (video_id, title, options,
// menu_hint, ...) and a `translated` flag on each track; the view reads only the fields below.

interface Video {
  video_id: string;
  title: string;
  channel: string;
  duration: string;
  duration_seconds: number;
  published: string | null;
  url: string;
  thumbnail: string | null;
  original_language: string | null;
}
interface Track { lang: string; name: string; auto: boolean; kind?: "original" | "auto" }
interface Option { id: string; label: string }
interface MenuData {
  video: Video;
  tracks: Track[];
  recommended: { lang: string; auto: boolean } | null;
  formats: Option[];
  layouts: Option[];
  defaults: { fmt: string; layout: string };
  base_url: string;
  download_template: string;
  labels: Record<string, Record<string, string>>;
}

type Labels = Record<string, string>;
type Block = CallToolResult["content"][number];

// Fallback strings, used before the tool result arrives and for keys the server does not send.
const FALLBACK: Record<"en" | "ko", Labels> = {
  en: {
    subtitles: "Subtitles", format: "Format", layout: "Text layout",
    paragraphs: "Paragraphs", sentences: "Sentences", cues: "Original cues",
    original: "original", auto: "auto", download: "Download", preview: "Preview", summarize: "Summarize",
    translate: "Translate to Korean", keypoints: "Key points", copy: "Copy", copied: "Copied",
    openWeb: "Open web app", selected: "Selected", downloading: "Preparing file...",
    downloadReady: "If the download did not start, open this link:", loading: "Loading...",
    error: "Something went wrong", sent: "Sent to the chat", sending: "Sending...",
    copyHint: "Copy is blocked here. The text is selected: press Ctrl+C (Cmd+C on Mac).",
    noLink: "The server did not provide a download link.", dismiss: "Dismiss", noTracks: "This video has no downloadable subtitles.",
  },
  ko: {
    subtitles: "자막", format: "파일 형식", layout: "텍스트 줄 정돈",
    paragraphs: "문단", sentences: "한 문장씩", cues: "원본 줄",
    original: "원본", auto: "자동", download: "다운로드", preview: "미리 보기", summarize: "요약",
    translate: "한국어로 번역", keypoints: "핵심 정리", copy: "복사", copied: "복사됨",
    openWeb: "웹앱 열기", selected: "선택됨", downloading: "파일 준비 중...",
    downloadReady: "다운로드가 시작되지 않으면 이 링크를 여세요:", loading: "불러오는 중...",
    error: "오류가 발생했습니다", sent: "채팅에 요청을 보냈습니다", sending: "보내는 중...",
    copyHint: "여기서는 복사가 막혀 있습니다. 텍스트가 선택되어 있으니 Ctrl+C (Mac은 Cmd+C)를 누르세요.",
    noLink: "서버가 다운로드 링크를 제공하지 않았습니다.", dismiss: "닫기", noTracks: "이 영상에는 내려받을 수 있는 자막이 없습니다.",
  },
};

// ---------- DOM helpers ----------

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing #${id}`);
  return el as T;
};
function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}
function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  try { return JSON.stringify(err); } catch { return String(err); }
}
function textOf(result: CallToolResult): string {
  return (result.content ?? [])
    .filter((b): b is Extract<Block, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("\n")
    .trim();
}

// ---------- State ----------

const app = new App({ name: "YouTube Subtitles", version: "1.0.0" });
let lang: "en" | "ko" = "en";
let L: Labels = FALLBACK.en;
let data: MenuData | null = null;
let trackIndex = 0;
let fmt = "txt";
let layout = "paragraphs";
let previewKey = "";
let previewText = "";
let noticeTimer = 0;

// ---------- Theme and locale ----------

function hostContext(): McpUiHostContext | undefined {
  try { return app.getHostContext(); } catch { return undefined; }
}

function applyTheme(ctx?: Partial<McpUiHostContext>): void {
  const theme = ctx?.theme ?? hostContext()?.theme;
  const dark = theme ? theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const locale = ctx?.locale ?? hostContext()?.locale;
  if (locale) setLanguage(locale);
}

function setLanguage(locale: string): void {
  const next = locale.toLowerCase().startsWith("ko") ? "ko" : "en";
  const changed = next !== lang;
  lang = next;
  document.documentElement.lang = next;
  L = { ...FALLBACK[next], ...(data?.labels?.[next] ?? {}) };
  $("loading-text").textContent = L.loading;
  $("alert-close").setAttribute("aria-label", L.dismiss);
  if (data && changed) renderMenu();
}

window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
  if (!hostContext()?.theme) applyTheme();
});

// ---------- Alerts and notices ----------

function showError(message: string): void {
  $("alert-text").textContent = message || L.error;
  $("alert").hidden = false;
  $("loading").hidden = true;
}
function clearError(): void { $("alert").hidden = true; }
$("alert-close").addEventListener("click", () => {
  clearError();
  // Never leave the panel blank: bring back the loading line if there is nothing else to show.
  if (!data) $("loading").hidden = false;
});

function showNotice(text: string): void {
  const n = $("notice");
  n.textContent = text;
  n.hidden = false;
  window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => { n.hidden = true; }, 8000);
}

function setBusy(button: HTMLButtonElement, busy: boolean, busyText?: string): void {
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
  const spinner = button.querySelector<HTMLElement>(".spinner");
  const icon = button.querySelector<HTMLElement>(".icon");
  const label = button.querySelector<HTMLElement>(".label");
  if (spinner) spinner.hidden = !busy;
  if (icon) icon.style.display = busy ? "none" : "";
  if (label) {
    const key = label.dataset.label;
    label.textContent = busy && busyText ? busyText : key ? L[key] : label.textContent;
  }
}

// ---------- Rendering ----------

function isMenuData(value: unknown): value is MenuData {
  const v = value as MenuData | null;
  return !!v && typeof v === "object" && !!v.video && Array.isArray(v.tracks);
}

function onToolResult(result: CallToolResult): void {
  if (result.isError) {
    showError(textOf(result) || L.error);
    return;
  }
  const sc = result.structuredContent;
  if (!isMenuData(sc)) {
    showError(textOf(result) || L.error);
    return;
  }
  data = sc;
  fmt = sc.defaults?.fmt || sc.formats?.[0]?.id || "txt";
  layout = sc.defaults?.layout || sc.layouts?.[0]?.id || "paragraphs";
  const rec = sc.recommended;
  const idx = rec ? sc.tracks.findIndex((t) => t.lang === rec.lang && t.auto === rec.auto) : -1;
  trackIndex = idx >= 0 ? idx : 0;
  L = { ...FALLBACK[lang], ...(sc.labels?.[lang] ?? {}) };
  clearError();
  renderMenu();
  $("loading").hidden = true;
  $("menu").hidden = false;
}

function renderMenu(): void {
  if (!data) return;
  const { video } = data;

  // Header
  const img = $<HTMLImageElement>("thumb-img");
  if (video.thumbnail && img.getAttribute("src") !== video.thumbnail) {
    img.hidden = true;
    img.onload = () => { img.hidden = false; };
    img.onerror = () => { img.hidden = true; };
    img.src = video.thumbnail;
  }
  for (const id of ["title-link", "thumb-link"]) $<HTMLAnchorElement>(id).href = video.url;
  $("title-link").textContent = video.title || video.video_id;
  $("video-channel").textContent = video.channel || "";
  $("video-channel").hidden = !video.channel;
  $("duration-text").textContent = video.duration || "";
  $("badge-duration").hidden = !video.duration;
  $("date-text").textContent = video.published || "";
  $("badge-date").hidden = !video.published;

  // Legends and button labels
  $("tracks-legend").textContent = L.subtitles;
  $("format-legend").textContent = L.format;
  $("layout-legend").textContent = L.layout;
  $("selected-label").textContent = `${L.selected}:`;
  document.querySelectorAll<HTMLElement>("[data-label]").forEach((node) => {
    const key = node.dataset.label;
    if (key && !node.closest("button")?.disabled) node.textContent = L[key];
  });
  $("btn-copy").querySelector(".label")!.textContent = L.copy;
  $("download-ready").textContent = L.downloadReady;
  $("btn-web").hidden = !data.base_url;

  renderTracks();
  renderSegments("formats", "fmt", data.formats, fmt, (o) => o.label);
  renderSegments("layouts", "layout", data.layouts, layout, (o) => L[o.id] || o.label);
  syncSelection();
}

function renderTracks(): void {
  if (!data) return;
  const box = $("chips");
  box.replaceChildren();
  box.setAttribute("role", "radiogroup");
  box.setAttribute("aria-labelledby", "tracks-legend");
  if (data.tracks.length === 0) {
    box.append(el("p", { class: "muted" }, L.noTracks));
    return;
  }
  data.tracks.forEach((t, i) => {
    const id = `track-${i}`;
    const wrap = el("div", { class: "chip" });
    const input = el("input", { class: "chip-input sr-only", type: "radio", name: "track", id, value: String(i) });
    input.checked = i === trackIndex;
    input.dataset.lang = t.lang;
    input.dataset.auto = String(t.auto);
    input.addEventListener("change", () => {
      if (input.checked) { trackIndex = i; syncSelection(); }
    });
    const label = el("label", { class: "chip-label", for: id, title: `${t.name} (${t.lang})` });
    label.append(
      el("span", { class: "chip-name" }, t.name),
      el("span", { class: "chip-kind" }, kindLabel(t)),
      el("span", { class: "chip-code" }, t.lang),
    );
    wrap.append(input, label);
    box.append(wrap);
  });
}

function renderSegments(boxId: string, name: string, options: Option[], current: string, text: (o: Option) => string): void {
  const box = $(boxId);
  box.replaceChildren();
  box.setAttribute("role", "radiogroup");
  box.setAttribute("aria-labelledby", `${name === "fmt" ? "format" : "layout"}-legend`);
  for (const o of options ?? []) {
    const id = `${name}-${o.id}`;
    const wrap = el("div", { class: "seg" });
    const input = el("input", { class: "seg-input sr-only", type: "radio", name, id, value: o.id });
    input.checked = o.id === current;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      if (name === "fmt") fmt = o.id; else layout = o.id;
      syncSelection();
    });
    wrap.append(input, el("label", { class: "seg-label", for: id }, text(o)));
    box.append(wrap);
  }
}

function kindLabel(t: Track): string {
  return t.auto || t.kind === "auto" ? L.auto : L.original;
}

function currentTrack(): Track | null {
  return data?.tracks[trackIndex] ?? null;
}

function selectionKey(): string {
  const t = currentTrack();
  return t ? `${t.lang}|${t.auto}|${fmt}|${fmt === "txt" ? layout : "cues"}` : "";
}

function syncSelection(): void {
  const t = currentTrack();
  $("layout-group").hidden = fmt !== "txt";
  $("selected-badge").textContent = t ? `${t.name} · ${kindLabel(t)} · ${t.lang}` : "-";
  const hasTrack = !!t;
  for (const id of ["btn-download", "btn-preview", "btn-summarize", "btn-translate", "btn-keypoints"]) {
    const b = $<HTMLButtonElement>(id);
    if (b.getAttribute("aria-busy") !== "true") b.disabled = !hasTrack;
  }
  // A preview or link for another selection would be misleading: hide it.
  if (previewKey && previewKey !== selectionKey()) hidePreview();
  $("download-info").hidden = true;
}

// ---------- Download ----------

function downloadUrl(): string {
  const t = currentTrack();
  if (!data || !t || !data.base_url || !data.download_template) return "";
  const values: Record<string, string> = {
    video_id: data.video.video_id,
    lang: t.lang,
    auto: t.auto ? "true" : "false",
    fmt,
    layout: fmt === "txt" ? layout : "cues",
  };
  return data.download_template.replace(/\{(\w+)\}/g, (m, key: string) =>
    key in values ? encodeURIComponent(values[key]) : m,
  );
}

function subtitleArgs(extra: Record<string, unknown>): Record<string, unknown> {
  const t = currentTrack()!;
  return {
    url: data!.video.url,
    lang: t.lang,
    auto: t.auto,
    fmt,
    layout: fmt === "txt" ? layout : "cues",
    include_header: true,
    user_confirmed: true, // the user picked this action in the menu; the server refuses get_subtitles without it
    ...extra,
  };
}

// Host-side download: fetch the file through get_subtitles and hand its embedded resource to the host.
async function downloadViaHost(): Promise<void> {
  const result = await app.callServerTool({ name: "get_subtitles", arguments: subtitleArgs({ attach: true }) });
  if (result.isError) throw new Error(textOf(result) || L.error);
  const block = result.content.find((b): b is Extract<Block, { type: "resource" }> => b.type === "resource");
  if (!block) throw new Error("get_subtitles returned no file.");
  // Hosts name the saved file after the URI; use the file name from the resource link when there is one.
  const link = result.content.find((b): b is Extract<Block, { type: "resource_link" }> => b.type === "resource_link");
  const named = link?.name
    ? { ...block, resource: { ...block.resource, uri: `file:///${encodeURIComponent(link.name)}` } }
    : block;
  const res = await app.downloadFile({ contents: [named] });
  if (res?.isError) throw new Error("The host did not save the file.");
}

async function onDownload(): Promise<void> {
  if (!currentTrack()) return;
  const button = $<HTMLButtonElement>("btn-download");
  const url = downloadUrl();
  clearError();
  setBusy(button, true, L.downloading);
  try {
    let done = false;
    if (app.getHostCapabilities()?.downloadFile) {
      try {
        await downloadViaHost();
        done = true;
      } catch (err) {
        console.warn("[subtitle menu] host download failed, falling back to a link:", errorMessage(err));
      }
    }
    if (!done) {
      if (!url) throw new Error(L.noLink);
      await app.openLink({ url });
    }
  } catch (err) {
    showError(`${L.error}: ${errorMessage(err)}`);
  } finally {
    setBusy(button, false);
    if (url) {
      const a = $<HTMLAnchorElement>("download-url");
      a.href = url;
      a.textContent = url;
      $("download-info").hidden = false;
    }
  }
}

// ---------- Preview ----------

function hidePreview(): void {
  $("preview").hidden = true;
  $("btn-preview").setAttribute("aria-expanded", "false");
  $("copy-hint").hidden = true;
}

async function onPreview(): Promise<void> {
  if (!currentTrack()) return;
  const button = $<HTMLButtonElement>("btn-preview");
  if (!$("preview").hidden) { hidePreview(); return; }
  const key = selectionKey();
  clearError();
  if (key !== previewKey || !previewText) {
    setBusy(button, true, L.loading);
    try {
      const result = await app.callServerTool({
        name: "get_subtitles",
        arguments: subtitleArgs({ attach: false, max_chars: 20000 }),
      });
      if (result.isError) throw new Error(textOf(result) || L.error);
      previewText = textOf(result);
      previewKey = key;
    } catch (err) {
      showError(`${L.error}: ${errorMessage(err)}`);
      return;
    } finally {
      setBusy(button, false);
    }
  }
  const t = currentTrack()!;
  $("preview-title").textContent = `${t.name} · ${fmt.toUpperCase()}`;
  $("preview-text").textContent = previewText;
  $("copy-hint").hidden = true;
  $("preview").hidden = false;
  button.setAttribute("aria-expanded", "true");
}

async function onCopy(): Promise<void> {
  const button = $<HTMLButtonElement>("btn-copy");
  const label = button.querySelector(".label")!;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(previewText);
    label.textContent = L.copied;
    window.setTimeout(() => { label.textContent = L.copy; }, 1500);
  } catch {
    const pre = $("preview-text");
    const range = document.createRange();
    range.selectNodeContents(pre);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    pre.focus();
    const hint = $("copy-hint");
    hint.textContent = L.copyHint;
    hint.hidden = false;
  }
}

// ---------- Chat prompts ----------

function answerLanguage(): string {
  const locale = hostContext()?.locale;
  if (!locale) return "the user's language";
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(locale.split("-")[0]);
    if (name) return name;
  } catch { /* fall through */ }
  return "the user's language";
}

function promptText(kind: string): string {
  const t = currentTrack()!;
  const v = data!.video;
  const task: Record<string, string> = {
    summarize: "summarize them",
    translate: "translate them to Korean, in sections if long",
    keypoints: "list the key points with timestamps if available",
  };
  const answer = kind === "translate" ? "" : ` Answer in ${answerLanguage()}.`;
  return (
    `Using get_subtitles with url=${v.url}, lang=${t.lang}, auto=${t.auto}, fmt=txt, layout=paragraphs, ` +
    `user_confirmed=true, ` +
    `fetch the subtitles of '${v.title}' and ${task[kind]}.${answer}`
  );
}

async function onPrompt(button: HTMLButtonElement): Promise<void> {
  const kind = button.dataset.prompt;
  if (!kind || !currentTrack()) return;
  clearError();
  setBusy(button, true, L.sending);
  try {
    const res = await app.sendMessage({ role: "user", content: [{ type: "text", text: promptText(kind) }] });
    if (res?.isError) throw new Error("The host did not accept the message.");
    showNotice(`${L.sent}: ${L[kind]}`);
  } catch (err) {
    showError(`${L.error}: ${errorMessage(err)}`);
  } finally {
    setBusy(button, false);
  }
}

// ---------- Links ----------

async function open(url: string): Promise<void> {
  if (!url) return;
  try {
    const res = await app.openLink({ url });
    if (res?.isError) throw new Error("The host did not open the link.");
  } catch (err) {
    showError(`${L.error}: ${errorMessage(err)}`);
  }
}

function wire(): void {
  $("btn-download").addEventListener("click", () => void onDownload());
  $("btn-preview").addEventListener("click", () => void onPreview());
  $("btn-copy").addEventListener("click", () => void onCopy());
  for (const id of ["btn-summarize", "btn-translate", "btn-keypoints"]) {
    const b = $<HTMLButtonElement>(id);
    b.addEventListener("click", () => void onPrompt(b));
  }
  $("btn-web").addEventListener("click", () => void open(data?.base_url ?? ""));
  for (const id of ["title-link", "thumb-link", "download-url"]) {
    $<HTMLAnchorElement>(id).addEventListener("click", (e) => {
      e.preventDefault();
      void open((e.currentTarget as HTMLAnchorElement).href);
    });
  }
}

// ---------- Start ----------

async function main(): Promise<void> {
  wire();
  setLanguage(navigator.language || "en");
  applyTheme();
  app.ontoolresult = (result) => onToolResult(result as CallToolResult);
  app.onhostcontextchanged = (ctx) => applyTheme({ ...hostContext(), ...ctx });
  try {
    await app.connect();
  } catch (err) {
    showError(`${L.error}: ${errorMessage(err)}`);
    return;
  }
  applyTheme(app.getHostContext());
}

void main();
