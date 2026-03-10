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
    "a#video-title[href*='/watch']",
    "a#video-title-link[href*='/watch']",
    "ytd-rich-item-renderer a#video-title-link",
    "yt-lockup-view-model a.yt-lockup-metadata-view-model__title[href*='/watch']",
    "yt-lockup-view-model a.yt-lockup-view-model__content-image[href*='/watch']",
    "ytd-video-renderer h3.title-and-badge a",
    "ytd-rich-item-renderer h3 a",
    "a[href*='/watch?v=']",
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


AD_OVERLAY_SELECTOR = (
    ".ytp-ad-player-overlay, "
    ".ytp-ad-text, "
    ".ad-showing, "
    ".ytp-ad-skip-button-container"
)
AD_SKIP_SELECTOR = (
    "button.ytp-skip-ad-button, "
    "button.ytp-ad-skip-button, "
    "button.ytp-ad-skip-button-modern, "
    ".ytp-ad-skip-button-container button"
)
AD_INFO_SELECTOR = (
    ".ytp-ad-player-overlay-layout__ad-info-container, "
    ".ytp-ad-player-overlay-title, "
    ".ytp-ad-text-overlay-title, "
    ".ytp-ad-message-container, "
    ".ytp-ad-preview-container, "
    ".ytp-ad-simple-ad-badge, "
    ".ytp-ad-text"
)
AD_BUTTON_SELECTOR = (
    "a.ytp-ad-visit-advertiser-button, "
    "button.ytp-ad-visit-advertiser-button, "
    ".ytp-ad-button, "
    ".ytp-ad-action-interstitial-link"
)
AD_CAPTION_SELECTOR = ".ytp-caption-segment"
