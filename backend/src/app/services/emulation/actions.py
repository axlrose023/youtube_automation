from enum import Enum


class Action(str, Enum):


    WATCH_LONG = "watch_long"
    WATCH_FOCUSED = "watch_focused"
    SURF_VIDEO = "surf_video"
    CLICK_RECOMMENDED = "click_recommended"
    SEARCH = "search"
    REFINE_SEARCH = "refine_search"
    GO_HOME = "go_home"
    GO_BACK = "go_back"
    IDLE = "idle"
    SCROLL_FEED = "scroll_feed"
    SCROLL_RESULTS = "scroll_results"


WATCH_ACTIONS = frozenset(
    (
        Action.WATCH_LONG,
        Action.WATCH_FOCUSED,
        Action.SURF_VIDEO,
        Action.CLICK_RECOMMENDED,
    )
)

SEARCH_ACTIONS = frozenset(
    (
        Action.SEARCH,
        Action.REFINE_SEARCH,
    )
)
