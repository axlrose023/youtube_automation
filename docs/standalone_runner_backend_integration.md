# Standalone Runner Backend Integration Plan

## Goal

Replace the backend Android emulation business logic with
`standalone_topic_runner/runner.py`, while preserving the existing backend and
frontend contract:

- `/emulation/*` API routes stay compatible.
- Redis live session state, SSE status updates, history, and capture endpoints
  stay compatible.
- DB persistence continues to use `emulation_sessions`, `ad_captures`, and
  `ad_capture_screenshots`.
- The observable YouTube/emulator behavior inside `runner.py` is not refactored
  or changed during this integration.

## Non-goals For This Pass

- Do not clean up or reorganize `runner.py` internals.
- Do not rewrite frontend.
- Do not delete old desktop/Android modules in the first integration step.
  They can be removed after the new path is verified.
- Do not change ad/banner/watch behavior. Only add callable/session/progress
  plumbing and persistence mapping.

## Existing Pieces To Keep

- API and schemas:
  - `backend/src/app/api/modules/emulation/routes.py`
  - `backend/src/app/api/modules/emulation/service.py`
  - `backend/src/app/api/modules/emulation/schema.py`
- Session storage:
  - `backend/src/app/services/emulation/session/store.py`
- Persistence:
  - `backend/src/app/services/emulation/persistence/*`
  - `backend/src/app/api/modules/emulation/models.py`
- Android infrastructure:
  - `avd_manager.py`
  - `appium_provider.py`
  - `screenrecord.py`
  - `tooling.py`
  - `proxy_bridge.py`
- Task lock / queue behavior in:
  - `backend/src/app/tasks/android_emulation.py`

## New Shape

### 1. Runner Becomes Callable

Add a minimal callable API to `standalone_topic_runner/runner.py`:

```python
@dataclass
class StandaloneRunOptions:
    topics: list[str]
    max_watch_seconds: float
    scroll_rounds: int = 10
    ad_record_seconds: float = 30.0
    avd_name: str | None = None
    manage_appium: bool = True
    headless: bool | None = None
    proxy_url: str | None = None
    run_dir: Path | None = None
    stop_event: asyncio.Event | None = None
    on_progress: Callable[[StandaloneProgressEvent], Awaitable[None]] | None = None

@dataclass
class StandaloneRunResult:
    started_at: str
    finished_at: str
    avd: str
    run_dir: Path
    topics: list[TopicRecord]
```

`main_async(args)` should become a thin wrapper that builds
`StandaloneRunOptions` and calls `run_standalone_session(options)`.

Behavior must stay the same:

- same schedule logic
- same banner/ad/watch loops
- same result JSON structure
- same device shutdown behavior

### 2. Progress Events

Emit best-effort events from the same points where the CLI already persists
`result.json` or prints useful progress:

- `topic_started`
- `banner_captured`
- `ad_captured`
- `video_opened`
- `topic_finished`
- `session_finished`

Events are additive. If `on_progress` fails, runner logs and continues.

### 3. Stop Handling

Use the existing backend stop watcher. The task owns an `asyncio.Event`.

Runner checks `stop_event` at safe loop boundaries:

- before starting next topic
- before opening a new video
- inside watch/ad/banner loops where a wait already exists

If stopped, return partial `StandaloneRunResult`. Task decides whether status is
`stopped` or `completed`.

### 4. Proxy Handling

Reuse the old Android proxy infrastructure:

- For HTTP/HTTPS proxies, pass emulator-reachable URL into
  `AndroidEmulatorLaunchOptions(http_proxy=...)`.
- For SOCKS proxies, start `AndroidHttpProxyBridge` and pass its emulator URL.
- Stop the bridge in `finally`.

No Playwright landing scraper is introduced here because standalone runner reads
landing URLs/screenshots from the emulator itself.

### 5. Artifact Location

Backend task passes:

```text
artifacts/android_sessions/{session_id}/run_YYYYMMDD_HHMMSS
```

as `run_dir`.

This keeps generated files under `config.storage.base_path`, so existing media
serving can resolve screenshots and videos.

### 6. Backend Mapping

Create a small adapter in `backend/src/app/tasks/android_emulation.py` or a new
helper module near it. It converts runner records to existing live payloads.

#### Video Ad Mapping

`AdRecord` -> `EmulationWatchedAd` dict:

- `position`: sequential global position
- `watched_seconds`: `recorded_seconds`
- `completed`: `True` when video file exists or landing exists
- `skip_clicked`: best-effort `False` unless runner later exposes it
- `cta_text`: `cta_label`
- `cta_href`: `landing_url`
- `landing_urls`: `[landing_url]` if present
- `advertiser_domain`: parsed from `landing_url`
- `ad_duration_seconds`: `recorded_seconds`
- `capture.video_file`: ad mp4 path
- `capture.video_status`: `completed` if mp4 exists, else `no_src`
- `capture.landing_url`: landing URL
- `capture.landing_status`: `completed` if URL or screenshot exists
- `capture.screenshot_paths`: landing screenshot if present

#### Banner Mapping

`BannerRecord` -> `EmulationWatchedAd` dict:

- Treat as capture-style ad with no video source.
- `headline_text`: banner title
- `display_url`/`advertiser_domain`: derived from `landing_url` when possible
- `capture.video_status`: `fallback_screenshots`
- `capture.landing_url`: landing URL
- `capture.landing_status`: `completed` if URL or screenshot exists
- `capture.screenshot_paths`:
  - offset `0`: banner screenshot
  - offset `1000`: landing screenshot, if present

#### Watched Video Mapping

`TopicRecord.opened_videos` -> `EmulationWatchedVideo` dict:

- `position`: sequential
- `title`: opened video title
- `watched_seconds`: topic-level watch time divided conservatively across opened
  videos until runner exposes per-video watch seconds
- `target_seconds`: same as watched seconds
- `search_keyword`: topic
- `matched_topics`: `[topic]`

Future small runner addition: add `watch_seconds`/`end_reason` per opened video.

### 7. Android Task Replacement

Keep the existing lock/heartbeat/stop watcher in
`backend/src/app/tasks/android_emulation.py`.

Replace only this part:

```python
runner = AndroidYouTubeSessionRunner(config)
result = await runner.run(...)
```

with:

```python
result = await run_standalone_session(options)
watched_ads, watched_videos = map_standalone_result(result)
```

Persist through existing calls:

- `session_store.update(...)`
- `persistence.persist_ad_captures(...)`
- `persistence.persist_history_completed(...)`
- `persistence.persist_history_failed(...)`

### 8. API Defaults

Keep `runner` in `StartEmulationRequest` for frontend compatibility.

Change service behavior conservatively:

- default new sessions to `android`
- if request sends `desktop`, either map to `android` or reject later after UI is
  updated
- keep `profile_id` accepted but ignored for Android replacement path

Proxy remains required for Android only if current product flow requires it.

### 9. Verification

Static:

- `python -m py_compile standalone_topic_runner/runner.py`
- `python -m py_compile backend/src/app/tasks/android_emulation.py`
- compile mapper module if split out

Fixture checks:

- Load recent `result.json` files and verify mapping produces:
  - watched videos
  - video ads with mp4 + landing URL + landing screenshot
  - banners with screenshot + landing URL + landing screenshot
- Confirm normalized media references resolve under storage.

Runtime smoke:

- 10-20 minute Android session with 2 topics.
- Check Redis/live status via logs or API.
- Check DB history/captures after completion.
- Check media endpoint for at least one mp4 and one screenshot path.

## Cleanup After Verification

Only after the new path is stable:

- Remove old Android YouTube behavior modules.
- Remove desktop emulation task/service if product no longer exposes it.
- Simplify DI browser provider if no other API depends on it.
- Remove obsolete tests or rewrite them against standalone runner fixtures.
