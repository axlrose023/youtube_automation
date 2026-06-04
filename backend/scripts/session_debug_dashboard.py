#!/usr/bin/env python3
"""Generate an HTML debug dashboard for an Android session artifact.

Reads `artifacts/android_sessions/session_*.json` and emits a single self-contained
HTML file next to it with:
  - Per-topic timeline and notes
  - Per-ad panel: on-device screenshots, Playwright landing screenshot, video clip,
    full text fields, CTA info, Gemini analysis, guardrails, dedup trace
  - Session-level stats: ad counts, timings, infra failures

Usage:
    python scripts/session_debug_dashboard.py                 # newest session
    python scripts/session_debug_dashboard.py <session_path>  # specific file
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ARTIFACTS_ROOT = Path(__file__).resolve().parent.parent.parent / "artifacts"
SESSIONS_DIR = ARTIFACTS_ROOT / "android_sessions"


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return str(ts)


def _rel_to_artifacts(path: Any) -> str | None:
    """Paths in session JSON are already relative to artifacts/. Normalize + verify."""
    if not path:
        return None
    if isinstance(path, (list, tuple)):
        for item in path:
            resolved = _rel_to_artifacts(item)
            if resolved:
                return resolved
        return None
    if not isinstance(path, (str, Path)):
        return None
    p = Path(str(path))
    if p.is_absolute():
        try:
            p = p.relative_to(ARTIFACTS_ROOT)
        except ValueError:
            return str(p)
    full = ARTIFACTS_ROOT / p
    return str(p) if full.exists() else None


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _json_block(obj: Any) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(obj)
    return f'<pre class="json">{html.escape(text)}</pre>'


def _kv(label: str, value: Any, *, mono: bool = False, copy: bool = False) -> str:
    val_html = f'<code>{_esc(value)}</code>' if mono else _esc(value) if value is not None else '<span class="muted">—</span>'
    copy_attr = f' data-copy="{_esc(value)}"' if copy and value else ""
    return f'<div class="kv"{copy_attr}><span class="k">{_esc(label)}</span><span class="v">{val_html}</span></div>'


def _img(src: str | None, alt: str) -> str:
    if not src:
        return f'<div class="img-empty">[no {_esc(alt)}]</div>'
    return f'<figure><img src="../{_esc(src)}" alt="{_esc(alt)}" loading="lazy"><figcaption>{_esc(alt)}</figcaption></figure>'


def _video(src: str | None, alt: str) -> str:
    if not src:
        return ""
    return (
        f'<figure><video controls preload="metadata" src="../{_esc(src)}"></video>'
        f'<figcaption>{_esc(alt)}</figcaption></figure>'
    )


def _render_ad(idx: int, ad: dict[str, Any], *, all_ads: list[dict[str, Any]]) -> str:
    cap = ad.get("capture") or {}
    headline = ad.get("headline_text") or cap.get("headline_text")
    sponsor = ad.get("sponsor_label") or cap.get("sponsor_label")
    advertiser = ad.get("advertiser_domain") or cap.get("advertiser_domain")
    display_url = ad.get("display_url_decoded") or ad.get("display_url")
    cta_text = ad.get("cta_text")
    cta_href = ad.get("cta_href") or cap.get("cta_href")
    landing_url = ad.get("landing_url") or cap.get("landing_url")
    landing_status = ad.get("landing_status") or cap.get("landing_status")
    video_file = ad.get("video_file") or cap.get("video_file")
    video_status = ad.get("video_status") or cap.get("video_status")
    scrape_url = cap.get("landing_scrape_url")
    scrape_title = cap.get("landing_scrape_title")
    scrape_shot = cap.get("landing_scrape_screenshot")
    analysis_status = cap.get("analysis_status") or ad.get("analysis_status")
    analysis_summary = cap.get("analysis_summary") or ad.get("analysis_summary")
    analysis_raw = cap.get("analysis_raw_response")
    screenshot_paths = cap.get("screenshot_paths") or []
    visible_lines = ad.get("visible_lines") or []
    full_text = ad.get("full_text") or ad.get("full_visible_text")
    duplicate_of_idx: int | None = None
    for j, other in enumerate(all_ads):
        if j >= idx:
            break
        other_landing = other.get("landing_url") or (other.get("capture") or {}).get("landing_url")
        if landing_url and other_landing and landing_url == other_landing:
            duplicate_of_idx = j + 1
            break

    status_cls = {
        "completed": "ok",
        "relevant": "ok",
        "not_relevant": "warn",
        "failed": "err",
        "skipped": "warn",
    }.get((analysis_status or "").lower(), "muted")

    header_bits = []
    if duplicate_of_idx:
        header_bits.append(f'<span class="badge badge-warn">DUPLICATE of #{duplicate_of_idx}</span>')
    if analysis_status:
        header_bits.append(f'<span class="badge badge-{status_cls}">{_esc(analysis_status)}</span>')

    def _flatten(items: Any) -> list[str]:
        out: list[str] = []
        if isinstance(items, (list, tuple)):
            for it in items:
                out.extend(_flatten(it))
        elif isinstance(items, (str, Path)):
            out.append(str(items))
        return out

    ondev_imgs = ""
    for path in _flatten(screenshot_paths):
        rel = _rel_to_artifacts(path)
        ondev_imgs += _img(rel, Path(path).name if path else "screenshot")
    if not ondev_imgs:
        ondev_imgs = '<div class="img-empty">[no on-device screenshots]</div>'

    scrape_img = _img(_rel_to_artifacts(scrape_shot), "playwright landing screenshot")
    video_name = Path(str(video_file)).name if isinstance(video_file, (str, Path)) else "ad video"
    video_block = _video(_rel_to_artifacts(video_file), video_name)

    # Compact dedup trace
    dedup_hint = ""
    if duplicate_of_idx:
        dedup_hint = (
            '<div class="warn-box">⚠ This ad was NOT dedup\'d in the pipeline. Same landing_url '
            f'appears in Ad #{duplicate_of_idx}. Most likely reason: captured on a fresh topic run, '
            'so the dedup window was reset.</div>'
        )

    return f"""
    <section class="ad" id="ad-{idx}">
      <header>
        <h3>Ad #{idx} &nbsp;<small>({_esc(sponsor or '—')})</small></h3>
        <div class="badges">{''.join(header_bits)}</div>
      </header>

      {dedup_hint}

      <div class="grid2">
        <div class="col">
          <h4>Text fields</h4>
          {_kv("headline", headline)}
          {_kv("sponsor", sponsor)}
          {_kv("advertiser domain", advertiser, mono=True)}
          {_kv("display URL", display_url, mono=True)}
          {_kv("CTA text", cta_text)}
          {_kv("CTA href", cta_href, mono=True, copy=True)}
          {_kv("landing URL", landing_url, mono=True, copy=True)}
          {_kv("landing status", landing_status)}
          {_kv("watched sec", ad.get("watched_seconds"))}
          {_kv("ad duration sec", ad.get("ad_duration_seconds"))}
          {_kv("first ad offset sec", ad.get("first_ad_offset_seconds"))}
          {_kv("end reason", ad.get("end_reason"))}

          <h4>Playwright scrape</h4>
          {_kv("final URL", scrape_url, mono=True, copy=True)}
          {_kv("title", scrape_title)}

          <h4>Analysis</h4>
          {_kv("status", analysis_status)}
          {_json_block(analysis_summary) if analysis_summary else '<div class="muted">no summary</div>'}
        </div>

        <div class="col">
          <h4>On-device screenshots</h4>
          <div class="img-row">{ondev_imgs}</div>

          <h4>Playwright landing</h4>
          <div class="img-row">{scrape_img}</div>

          <h4>Ad video clip <small class="muted">(status: {_esc(video_status or '—')})</small></h4>
          {('<div class="warn-box">⚠ PARTIAL recording — screenrecord truncated well short of the observed ad duration.</div>' if video_status == 'partial' else '')}
          {video_block or ('<div class="muted">video deleted (not_relevant cleanup)</div>' if video_status == 'deleted_not_relevant' else '<div class="muted">no video</div>')}
        </div>
      </div>

      <details>
        <summary>Raw visible lines ({len(visible_lines)}) · full text · Gemini raw</summary>
        <h5>visible_lines</h5>
        {_json_block(visible_lines)}
        <h5>full_text</h5>
        <pre class="json">{_esc(full_text)}</pre>
        <h5>Gemini raw response</h5>
        <pre class="json">{_esc(analysis_raw)}</pre>
        <h5>Full ad record</h5>
        {_json_block(ad)}
      </details>
    </section>
    """


def _render_topic(idx: int, tr: dict[str, Any]) -> str:
    notes = tr.get("notes") or []
    notes_html = "".join(f'<li><code>{_esc(n)}</code></li>' for n in notes)
    badge_cls = "ok" if tr.get("watch_verified") else "warn"
    return f"""
    <section class="topic">
      <header>
        <h3>Topic #{idx}: {_esc(tr.get('topic'))}</h3>
        <span class="badge badge-{badge_cls}">{'verified' if tr.get('watch_verified') else 'not verified'}</span>
      </header>
      <div class="grid3">
        {_kv("opened", tr.get("opened_title"))}
        {_kv("watch sec", tr.get("watch_seconds"))}
        {_kv("ad detected", tr.get("watch_ad_detected"))}
        {_kv("ads in topic", len(tr.get("watched_ads") or []))}
        {_kv("liked", tr.get("liked"))}
        {_kv("subscribed", tr.get("subscribed"))}
      </div>
      <details>
        <summary>Notes ({len(notes)})</summary>
        <ul class="notes">{notes_html}</ul>
      </details>
    </section>
    """


def _render_summary(data: dict[str, Any]) -> str:
    ads = data.get("watched_ads") or []
    topic_results = data.get("topic_results") or []
    analysis_counts: dict[str, int] = {}
    for ad in ads:
        cap = ad.get("capture") or {}
        s = cap.get("analysis_status") or ad.get("analysis_status") or "unknown"
        analysis_counts[s] = analysis_counts.get(s, 0) + 1
    landings = [a.get("landing_url") or (a.get("capture") or {}).get("landing_url") for a in ads]
    dup_count = sum(1 for i, u in enumerate(landings) if u and u in landings[:i])
    return f"""
    <section class="summary">
      <h2>Session summary</h2>
      <div class="grid4">
        {_kv("elapsed sec", data.get("elapsed_seconds"))}
        {_kv("duration target min", data.get("duration_minutes_target"))}
        {_kv("verified topics", data.get("videos_verified"))}
        {_kv("topic runs", len(topic_results))}
        {_kv("ads total", len(ads))}
        {_kv("dup landing urls", dup_count)}
        {_kv("analysis done", data.get("ad_analysis_done"))}
        {_kv("analysis terminal", data.get("ad_analysis_terminal"))}
      </div>
      <h4>Analysis breakdown</h4>
      <div class="grid4">
        {''.join(_kv(k, v) for k, v in analysis_counts.items())}
      </div>
    </section>
    """


CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0e1116; color: #e6edf3; margin: 0; padding: 24px; max-width: 1600px; margin-inline: auto; }
h1, h2, h3, h4, h5 { color: #f0f6fc; margin: 1.2em 0 0.4em; }
h1 { font-size: 28px; border-bottom: 2px solid #30363d; padding-bottom: 8px; }
h2 { font-size: 22px; color: #58a6ff; }
h3 { font-size: 18px; }
h4 { font-size: 15px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 1em; }
h5 { font-size: 13px; color: #7d8590; }
section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px 20px; margin: 16px 0; }
section.ad { border-left: 4px solid #58a6ff; }
section.topic { border-left: 4px solid #d29922; }
section.summary { border-left: 4px solid #3fb950; }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
header h3 { margin: 0; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.badge-ok { background: #1f6f3d; color: #aff5c0; }
.badge-warn { background: #6f4300; color: #ffcc80; }
.badge-err { background: #7d1a1a; color: #ffb0b0; }
.badge-muted { background: #2d333b; color: #8b949e; }
.warn-box { background: #3a2a00; border: 1px solid #6f4300; color: #ffcc80; padding: 10px 14px; border-radius: 6px; margin: 10px 0; font-size: 13px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
@media (max-width: 1100px) { .grid2 { grid-template-columns: 1fr; } .grid3 { grid-template-columns: 1fr 1fr; } .grid4 { grid-template-columns: 1fr 1fr; } }
.col { min-width: 0; }
.kv { display: grid; grid-template-columns: 150px 1fr; gap: 8px; padding: 4px 0; font-size: 13px; border-bottom: 1px solid #21262d; min-width: 0; }
.kv .k { color: #8b949e; font-weight: 500; }
.kv .v { word-break: break-word; overflow-wrap: anywhere; }
.kv[data-copy] { cursor: pointer; }
.kv[data-copy]:hover { background: #1c2128; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; background: #0d1117; padding: 2px 6px; border-radius: 3px; font-size: 12px; color: #d2a8ff; }
.muted { color: #6e7681; }
.json { background: #0d1117; padding: 10px; border-radius: 6px; font-size: 11px; overflow-x: auto; max-height: 320px; overflow-y: auto; font-family: "SF Mono", Menlo, monospace; border: 1px solid #21262d; }
.img-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
figure { margin: 0; display: flex; flex-direction: column; gap: 4px; max-width: 100%; }
figure img, figure video { max-width: 320px; max-height: 600px; border: 1px solid #30363d; border-radius: 4px; background: #000; }
figcaption { font-size: 11px; color: #7d8590; word-break: break-all; }
.img-empty { color: #6e7681; font-style: italic; padding: 20px; background: #0d1117; border-radius: 4px; border: 1px dashed #30363d; }
details { margin-top: 10px; }
summary { cursor: pointer; padding: 6px; background: #0d1117; border-radius: 4px; font-size: 12px; color: #8b949e; user-select: none; }
summary:hover { color: #f0f6fc; }
ul.notes { font-size: 12px; max-height: 300px; overflow-y: auto; background: #0d1117; padding: 10px 10px 10px 26px; border-radius: 4px; }
ul.notes li { margin: 2px 0; }
nav.toc { position: sticky; top: 0; background: #0e1116; padding: 10px 0; border-bottom: 1px solid #30363d; margin-bottom: 20px; z-index: 10; display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; }
nav.toc a { color: #58a6ff; text-decoration: none; padding: 4px 10px; border-radius: 4px; }
nav.toc a:hover { background: #1c2128; }
"""

JS = """
document.querySelectorAll('.kv[data-copy]').forEach(el => {
  el.addEventListener('click', () => {
    const v = el.dataset.copy;
    if (!v) return;
    navigator.clipboard.writeText(v).then(() => {
      const orig = el.style.background;
      el.style.background = '#1f6f3d';
      setTimeout(() => { el.style.background = orig; }, 400);
    });
  });
});
"""


def build_html(data: dict[str, Any], session_path: Path) -> str:
    ads = data.get("watched_ads") or []
    topic_results = data.get("topic_results") or []

    toc = '<nav class="toc">'
    toc += f'<a href="#summary">Summary</a>'
    for i, _ in enumerate(topic_results, 1):
        toc += f'<a href="#topic-{i}">Topic #{i}</a>'
    for i, _ in enumerate(ads, 1):
        toc += f'<a href="#ad-{i}">Ad #{i}</a>'
    toc += "</nav>"

    body_parts = [
        f'<h1>Session debug · {session_path.stem}</h1>',
        _kv("AVD", data.get("avd_name")),
        _kv("serial", data.get("adb_serial")),
        _kv("recorded_at", data.get("recorded_at")),
        toc,
        f'<section class="summary" id="summary">',
        _render_summary(data).split('<section class="summary">')[1],
    ]

    body_parts.extend(
        _render_topic(i, tr)
        for i, tr in enumerate(topic_results, 1)
    )
    body_parts.extend(
        _render_ad(i, ad, all_ads=ads)
        for i, ad in enumerate(ads, 1)
    )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Session debug · {_esc(session_path.stem)}</title>
<style>{CSS}</style>
</head><body>
{''.join(body_parts)}
<script>{JS}</script>
</body></html>
"""


def main() -> int:
    if len(sys.argv) > 1:
        session_path = Path(sys.argv[1]).resolve()
    else:
        candidates = sorted(SESSIONS_DIR.glob("session_*.json"))
        if not candidates:
            print(f"No sessions found in {SESSIONS_DIR}", file=sys.stderr)
            return 1
        session_path = candidates[-1]

    if not session_path.exists():
        print(f"File not found: {session_path}", file=sys.stderr)
        return 1

    data = json.loads(session_path.read_text(encoding="utf-8"))
    html_out = build_html(data, session_path)
    out_path = session_path.with_suffix(".debug.html")
    out_path.write_text(html_out, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
