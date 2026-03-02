YOUTUBE_URL = "https://www.youtube.com"
SEARCH_INPUT_SELECTORS = [
    "input#search",
    "input[name='search_query']",
    "input[aria-label='Search']",
    "input[aria-label='Поиск']",
]
SEARCH_BUTTON_SELECTORS = [
    "button#search-icon-legacy",
    "button[aria-label='Search']",
    "button[aria-label='Поиск']",
]
SEARCH_INPUT = SEARCH_INPUT_SELECTORS[0]
SEARCH_BUTTON = SEARCH_BUTTON_SELECTORS[0]

VIDEO_SELECTORS = [
    "ytd-video-renderer a#video-title",
    "ytd-rich-item-renderer a#video-title-link",
    "ytd-video-renderer h3.title-and-badge a",
    "ytd-rich-item-renderer h3 a",
]
RECOMMENDED_SELECTORS = [
    "#secondary #items yt-lockup-view-model a.yt-lockup-metadata-view-model__title",
    "ytd-watch-next-secondary-results-renderer #items yt-lockup-view-model a.yt-lockup-metadata-view-model__title",
    "#secondary #items yt-lockup-view-model a.yt-lockup-view-model__content-image",
    "ytd-watch-next-secondary-results-renderer #items yt-lockup-view-model a.yt-lockup-view-model__content-image",
    "ytd-watch-next-secondary-results-renderer a#video-title",
    "ytd-watch-next-secondary-results-renderer a#thumbnail",
    "#secondary #contents ytd-compact-video-renderer a#video-title",
    "#secondary #contents ytd-compact-video-renderer a#thumbnail",
    "#secondary ytd-compact-video-renderer a#video-title",
    "#secondary ytd-compact-video-renderer a#thumbnail",
    "#related ytd-compact-video-renderer a#video-title",
    "#related ytd-compact-video-renderer a#thumbnail",
    "ytd-compact-video-renderer a#video-title",
    "ytd-compact-video-renderer .metadata a",
    "ytd-rich-item-renderer a#video-title-link",
    "ytd-reel-item-renderer a#thumbnail",
]
CONSENT_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('I agree')",
    "button:has-text('Принять все')",
    "button:has-text('Я принимаю')",
    "button[aria-label*='Accept']",
    "tp-yt-paper-button.ytd-consent-bump-v2-lightbox",
    "button[aria-label*='Reject']",
]

MAX_CONSECUTIVE_FAILURES = 4
MAX_SURF_STREAK = 4
MAX_RECOMMENDED_STREAK = 2

SEARCH_MODIFIERS = [
    "tutorial", "how to", "best", "review", "explained",
    "for beginners", "tips", "guide", "vs", "top 10",
]
