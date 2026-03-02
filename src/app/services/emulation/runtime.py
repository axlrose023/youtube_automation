from dataclasses import dataclass

from playwright.async_api import Page

from .browser.ad_handler import AdHandler
from .browser.humanizer import Humanizer
from .browser.navigator import Navigator
from .browser.playback import PlaybackController
from .browser.searcher import Searcher
from .browser.traffic import TrafficTracker
from .browser.video_finder import VideoFinder
from .browser.watcher import VideoWatcher
from .core.state import SessionState
from .strategy.action_picker import ActionPicker
from .strategy.clock import SessionClock
from .strategy.dispatcher import ActionDispatcher
from .strategy.fatigue import FatigueManager


@dataclass(frozen=True)
class EmulationRuntime:
    humanizer: Humanizer
    navigator: Navigator
    clock: SessionClock
    picker: ActionPicker
    fatigue: FatigueManager
    dispatcher: ActionDispatcher
    traffic: TrafficTracker


def build_runtime(page: Page, state: SessionState) -> EmulationRuntime:
    humanizer = Humanizer(page, state)
    ads = AdHandler(page, humanizer, state)
    playback = PlaybackController(page, humanizer)
    finder = VideoFinder(page, state, humanizer)

    navigator = Navigator(page, state, humanizer, finder)
    searcher = Searcher(
        page,
        state,
        humanizer,
        dismiss_consent=navigator.dismiss_consent,
        go_home=navigator.safe_go_home,
    )
    navigator.attach_searcher(searcher)

    watcher = VideoWatcher(page, state, navigator, humanizer, ads, playback)
    clock = SessionClock(state)
    picker = ActionPicker(state)
    fatigue = FatigueManager(state, humanizer, navigator)
    dispatcher = ActionDispatcher(state, navigator, watcher, clock)
    traffic = TrafficTracker(page)

    return EmulationRuntime(
        humanizer=humanizer,
        navigator=navigator,
        clock=clock,
        picker=picker,
        fatigue=fatigue,
        dispatcher=dispatcher,
        traffic=traffic,
    )
