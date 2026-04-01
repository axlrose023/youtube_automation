from __future__ import annotations

import logging
import random

from playwright.async_api import Page

from ..config import (
    ENGAGEMENT_MAX_ATTEMPTS_PER_WATCH,
    LIKE_RELEVANT_MIN_WATCH_S,
    LIKE_RELEVANT_TARGET_FRACTION,
    SUBSCRIBE_MAX_PER_SESSION,
    SUBSCRIBE_RELEVANT_MIN_WATCH_S,
    SUBSCRIBE_RELEVANT_PROBABILITY,
    SUBSCRIBE_RELEVANT_TARGET_FRACTION,
)
from ..session.state import SessionState
from .humanizer import Humanizer

logger = logging.getLogger(__name__)


class EngagementController:
    def __init__(self, page: Page, state: SessionState, humanizer: Humanizer) -> None:
        self._page = page
        self._state = state
        self._h = humanizer

    async def maybe_engage(self, *, source_action: str, default_target_seconds: float) -> None:
        watch = self._state.current_watch
        if not isinstance(watch, dict):
            return
        if not self._is_relevant_watch(watch):
            return

        title = str(watch.get("title") or "").strip()
        url = str(watch.get("url") or self._page.url or "").strip()
        watched_seconds = self._coerce_float(watch.get("watched_seconds")) or 0.0
        target_seconds = (
            self._coerce_float(watch.get("target_seconds"))
            or max(default_target_seconds, watched_seconds, 1.0)
        )

        if self._should_attempt_like(watch, url, watched_seconds, target_seconds):
            await self._attempt_like(watch=watch, url=url, source_action=source_action)

        if self._should_attempt_subscribe(watch, title, watched_seconds, target_seconds):
            await self._attempt_subscribe(watch=watch, source_action=source_action)

    def _is_relevant_watch(self, watch: dict[str, object]) -> bool:
        matched_topics = watch.get("matched_topics")
        return isinstance(matched_topics, list) and any(isinstance(topic, str) and topic.strip() for topic in matched_topics)

    def _should_attempt_like(
        self,
        watch: dict[str, object],
        url: str,
        watched_seconds: float,
        target_seconds: float,
    ) -> bool:
        if bool(watch.get("liked")):
            return False
        if self._state.has_liked_video(url):
            watch["liked"] = True
            return False
        attempts = int(watch.get("engagement_like_attempts") or 0)
        if attempts >= ENGAGEMENT_MAX_ATTEMPTS_PER_WATCH:
            return False
        threshold = max(LIKE_RELEVANT_MIN_WATCH_S, target_seconds * LIKE_RELEVANT_TARGET_FRACTION)
        return watched_seconds >= threshold

    def _should_attempt_subscribe(
        self,
        watch: dict[str, object],
        title: str,
        watched_seconds: float,
        target_seconds: float,
    ) -> bool:
        if bool(watch.get("subscribed")):
            return False
        if len(self._state.subscribed_channel_keys) >= SUBSCRIBE_MAX_PER_SESSION:
            return False
        if not self._is_strong_relevance(title, watch):
            return False
        attempts = int(watch.get("engagement_subscribe_attempts") or 0)
        if attempts >= ENGAGEMENT_MAX_ATTEMPTS_PER_WATCH:
            return False
        if watch.get("subscribe_allowed") is None:
            watch["subscribe_allowed"] = random.random() < SUBSCRIBE_RELEVANT_PROBABILITY
        if not bool(watch.get("subscribe_allowed")):
            return False
        threshold = max(SUBSCRIBE_RELEVANT_MIN_WATCH_S, target_seconds * SUBSCRIBE_RELEVANT_TARGET_FRACTION)
        return watched_seconds >= threshold

    def _is_strong_relevance(self, title: str, watch: dict[str, object]) -> bool:
        current_topic = str(watch.get("search_keyword") or self._state.current_topic or "").strip()
        if not current_topic:
            return False
        return self._state.is_title_on_specific_topic(title, current_topic)

    async def _attempt_like(self, *, watch: dict[str, object], url: str, source_action: str) -> None:
        watch["engagement_like_attempts"] = int(watch.get("engagement_like_attempts") or 0) + 1
        result = await self._toggle_primary_button(kind="like")
        state = str(result.get("state") or "").strip()
        if state not in {"active", "clicked"}:
            return
        if state == "clicked":
            await self._h.delay(0.4, 0.9)
            confirmed = await self._read_primary_button_state(kind="like")
            if confirmed != "active":
                logger.info(
                    "Session %s: %s engagement — like click did not confirm active state",
                    self._state.session_id,
                    source_action,
                )
                return

        self._state.mark_video_liked(url)
        watch["liked"] = True
        logger.info(
            "Session %s: %s engagement — %s video like",
            self._state.session_id,
            source_action,
            "confirmed existing" if state == "active" else "clicked",
        )
        await self._h.delay(0.4, 1.0)

    async def _attempt_subscribe(self, *, watch: dict[str, object], source_action: str) -> None:
        watch["engagement_subscribe_attempts"] = int(watch.get("engagement_subscribe_attempts") or 0) + 1

        channel = await self._read_channel_identity()
        channel_key = str(channel.get("channel_key") or "").strip()
        channel_name = str(channel.get("channel_name") or "").strip()
        if not channel_key and not channel_name:
            return

        channel_key = channel_key or channel_name.lower()
        if self._state.has_subscribed_channel(channel_key):
            watch["subscribed"] = True
            if channel_key:
                watch["channel_key"] = channel_key
            if channel_name:
                watch["channel_name"] = channel_name
            return

        result = await self._toggle_primary_button(kind="subscribe")
        state = str(result.get("state") or "").strip()
        if state not in {"active", "clicked"}:
            return
        if state == "clicked":
            await self._h.delay(0.6, 1.1)
            confirmed = await self._read_primary_button_state(kind="subscribe")
            if confirmed != "active":
                logger.info(
                    "Session %s: %s engagement — subscribe click did not confirm active state",
                    self._state.session_id,
                    source_action,
                )
                return

        self._state.mark_channel_subscribed(channel_key)
        watch["subscribed"] = True
        if channel_key:
            watch["channel_key"] = channel_key
        if channel_name:
            watch["channel_name"] = channel_name
        logger.info(
            "Session %s: %s engagement — %s channel subscribe (%s)",
            self._state.session_id,
            source_action,
            "confirmed existing" if state == "active" else "clicked",
            channel_name or channel_key,
        )
        await self._h.delay(0.6, 1.2)

    async def _read_channel_identity(self) -> dict[str, str | None]:
        try:
            payload = await self._page.evaluate(
                """() => {
                    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const selectors = [
                        'ytm-slim-owner-renderer a[href]',
                        'ytm-video-owner-renderer a[href]',
                        '#owner a[href]',
                        'ytd-watch-metadata #owner a[href]',
                        'ytd-video-owner-renderer a[href]',
                    ];
                    for (const selector of selectors) {
                        const node = document.querySelector(selector);
                        if (!node) continue;
                        const href = clean(node.getAttribute('href'));
                        const name = clean(node.innerText || node.textContent);
                        if (!href && !name) continue;
                        return {
                            channel_key: href || null,
                            channel_name: name || null,
                        };
                    }
                    return { channel_key: null, channel_name: null };
                }"""
            )
        except Exception:
            payload = None

        if not isinstance(payload, dict):
            return {"channel_key": None, "channel_name": None}
        return {
            "channel_key": payload.get("channel_key") if isinstance(payload.get("channel_key"), str) else None,
            "channel_name": payload.get("channel_name") if isinstance(payload.get("channel_name"), str) else None,
        }

    async def _toggle_primary_button(self, *, kind: str) -> dict[str, str | bool | None]:
        try:
            payload = await self._page.evaluate(
                """(kind) => {
                    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (node) => {
                        if (!(node instanceof HTMLElement)) return false;
                        const style = window.getComputedStyle(node);
                        if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 18 && rect.height > 18;
                    };
                    const lower = (value) => clean(value).toLowerCase();
                    const cfg = kind === 'subscribe'
                        ? {
                            include: ['subscribe', 'подпис', 'підпис'],
                            active: ['subscribed', 'вы подписаны', 'ви підписані', 'unsubscribe', 'отменить подписку', 'скасувати підписку'],
                            exclude: ['dislike', 'не нравится', 'не подобається'],
                            structuralInclude: ['subscribe-button', 'ytd-subscribe-button-renderer', 'ytm-subscribe-button-renderer'],
                            structuralExclude: [],
                        }
                        : {
                            include: ['like', 'нрав', 'подоба'],
                            active: ['liked', 'убрать отметку', 'remove like', 'подобається'],
                            exclude: ['dislike', 'не нравится', 'не подобається', 'subscribe', 'подпис', 'підпис'],
                            structuralInclude: [
                                'like-button-view-model',
                                'ytlikebuttonviewmodelhost',
                                'segmented-likedislike-button-view-model',
                                'top-level-buttons-computed',
                            ],
                            structuralExclude: ['dislikebutton'],
                        };

                    const candidates = Array.from(
                        document.querySelectorAll(
                            'button, [role="button"], a, '
                            + 'ytd-subscribe-button-renderer, ytm-subscribe-button-renderer, '
                            + 'ytd-toggle-button-renderer, ytd-like-button-renderer, '
                            + 'segmented-like-dislike-button-view-model, like-button-view-model, '
                            + 'toggle-button-view-model, button-view-model'
                        ),
                    );

                    let best = null;
                    let bestScore = -1;

                    for (const node of candidates) {
                        const element = node instanceof HTMLElement ? node : node.querySelector?.('button, [role="button"], a');
                        const target = element instanceof HTMLElement ? element : node instanceof HTMLElement ? node : null;
                        if (!target || !visible(target)) continue;

                        const parts = [
                            target.getAttribute?.('aria-label'),
                            target.getAttribute?.('title'),
                            target.getAttribute?.('id'),
                            target.getAttribute?.('class'),
                            target.innerText,
                            target.textContent,
                            target.closest?.('[aria-label]')?.getAttribute?.('aria-label'),
                            target.closest?.('[title]')?.getAttribute?.('title'),
                            target.closest?.('[id]')?.getAttribute?.('id'),
                            target.closest?.('[class]')?.getAttribute?.('class'),
                        ];
                        const text = lower(parts.filter(Boolean).join(' '));
                        if (!text) continue;
                        const includedByText = cfg.include.some((token) => text.includes(token));
                        const includedStructurally = cfg.structuralInclude.some((token) => text.includes(token));
                        if (!(includedByText || includedStructurally)) continue;
                        if (cfg.exclude.some((token) => text.includes(token))) continue;
                        if (cfg.structuralExclude.some((token) => text.includes(token))) continue;

                        const ariaPressed = clean(target.getAttribute?.('aria-pressed')).toLowerCase();
                        const active =
                            ariaPressed === 'true'
                            || !!target.closest?.('[aria-pressed="true"]')
                            || !!target.querySelector?.('[aria-pressed="true"]')
                            || cfg.active.some((token) => text.includes(token));
                        const score =
                            (target.tagName === 'BUTTON' ? 3 : 1)
                            + (target.closest?.('ytd-watch-metadata, ytd-menu-renderer, ytm-slim-owner-renderer, ytm-slim-video-action-bar-renderer') ? 3 : 0)
                            + (target.hasAttribute?.('aria-label') ? 2 : 0)
                            + (active ? 1 : 0);
                        if (score > bestScore) {
                            best = { target, active, text };
                            bestScore = score;
                        }
                    }

                    if (!best) return { state: 'missing', clicked: false };
                    if (best.active) return { state: 'active', clicked: false };

                    best.target.scrollIntoView({ block: 'center', inline: 'nearest' });
                    const nested =
                        best.target.matches?.('button, [role="button"], a')
                            ? best.target
                            : best.target.querySelector?.('button, [role="button"], a');
                    if (nested && typeof nested.click === 'function') {
                        nested.click();
                    } else {
                        best.target.click();
                    }
                    return { state: 'clicked', clicked: true };
                }""",
                kind,
            )
        except Exception as exc:
            logger.debug(
                "Session %s: engagement %s toggle failed: %s",
                self._state.session_id,
                kind,
                exc,
            )
            return {"state": "error", "clicked": False}

        if not isinstance(payload, dict):
            return {"state": "error", "clicked": False}
        return {
            "state": payload.get("state") if isinstance(payload.get("state"), str) else "error",
            "clicked": bool(payload.get("clicked")),
        }

    async def _read_primary_button_state(self, *, kind: str) -> str:
        try:
            payload = await self._page.evaluate(
                """(kind) => {
                    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const visible = (node) => {
                        if (!(node instanceof HTMLElement)) return false;
                        const style = window.getComputedStyle(node);
                        if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 18 && rect.height > 18;
                    };
                    const lower = (value) => clean(value).toLowerCase();
                    const cfg = kind === 'subscribe'
                        ? {
                            include: ['subscribe', 'подпис', 'підпис'],
                            active: ['subscribed', 'вы подписаны', 'ви підписані', 'unsubscribe', 'отменить подписку', 'скасувати підписку'],
                            exclude: ['dislike', 'не нравится', 'не подобається'],
                            structuralInclude: ['subscribe-button', 'ytd-subscribe-button-renderer', 'ytm-subscribe-button-renderer'],
                            structuralExclude: [],
                        }
                        : {
                            include: ['like', 'нрав', 'подоба'],
                            active: ['liked', 'убрать отметку', 'remove like', 'подобається'],
                            exclude: ['dislike', 'не нравится', 'не подобається', 'subscribe', 'подпис', 'підпис'],
                            structuralInclude: [
                                'like-button-view-model',
                                'ytlikebuttonviewmodelhost',
                                'segmented-likedislike-button-view-model',
                                'top-level-buttons-computed',
                            ],
                            structuralExclude: ['dislikebutton'],
                        };
                    const candidates = Array.from(
                        document.querySelectorAll(
                            'button, [role="button"], a, '
                            + 'ytd-subscribe-button-renderer, ytm-subscribe-button-renderer, '
                            + 'ytd-toggle-button-renderer, ytd-like-button-renderer, '
                            + 'segmented-like-dislike-button-view-model, like-button-view-model, '
                            + 'toggle-button-view-model, button-view-model'
                        ),
                    );
                    let best = null;
                    let bestScore = -1;
                    for (const node of candidates) {
                        const element = node instanceof HTMLElement ? node : node.querySelector?.('button, [role="button"], a');
                        const target = element instanceof HTMLElement ? element : node instanceof HTMLElement ? node : null;
                        if (!target || !visible(target)) continue;
                        const parts = [
                            target.getAttribute?.('aria-label'),
                            target.getAttribute?.('title'),
                            target.getAttribute?.('id'),
                            target.getAttribute?.('class'),
                            target.innerText,
                            target.textContent,
                            target.closest?.('[aria-label]')?.getAttribute?.('aria-label'),
                            target.closest?.('[title]')?.getAttribute?.('title'),
                            target.closest?.('[id]')?.getAttribute?.('id'),
                            target.closest?.('[class]')?.getAttribute?.('class'),
                        ];
                        const text = lower(parts.filter(Boolean).join(' '));
                        if (!text) continue;
                        const includedByText = cfg.include.some((token) => text.includes(token));
                        const includedStructurally = cfg.structuralInclude.some((token) => text.includes(token));
                        if (!(includedByText || includedStructurally)) continue;
                        if (cfg.exclude.some((token) => text.includes(token))) continue;
                        if (cfg.structuralExclude.some((token) => text.includes(token))) continue;
                        const ariaPressed = clean(target.getAttribute?.('aria-pressed')).toLowerCase();
                        const active =
                            ariaPressed === 'true'
                            || !!target.closest?.('[aria-pressed="true"]')
                            || !!target.querySelector?.('[aria-pressed="true"]')
                            || cfg.active.some((token) => text.includes(token));
                        const score =
                            (target.tagName === 'BUTTON' ? 3 : 1)
                            + (target.closest?.('ytd-watch-metadata, ytd-menu-renderer, ytm-slim-owner-renderer, ytm-slim-video-action-bar-renderer') ? 3 : 0)
                            + (target.hasAttribute?.('aria-label') ? 2 : 0)
                            + (active ? 1 : 0);
                        if (score > bestScore) {
                            best = { active };
                            bestScore = score;
                        }
                    }
                    if (!best) return { state: 'missing' };
                    return { state: best.active ? 'active' : 'inactive' };
                }""",
                kind,
            )
        except Exception:
            return "error"
        if not isinstance(payload, dict):
            return "error"
        return payload.get("state") if isinstance(payload.get("state"), str) else "error"

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None
