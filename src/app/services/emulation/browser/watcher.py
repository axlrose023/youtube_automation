from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from playwright.async_api import Page

from ..core.config import (
    AD_COMPLETION_OVERFLOW_MAX_S,
    CLICK_RECOMMENDED_FALLBACK_A,
    CLICK_RECOMMENDED_FALLBACK_B,
    COMMENT_REFOLLOW_BASE_PROBABILITY,
    COMMENT_REFOLLOW_DEPTH_TRIGGER,
    COMMENT_REFOLLOW_MAX_PROBABILITY,
    COMMENT_REFOLLOW_STEP_PROBABILITY,
    CONTINUE_CURRENT_VIDEO_FALLBACK,
    CONTINUE_CURRENT_VIDEO_MIN_REMAINING_S,
    CONTINUE_CURRENT_VIDEO_PROBABILITY,
    COVERAGE_CAP_CLICK_RECOMMENDED,
    COVERAGE_CAP_DEFAULT,
    COVERAGE_CAP_WATCH_FOCUSED,
    COVERAGE_CAP_WATCH_LONG,
    FIRST_EVAL_RANGE,
    MICRO_PAUSE_PROBABILITY,
    MICRO_SCROLL_PROBABILITY,
    MICRO_WIGGLE_PROBABILITY,
    QUICK_EXIT_PROBABILITY,
    SEEK_CHOICES,
    SEEK_FORWARD_PROBABILITY,
    SPEED_CHANGE_PROBABILITY,
    SPEED_CHOICES,
    SURF_GO_BACK_PROBABILITY,
    SURF_VIDEO_RANGE,
    WATCH_CHUNK_RANGE,
    WATCH_FOCUSED_FALLBACK,
    WATCH_LONG_FALLBACK,
)
from ..core.session.state import Mode, SessionState
from .ads.handler import AdHandler
from .humanizer import Humanizer
from .navigator import Navigator
from .playback import PlaybackController
from .watch_duration import WatchDurationCalculator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchProfile:
    action: str
    mode_a: bool = True
    patient_ads: bool = True
    fallback_min: float = 30
    fallback_max: float = 600
    fatigue_reduction: float = 0.0
    fatigue_threshold: float = 0.0
    cap_range: tuple[float, float] = COVERAGE_CAP_DEFAULT
    mark_completed: bool = True
    increment_surf: bool = False
    go_back_probability: float = 0.0


PROFILE_WATCH_LONG = WatchProfile(
    action="watch_long",
    mode_a=True,
    patient_ads=True,
    fallback_min=WATCH_LONG_FALLBACK[0],
    fallback_max=WATCH_LONG_FALLBACK[1],
    fatigue_reduction=0.8,
    fatigue_threshold=0.7,
    cap_range=COVERAGE_CAP_WATCH_LONG,
)

PROFILE_WATCH_FOCUSED = WatchProfile(
    action="watch_focused",
    mode_a=False,
    patient_ads=False,
    fallback_min=WATCH_FOCUSED_FALLBACK[0],
    fallback_max=WATCH_FOCUSED_FALLBACK[1],
    fatigue_reduction=0.8,
    fatigue_threshold=0.5,
    cap_range=COVERAGE_CAP_WATCH_FOCUSED,
)

PROFILE_SURF_VIDEO = WatchProfile(
    action="surf_video",
    mode_a=False,
    patient_ads=False,
    fallback_min=SURF_VIDEO_RANGE[0],
    fallback_max=SURF_VIDEO_RANGE[1],
    mark_completed=False,
    increment_surf=True,
    go_back_probability=SURF_GO_BACK_PROBABILITY,
)


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
        self._duration = WatchDurationCalculator(state, playback)

    # ── Public actions ────────────────────────────────────────

    async def watch_long(self) -> None:
        if await self._maybe_continue_current_video(PROFILE_WATCH_LONG):
            return
        await self._execute_watch(PROFILE_WATCH_LONG)

    async def watch_focused(self) -> None:
        if await self._maybe_continue_current_video(PROFILE_WATCH_FOCUSED):
            return
        await self._execute_watch_focused()

    async def surf_video(self) -> None:
        await self._execute_watch(PROFILE_SURF_VIDEO)

    async def click_recommended(self) -> None:
        if not await self._nav.click_recommended():
            await self._on_no_video("click_recommended")
            return

        is_mode_a = self._state.mode == Mode.A
        fallback = CLICK_RECOMMENDED_FALLBACK_A if is_mode_a else CLICK_RECOMMENDED_FALLBACK_B
        profile = WatchProfile(
            action="click_recommended",
            mode_a=is_mode_a,
            patient_ads=is_mode_a,
            fallback_min=fallback[0],
            fallback_max=fallback[1],
            cap_range=COVERAGE_CAP_CLICK_RECOMMENDED,
        )
        await self._do_watch_after_click(profile)

    # ── Core watch orchestration ──────────────────────────────

    async def _execute_watch(self, profile: WatchProfile) -> None:
        if not await self._nav.click_any_video():
            await self._on_no_video(profile.action)
            return
        await self._do_watch_after_click(profile)

    async def _do_watch_after_click(self, profile: WatchProfile) -> None:
        self._state.no_video_streak = 0
        video_title, video_url = await self._log_opened_video(profile.action)
        self._state.start_current_watch(
            action=profile.action,
            title=video_title,
            url=video_url,
        )
        await self._ads.handle(patient=profile.patient_ads)
        await self._playback.ensure_playing(self._state.session_id)

        watch_s = await self._calculate_watch_time(profile)
        self._state.update_current_watch(target_seconds=watch_s)
        if watch_s <= 0:
            logger.info("Session %s: %s — no time left", self._state.session_id, profile.action)
            self._state.clear_current_watch()
            return

        logger.info("Session %s: %s — watching for %.0fs", self._state.session_id, profile.action, watch_s)
        watched_seconds = await self._watch_for(watch_s, mode_a=profile.mode_a, source_action=profile.action)
        self._record_and_finalize(profile, video_title, video_url, watched_seconds, watch_s)

        if profile.go_back_probability > 0 and random.random() < profile.go_back_probability:
            logger.info("Session %s: %s — going back", self._state.session_id, profile.action)
            await self._nav.go_back()

    async def _maybe_continue_current_video(self, profile: WatchProfile) -> bool:
        if not self._state.on_video_page:
            return False
        if self._state.remaining_seconds() < CONTINUE_CURRENT_VIDEO_MIN_REMAINING_S:
            return False
        if random.random() >= CONTINUE_CURRENT_VIDEO_PROBABILITY:
            return False

        opened_url = self._page.url or ""
        if not self._state.video_id_from_url(opened_url):
            return False

        logger.info(
            "Session %s: %s — continue current video instead of opening a new one",
            self._state.session_id, profile.action,
        )

        self._state.no_video_streak = 0
        video_title = await self._playback.get_title()
        self._state.start_current_watch(
            action=profile.action,
            title=video_title,
            url=opened_url,
        )
        await self._ads.handle(patient=profile.patient_ads)
        await self._playback.ensure_playing(self._state.session_id)

        watch_s = await self._calculate_watch_time(
            profile,
            fallback_override=(CONTINUE_CURRENT_VIDEO_FALLBACK[0], CONTINUE_CURRENT_VIDEO_FALLBACK[1]),
        )
        self._state.update_current_watch(target_seconds=watch_s)
        if watch_s <= 0:
            self._state.clear_current_watch()
            return True

        watched_seconds = await self._watch_for(
            watch_s, mode_a=profile.mode_a, source_action=f"{profile.action}_continue",
        )
        self._state.add_watched_video(
            action=profile.action,
            title=video_title,
            url=opened_url,
            watched_seconds=watched_seconds,
            target_seconds=watch_s,
            completed=profile.mark_completed,
            merge_if_same_url=True,
        )
        self._state.clear_current_watch()
        self._state.on_video_page = True
        return True

    async def _execute_watch_focused(self) -> None:
        profile = PROFILE_WATCH_FOCUSED

        if not await self._nav.click_any_video():
            await self._on_no_video(profile.action)
            return

        self._state.no_video_streak = 0
        video_title, video_url = await self._log_opened_video(profile.action)
        self._state.start_current_watch(
            action=profile.action,
            title=video_title,
            url=video_url,
        )
        await self._ads.handle(patient=False)
        await self._playback.ensure_playing(self._state.session_id)

        first_eval = self._duration.cap_to_remaining(random.uniform(*FIRST_EVAL_RANGE))
        self._state.update_current_watch(target_seconds=first_eval)
        if first_eval <= 0:
            self._state.clear_current_watch()
            return
        logger.info("Session %s: %s — first eval %.0fs", self._state.session_id, profile.action, first_eval)
        first_eval_watched = await self._watch_for(first_eval, mode_a=False, source_action="watch_focused_eval")

        if random.random() < QUICK_EXIT_PROBABILITY and self._state.remaining_seconds() > 90:
            logger.info("Session %s: %s — quick exit after eval", self._state.session_id, profile.action)
            self._state.add_watched_video(
                action=profile.action, title=video_title, url=video_url,
                watched_seconds=first_eval_watched, target_seconds=first_eval, completed=False,
            )
            self._state.clear_current_watch()
            self._state.surf_streak += 1
            self._state.on_video_page = True
            return

        if random.random() < SPEED_CHANGE_PROBABILITY:
            speed = random.choice(SPEED_CHOICES)
            logger.info("Session %s: setting speed to %.2fx", self._state.session_id, speed)
            await self._playback.set_speed(speed)

        if random.random() < SEEK_FORWARD_PROBABILITY:
            seek = random.choice(SEEK_CHOICES)
            logger.info("Session %s: seeking forward %ds", self._state.session_id, seek)
            await self._playback.seek_forward(seek)

        watch_s = await self._duration.decide(
            mode_a=profile.mode_a, fallback_min=profile.fallback_min, fallback_max=profile.fallback_max,
        )
        if self._state.fatigue > profile.fatigue_threshold:
            watch_s *= random.uniform(0.5, profile.fatigue_reduction if profile.fatigue_reduction > 0 else 0.8)
        watch_s = self._duration.cap_before_topic_coverage(watch_s, profile.cap_range, profile.action)
        watch_s = self._duration.apply_realism_floor(
            watch_s, profile.action,
            mark_completed=profile.mark_completed,
            after_coverage=self._state.all_topics_covered(),
        )
        watch_s = self._duration.cap_to_remaining(watch_s)
        self._state.update_current_watch(target_seconds=first_eval + watch_s)
        if watch_s <= 0:
            self._state.clear_current_watch()
            return

        logger.info("Session %s: %s — watching for %.0fs", self._state.session_id, profile.action, watch_s)
        watched_main = await self._watch_for(watch_s, mode_a=False, source_action=profile.action)
        self._state.add_watched_video(
            action=profile.action, title=video_title, url=video_url,
            watched_seconds=first_eval_watched + watched_main,
            target_seconds=first_eval + watch_s, completed=True,
        )
        self._state.clear_current_watch()
        self._state.videos_watched += 1
        self._state.on_video_page = True
        await self._playback.set_speed(1.0)
        logger.info("Session %s: %s done (total watched: %d)", self._state.session_id, profile.action, self._state.videos_watched)

    # ── Shared helpers ────────────────────────────────────────

    async def _calculate_watch_time(
        self,
        profile: WatchProfile,
        fallback_override: tuple[float, float] | None = None,
    ) -> float:
        fb_min = fallback_override[0] if fallback_override else profile.fallback_min
        fb_max = fallback_override[1] if fallback_override else profile.fallback_max

        watch_s = await self._duration.decide(mode_a=profile.mode_a, fallback_min=fb_min, fallback_max=fb_max)
        watch_s = self._duration.apply_fatigue_reduction(watch_s, profile.fatigue_threshold, profile.fatigue_reduction)
        watch_s = self._duration.cap_before_topic_coverage(watch_s, profile.cap_range, profile.action)
        watch_s = self._duration.apply_realism_floor(
            watch_s, profile.action,
            mark_completed=profile.mark_completed,
            after_coverage=self._state.all_topics_covered(),
        )
        return self._duration.cap_to_remaining(watch_s)

    def _record_and_finalize(
        self,
        profile: WatchProfile,
        video_title: str | None,
        video_url: str,
        watched_seconds: float,
        target_seconds: float,
    ) -> None:
        self._state.add_watched_video(
            action=profile.action, title=video_title, url=video_url,
            watched_seconds=watched_seconds, target_seconds=target_seconds,
            completed=profile.mark_completed,
        )
        self._state.clear_current_watch()
        if profile.increment_surf:
            self._state.surf_streak += 1
        if profile.mark_completed:
            self._state.videos_watched += 1
        self._state.on_video_page = True

        logger.info(
            "Session %s: %s done (total watched: %d)",
            self._state.session_id, profile.action, self._state.videos_watched,
        )

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

    async def _log_opened_video(self, action: str) -> tuple[str | None, str]:
        opened_url = self._page.url or self._state.last_clicked_video_url or ""
        dom_title = await self._playback.get_title()
        clicked_title = self._state.last_clicked_video_title

        video_title = dom_title
        if clicked_title and self._same_video(opened_url, self._state.last_clicked_video_url or ""):
            video_title = clicked_title
        if not video_title or video_title.strip().lower() == "youtube":
            video_title = clicked_title

        logger.info(
            "Session %s: %s — opened %s (title=%s)",
            self._state.session_id, action, opened_url, video_title or "<unknown>",
        )
        return video_title, opened_url

    def _same_video(self, left_url: str, right_url: str) -> bool:
        left_id = self._state.video_id_from_url(left_url)
        right_id = self._state.video_id_from_url(right_url)
        return bool(left_id and right_id and left_id == right_id)

    # ── Micro-interaction watch loop ──────────────────────────

    async def _watch_for(self, seconds: float, *, mode_a: bool, source_action: str) -> float:
        elapsed = 0.0
        chunks = 0
        ad_overflow_started_at: float | None = None
        comment_depth = 0
        comment_dwell_seconds = 0.0
        comment_refocus_target = random.uniform(4.0, 10.0)
        micro_scrolls = 0
        micro_wiggles = 0
        micro_pauses = 0
        ad_checks = 0
        ad_skip_attempts = 0
        while elapsed < seconds:
            remaining = self._state.remaining_seconds()
            if remaining <= 1.0:
                if await self._allow_terminal_ad_overflow(
                    mode_a=mode_a,
                    source_action=source_action,
                    elapsed=elapsed,
                    ad_checks=ad_checks,
                    ad_skip_attempts=ad_skip_attempts,
                    ad_overflow_started_at=ad_overflow_started_at,
                ):
                    ad_overflow_started_at = ad_overflow_started_at or time.monotonic()
                    if await self._ads.check():
                        ad_checks += 1
                    captured_ads = await self._ads.handle(patient=mode_a)
                    ad_skip_attempts += sum(
                        1 for ad in captured_ads if isinstance(ad, dict) and ad.get("skip_clicked")
                    )
                logger.info("Session %s: session time up during watch", self._state.session_id)
                break

            chunk = min(
                random.uniform(*WATCH_CHUNK_RANGE),
                seconds - elapsed,
                max(remaining - 0.5, 0.0),
            )
            if chunk <= 0:
                break
            chunks += 1
            await self._h.delay(chunk, chunk)
            elapsed += chunk
            self._state.increment_current_watch(chunk)
            if comment_depth > 0:
                comment_dwell_seconds += chunk

            remaining_after_delay = self._state.remaining_seconds()
            if remaining_after_delay <= 1.0:
                if await self._allow_terminal_ad_overflow(
                    mode_a=mode_a,
                    source_action=source_action,
                    elapsed=elapsed,
                    ad_checks=ad_checks,
                    ad_skip_attempts=ad_skip_attempts,
                    ad_overflow_started_at=ad_overflow_started_at,
                ):
                    ad_overflow_started_at = ad_overflow_started_at or time.monotonic()
                    if await self._ads.check():
                        ad_checks += 1
                    captured_ads = await self._ads.handle(patient=mode_a)
                    ad_skip_attempts += sum(
                        1 for ad in captured_ads if isinstance(ad, dict) and ad.get("skip_clicked")
                    )
                logger.info(
                    "Session %s: session time up after watch delay (source=%s, watched=%.1fs)",
                    self._state.session_id,
                    source_action,
                    elapsed,
                )
                break

            if await self._ads.check():
                ad_checks += 1
                logger.info(
                    "Session %s: %s micro — ad break detected",
                    self._state.session_id, source_action,
                )
                if mode_a and random.random() < 0.4:
                    await self._h.delay(3, 8)
                captured_ads = await self._ads.handle(patient=mode_a)
                ad_skip_attempts += sum(
                    1 for ad in captured_ads if isinstance(ad, dict) and ad.get("skip_clicked")
                )
                remaining_after_ads = self._state.remaining_seconds()
                if remaining_after_ads <= 1.0:
                    logger.info(
                        "Session %s: session time up after ad handling (source=%s, watched=%.1fs)",
                        self._state.session_id,
                        source_action,
                        elapsed,
                    )
                    break

            roll = random.random()
            if roll < MICRO_SCROLL_PROBABILITY:
                micro_scrolls += 1
                if comment_depth == 0:
                    comment_refocus_target = random.uniform(4.0, 10.0)
                amount = random.randint(1, 2)
                comment_depth += amount
                logger.info(
                    "Session %s: %s micro — scroll_down amount=%d (depth=%d dwell=%.1fs target=%.1fs)",
                    self._state.session_id, source_action,
                    amount, comment_depth, comment_dwell_seconds, comment_refocus_target,
                )
                await self._h.scroll("down", amount=amount)
                if self._should_refocus_after_comment_scroll(
                    comment_depth=comment_depth,
                    comment_dwell_seconds=comment_dwell_seconds,
                    comment_refocus_target=comment_refocus_target,
                ):
                    await self._refocus_after_comment_glance(
                        source_action=source_action,
                        comment_depth=comment_depth,
                        comment_dwell_seconds=comment_dwell_seconds,
                    )
                    comment_depth = 0
                    comment_dwell_seconds = 0.0
            elif roll < MICRO_SCROLL_PROBABILITY + MICRO_WIGGLE_PROBABILITY:
                micro_wiggles += 1
                logger.info("Session %s: %s micro — wiggle_mouse", self._state.session_id, source_action)
                await self._h.wiggle_mouse()
            elif roll < MICRO_SCROLL_PROBABILITY + MICRO_WIGGLE_PROBABILITY + MICRO_PAUSE_PROBABILITY and not mode_a:
                micro_pauses += 1
                logger.info("Session %s: %s micro — pause_resume (k)", self._state.session_id, source_action)
                await self._page.keyboard.press("k")
                await self._h.delay(2, 6)
                await self._page.keyboard.press("k")

            if (
                comment_depth > 0
                and comment_dwell_seconds >= comment_refocus_target * 1.8
            ):
                await self._refocus_after_comment_glance(
                    source_action=source_action,
                    comment_depth=comment_depth,
                    comment_dwell_seconds=comment_dwell_seconds,
                )
                comment_depth = 0
                comment_dwell_seconds = 0.0

        logger.info(
            "Session %s: %s summary — watched=%.0fs target=%.0fs chunks=%d micro(scroll=%d,wiggle=%d,pause=%d) ads(detected=%d,skip_attempts=%d)",
            self._state.session_id, source_action,
            elapsed, seconds, chunks,
            micro_scrolls, micro_wiggles, micro_pauses,
            ad_checks, ad_skip_attempts,
        )
        return elapsed

    async def _allow_terminal_ad_overflow(
        self,
        *,
        mode_a: bool,
        source_action: str,
        elapsed: float,
        ad_checks: int,
        ad_skip_attempts: int,
        ad_overflow_started_at: float | None,
    ) -> bool:
        if not await self._ads.check():
            return False
        if ad_overflow_started_at is not None:
            overflow_elapsed = max(time.monotonic() - ad_overflow_started_at, 0.0)
            if overflow_elapsed >= AD_COMPLETION_OVERFLOW_MAX_S:
                logger.info(
                    "Session %s: %s terminal ad overflow cap hit after %.1fs",
                    self._state.session_id,
                    source_action,
                    overflow_elapsed,
                )
                return False
        logger.info(
            "Session %s: %s allowing terminal ad overflow (watched=%.1fs, ads_detected=%d, ad_skip_attempts=%d, patient=%s)",
            self._state.session_id,
            source_action,
            elapsed,
            ad_checks,
            ad_skip_attempts,
            mode_a,
        )
        return True

    # ── Comment / scroll helpers ──────────────────────────────

    def _should_refocus_after_comment_scroll(
        self,
        *,
        comment_depth: int,
        comment_dwell_seconds: float,
        comment_refocus_target: float,
    ) -> bool:
        if comment_depth >= COMMENT_REFOLLOW_DEPTH_TRIGGER + 2:
            return True
        if comment_depth < 2:
            return False
        if comment_dwell_seconds < min(comment_refocus_target, 4.0):
            return False

        depth_bonus = max(comment_depth - 2, 0) * COMMENT_REFOLLOW_STEP_PROBABILITY
        dwell_ratio = min(comment_dwell_seconds / max(comment_refocus_target, 1.0), 1.5)
        dwell_bonus = dwell_ratio * 0.2
        probability = min(
            COMMENT_REFOLLOW_BASE_PROBABILITY * 0.55 + depth_bonus + dwell_bonus,
            COMMENT_REFOLLOW_MAX_PROBABILITY,
        )
        return random.random() < probability

    async def _refocus_after_comment_glance(
        self,
        *,
        source_action: str,
        comment_depth: int,
        comment_dwell_seconds: float,
    ) -> None:
        read_time = min(
            random.uniform(2.4, 4.5)
            + min(comment_depth * 0.45, 2.0)
            + min(comment_dwell_seconds * 0.12, 1.5),
            8.0,
        )
        logger.info(
            "Session %s: %s micro — reading comments %.1fs (depth=%d dwell=%.1fs)",
            self._state.session_id, source_action,
            read_time, comment_depth, comment_dwell_seconds,
        )
        await self._h.delay(read_time, read_time)

        additional_scrolls = 1 if comment_depth >= 2 else 0
        if comment_depth >= 4 and random.random() < 0.35:
            additional_scrolls += 1
        for _ in range(additional_scrolls):
            if random.random() < 0.75:
                await self._h.scroll("down", amount=1)
                await self._h.delay(0.6, 1.4)

        await self._smooth_return_to_player()
        logger.info(
            "Session %s: %s micro — smooth return to player after comments glance",
            self._state.session_id, source_action,
        )
        await self._h.delay(0.3, 0.9)
        await self._playback.ensure_playing(self._state.session_id)

    async def _smooth_return_to_player(self) -> None:
        steps = random.randint(2, 5)
        for _ in range(steps):
            if await self._is_player_in_view():
                return
            await self._h.scroll("up", amount=1)
            await self._h.delay(0.35, 0.9)

        if await self._is_player_in_view():
            return

        try:
            await self._page.evaluate(
                """() => {
                    const player =
                        document.querySelector('#movie_player')
                        || document.querySelector('#player')
                        || document.querySelector('ytd-player')
                        || document.querySelector('video');
                    if (!player || typeof player.scrollIntoView !== 'function') {
                        return false;
                    }
                    player.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                        inline: 'nearest',
                    });
                    return true;
                }""",
            )
        except Exception:
            return
        await self._h.delay(0.5, 1.0)

    async def _is_player_in_view(self) -> bool:
        try:
            return bool(
                await self._page.evaluate(
                    """() => {
                        const player =
                            document.querySelector('#movie_player')
                            || document.querySelector('#player')
                            || document.querySelector('ytd-player')
                            || document.querySelector('video');
                        if (!player) return false;
                        const rect = player.getBoundingClientRect();
                        const vh = window.innerHeight || document.documentElement.clientHeight || 0;
                        if (!vh) return false;
                        return rect.top < vh * 0.72 && rect.bottom > vh * 0.18;
                    }""",
                )
            )
        except Exception:
            return False
