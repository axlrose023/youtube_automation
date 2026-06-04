"""Browser automation package.

Keep package import side effects minimal so lightweight consumers, such as the
Android landing scraper, can import deep utilities without pulling Playwright-
heavy desktop modules eagerly.
"""

__all__ = [
    "AdHandler",
    "Humanizer",
    "Navigator",
    "PlaybackController",
    "Searcher",
    "TrafficTracker",
    "VideoFinder",
    "VideoWatcher",
]


def __getattr__(name: str):
    if name == "AdHandler":
        from .ads.handler import AdHandler

        return AdHandler
    if name == "Humanizer":
        from .humanizer import Humanizer

        return Humanizer
    if name == "Navigator":
        from .navigator import Navigator

        return Navigator
    if name == "PlaybackController":
        from .playback import PlaybackController

        return PlaybackController
    if name == "Searcher":
        from .searcher import Searcher

        return Searcher
    if name == "TrafficTracker":
        from .traffic import TrafficTracker

        return TrafficTracker
    if name == "VideoFinder":
        from .video_finder import VideoFinder

        return VideoFinder
    if name == "VideoWatcher":
        from .watcher import VideoWatcher

        return VideoWatcher
    raise AttributeError(name)
