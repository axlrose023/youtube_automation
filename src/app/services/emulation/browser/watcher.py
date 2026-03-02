import logging
import random

from playwright.async_api import Page

from ..core.state import Mode, SessionState
from .ad_handler import AdHandler
from .humanizer import Humanizer
from .navigator import Navigator
from .playback import PlaybackController

logger = logging.getLogger(__name__)


class VideoWatcher:
    def __init__(
        self,
        page: Page,
        state: SessionState,
        navigator: Navigator,
        humanizer: Humanizer,
        ads: AdHandler,
        playback: PlaybackController,
    ) -> None:
        self._page = page
        self._state = state
        self._nav = navigator
        self._h = humanizer
        self._ads = ads
        self._playback = playback

    # ── public actions ──────────────────────────────────────────────

    async def watch_long(self) -> None:
        if not await self._nav.click_any_video():
            await self._on_no_video("watch_long")
            return

        self._state.no_video_streak = 0
        await self._log_opened_video("watch_long")
        await self._ads.handle(patient=True)
        await self._playback.ensure_playing(self._state.session_id)

        watch_s = await self._decide_duration(mode_a=True, fallback_min=30, fallback_max=600)
        if self._state.fatigue > 0.7:
            before = watch_s
            watch_s *= random.uniform(0.5, 0.8)
            logger.info("Session %s: fatigue %.2f, reduced %.0fs -> %.0fs", self._state.session_id, self._state.fatigue, before, watch_s)
        watch_s = self._cap_before_topic_coverage(watch_s, action="watch_long")
        watch_s = self._cap_to_remaining(watch_s)
        if watch_s <= 0:
            logger.info("Session %s: watch_long — no time left", self._state.session_id)
            return

        logger.info("Session %s: watch_long — watching for %.0fs", self._state.session_id, watch_s)
        await self._watch_for(watch_s, mode_a=True, source_action="watch_long")
        self._state.videos_watched += 1
        self._state.on_video_page = True
        logger.info("Session %s: watch_long done (total watched: %d)", self._state.session_id, self._state.videos_watched)

    async def watch_focused(self) -> None:
        if not await self._nav.click_any_video():
            await self._on_no_video("watch_focused")
            return

        self._state.no_video_streak = 0
        await self._log_opened_video("watch_focused")
        await self._ads.handle(patient=False)
        await self._playback.ensure_playing(self._state.session_id)

        first_eval = self._cap_to_remaining(random.uniform(15, 45))
        if first_eval <= 0:
            return
        logger.info("Session %s: watch_focused — first eval %.0fs", self._state.session_id, first_eval)
        await self._watch_for(first_eval, mode_a=False, source_action="watch_focused_eval")

        if random.random() < 0.25 and self._state.remaining_seconds() > 90:
            logger.info("Session %s: watch_focused — quick exit after eval", self._state.session_id)
            self._state.surf_streak += 1
            self._state.on_video_page = True
            return

        if random.random() < 0.4:
            speed = random.choice([1.25, 1.5, 1.75, 2.0])
            logger.info("Session %s: setting speed to %.2fx", self._state.session_id, speed)
            await self._playback.set_speed(speed)

        if random.random() < 0.3:
            seek = random.choice([10, 15, 30])
            logger.info("Session %s: seeking forward %ds", self._state.session_id, seek)
            await self._playback.seek_forward(seek)

        watch_s = await self._decide_duration(mode_a=False, fallback_min=30, fallback_max=300)
        if self._state.fatigue > 0.5:
            watch_s *= random.uniform(0.5, 0.8)
        watch_s = self._cap_before_topic_coverage(watch_s, action="watch_focused")
        watch_s = self._cap_to_remaining(watch_s)
        if watch_s <= 0:
            return

        logger.info("Session %s: watch_focused — watching for %.0fs", self._state.session_id, watch_s)
        await self._watch_for(watch_s, mode_a=False, source_action="watch_focused")
        self._state.videos_watched += 1
        self._state.on_video_page = True

        await self._playback.set_speed(1.0)
        logger.info("Session %s: watch_focused done (total watched: %d)", self._state.session_id, self._state.videos_watched)

    async def surf_video(self) -> None:
        if not await self._nav.click_any_video():
            await self._on_no_video("surf_video")
            return

        self._state.no_video_streak = 0
        await self._log_opened_video("surf_video")
        await self._ads.handle(patient=False)
        await self._playback.ensure_playing(self._state.session_id)
        watch_s = self._cap_to_remaining(random.uniform(10, 40))
        if watch_s <= 0:
            return
        logger.info("Session %s: surf_video — watching for %.0fs", self._state.session_id, watch_s)
        await self._watch_for(watch_s, mode_a=False, source_action="surf_video")

        self._state.surf_streak += 1
        self._state.on_video_page = True
        if random.random() < 0.7:
            logger.info("Session %s: surf_video — going back", self._state.session_id)
            await self._nav.go_back()

    async def click_recommended(self) -> None:
        if not await self._nav.click_recommended():
            await self._on_no_video("click_recommended")
            return

        self._state.no_video_streak = 0
        await self._log_opened_video("click_recommended")
        is_mode_a = self._state.mode == Mode.A
        await self._ads.handle(patient=is_mode_a)
        await self._playback.ensure_playing(self._state.session_id)

        watch_s = await self._decide_duration(
            mode_a=is_mode_a,
            fallback_min=15 if not is_mode_a else 30,
            fallback_max=180 if not is_mode_a else 400,
        )
        watch_s = self._cap_before_topic_coverage(watch_s, action="click_recommended")
        watch_s = self._cap_to_remaining(watch_s)
        if watch_s <= 0:
            return
        logger.info("Session %s: click_recommended — watching for %.0fs", self._state.session_id, watch_s)
        await self._watch_for(watch_s, mode_a=is_mode_a, source_action="click_recommended")
        self._state.videos_watched += 1
        logger.info("Session %s: click_recommended done (total watched: %d)", self._state.session_id, self._state.videos_watched)

    # ── no-video recovery ───────────────────────────────────────────

    async def _on_no_video(self, action: str) -> None:
        self._state.no_video_streak += 1
        logger.info(
            "Session %s: %s — no video found (streak=%d)",
            self._state.session_id, action, self._state.no_video_streak,
        )

        await self._nav.recover_from_no_video()

        if self._state.on_video_page and self._state.no_video_streak >= 1:
            logger.info(
                "Session %s: %s — forcing home recovery on watch page miss",
                self._state.session_id, action,
            )
            await self._nav.safe_go_home()
            self._state.on_video_page = False
            return

        if self._state.no_video_streak >= 2:
            logger.info(
                "Session %s: %s — forcing home recovery after repeated misses",
                self._state.session_id, action,
            )
            await self._nav.safe_go_home()
            self._state.on_video_page = False

    # ── watching loop ───────────────────────────────────────────────

    async def _log_opened_video(self, action: str) -> None:
        video_title = await self._playback.get_title()
        logger.info(
            "Session %s: %s — opened %s (title=%s)",
            self._state.session_id,
            action,
            self._page.url,
            video_title or "<unknown>",
        )

    async def _watch_for(self, seconds: float, *, mode_a: bool, source_action: str) -> None:
        elapsed = 0.0
        chunks = 0
        micro_scrolls = 0
        micro_wiggles = 0
        micro_pauses = 0
        ad_checks = 0
        ad_skip_attempts = 0
        while elapsed < seconds:
            remaining = self._state.remaining_seconds()
            if remaining <= 1.0:
                logger.info("Session %s: session time up during watch", self._state.session_id)
                break

            chunk = min(
                random.uniform(3.0, 12.0),
                seconds - elapsed,
                max(remaining - 0.5, 0.0),
            )
            if chunk <= 0:
                break
            chunks += 1
            await self._h.delay(chunk, chunk)
            elapsed += chunk

            if await self._ads.check():
                ad_checks += 1
                logger.info(
                    "Session %s: %s micro — ad break detected",
                    self._state.session_id,
                    source_action,
                )
                if mode_a and random.random() < 0.4:
                    await self._h.delay(3, 8)
                ad_skip_attempts += 1
                await self._ads.try_skip()

            roll = random.random()
            if roll < 0.10:
                micro_scrolls += 1
                amount = random.randint(1, 2)
                logger.info(
                    "Session %s: %s micro — scroll_down amount=%d",
                    self._state.session_id,
                    source_action,
                    amount,
                )
                await self._h.scroll("down", amount=amount)
            elif roll < 0.15:
                micro_wiggles += 1
                logger.info(
                    "Session %s: %s micro — wiggle_mouse",
                    self._state.session_id,
                    source_action,
                )
                await self._h.wiggle_mouse()
            elif roll < 0.18 and not mode_a:
                micro_pauses += 1
                logger.info(
                    "Session %s: %s micro — pause_resume (k)",
                    self._state.session_id,
                    source_action,
                )
                await self._page.keyboard.press("k")
                await self._h.delay(2, 6)
                await self._page.keyboard.press("k")

        logger.info(
            "Session %s: %s summary — watched=%.0fs target=%.0fs chunks=%d micro(scroll=%d,wiggle=%d,pause=%d) ads(detected=%d,skip_attempts=%d)",
            self._state.session_id,
            source_action,
            elapsed,
            seconds,
            chunks,
            micro_scrolls,
            micro_wiggles,
            micro_pauses,
            ad_checks,
            ad_skip_attempts,
        )

    # ── duration decision ───────────────────────────────────────────

    async def _decide_duration(
        self, *, mode_a: bool, fallback_min: float, fallback_max: float,
    ) -> float:
        video_dur = await self._playback.get_duration()
        logger.info("Session %s: video duration = %s", self._state.session_id, f"{video_dur:.0f}s" if video_dur else "unknown")

        patience = self._state.personality.patience

        if video_dur and video_dur > 5:
            if self._should_watch_full(video_dur, mode_a=mode_a):
                watch = video_dur * random.uniform(0.85, 1.0) * patience
                logger.info("Session %s: decided full watch %.0fs / %.0fs (patience=%.2f)", self._state.session_id, watch, video_dur, patience)
                return watch

            if video_dur < 180:
                fraction = random.uniform(0.4, 0.8)
            elif video_dur < 600:
                fraction = random.uniform(0.2, 0.6)
            else:
                fraction = random.uniform(0.1, 0.4)

            watch = max(video_dur * fraction, fallback_min) * patience
            logger.info("Session %s: decided partial watch %.0fs / %.0fs (%.0f%%, patience=%.2f)", self._state.session_id, watch, video_dur, fraction * 100, patience)
            return watch

        watch = random.uniform(fallback_min, fallback_max) * patience
        logger.info("Session %s: decided fallback watch %.0fs (patience=%.2f)", self._state.session_id, watch, patience)
        return watch

    def _should_watch_full(self, video_dur: float, *, mode_a: bool) -> bool:
        if video_dur > 1200 and self._state.fatigue > 0.5:
            return False

        if mode_a:
            chance = 0.35 if video_dur < 120 else (0.20 if video_dur < 600 else 0.10)
        else:
            chance = 0.20 if video_dur < 120 else (0.10 if video_dur < 600 else 0.05)

        chance *= max(1.0 - self._state.fatigue * 0.5, 0.3)
        return random.random() < chance

    def _cap_to_remaining(self, seconds: float) -> float:
        return min(seconds, self._state.remaining_seconds())

    def _cap_before_topic_coverage(self, seconds: float, *, action: str) -> float:
        unsearched = self._state.unsearched_topics()
        if not unsearched:
            return seconds

        dynamic_default_caps = {
            "watch_long": random.uniform(140.0, 320.0),
            "watch_focused": random.uniform(90.0, 220.0),
            "click_recommended": random.uniform(60.0, 180.0),
        }
        default_cap = dynamic_default_caps.get(action, random.uniform(60.0, 180.0))

        # Keep some budget for remaining topic hops near deadline while preserving variability.
        pending_topics_after_current = len(unsearched)
        remaining_seconds = self._state.remaining_seconds()
        search_overhead_budget = 25.0
        watch_segments_left = pending_topics_after_current + 1
        budget_for_watch = max(
            remaining_seconds - pending_topics_after_current * search_overhead_budget,
            0.0,
        )
        dynamic_cap = max(35.0, (budget_for_watch / max(watch_segments_left, 1)) * 0.75)
        coverage_cap = min(default_cap, dynamic_cap)
        limited = min(seconds, coverage_cap)
        if limited < seconds:
            logger.info(
                "Session %s: %s capped %.0fs -> %.0fs until all topics covered (remaining_topics=%d, remaining=%.0fs)",
                self._state.session_id,
                action,
                seconds,
                limited,
                pending_topics_after_current,
                remaining_seconds,
            )
        return limited
