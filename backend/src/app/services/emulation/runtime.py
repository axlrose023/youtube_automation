from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from playwright.async_api import Page

from .browser.ads.capture import AdCaptureProvider
from .browser.ads.handler import AdHandler
from .browser.humanizer import Humanizer
from .browser.navigator import Navigator
from .browser.playback import PlaybackController
from .browser.searcher import Searcher
from .browser.traffic import TrafficTracker
from .browser.video_finder import VideoFinder
from .browser.watcher import VideoWatcher
from .decision import ActionPicker
from .session.clock import SessionClock
from .session.fatigue import FatigueManager
from .session.state import SessionState
from .workflow.dispatcher import ActionDispatcher


@dataclass(frozen=True)
class EmulationRuntime:
    ads: AdHandler
    humanizer: Humanizer
    navigator: Navigator
    clock: SessionClock
    picker: ActionPicker
    fatigue: FatigueManager
    dispatcher: ActionDispatcher
    traffic: TrafficTracker


def build_runtime(
    page: Page,
    state: SessionState,
    capture: AdCaptureProvider | None = None,
    on_capture_ready: Callable[[list[dict[str, object]], dict[str, object]], Awaitable[None]] | None = None,
) -> EmulationRuntime:
    humanizer = Humanizer(page, state)
    ads = AdHandler(page, humanizer, state, capture=capture, on_capture_ready=on_capture_ready)
    playback = PlaybackController(page, humanizer)
    finder = VideoFinder(page, state, humanizer)

    navigator = Navigator(page, state, humanizer, finder)
    searcher = Searcher(page, state, humanizer, navigator=navigator)
    navigator.set_searcher(searcher)

    watcher = VideoWatcher(page, state, navigator, humanizer, ads, playback)
    clock = SessionClock(state)
    picker = ActionPicker(state)
    fatigue = FatigueManager(state, humanizer, navigator)
    dispatcher = ActionDispatcher(state, navigator, watcher, clock)
    traffic = TrafficTracker(page)

    return EmulationRuntime(
        ads=ads,
        humanizer=humanizer,
        navigator=navigator,
        clock=clock,
        picker=picker,
        fatigue=fatigue,
        dispatcher=dispatcher,
        traffic=traffic,
    )
