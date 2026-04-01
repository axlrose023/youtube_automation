YOUTUBE_URL = "https://www.youtube.com"
SEARCH_INPUT_SELECTORS = [
    "input#search",
    "input[name='search_query']",
    "input[aria-label='Search']",
    "input[aria-label='Поиск']",
]
MOBILE_SEARCH_INPUT_SELECTORS = [
    "input[name='search_query']",
    "input[type='search']",
    "input[aria-label='Search YouTube']",
    "input[aria-label='Search']",
    "input[aria-label='Поиск']",
    "form[role='search'] input",
    "form[action='/results'] input",
]
SEARCH_BUTTON_SELECTORS = [
    "button#search-icon-legacy",
    "button[aria-label='Search']",
    "button[aria-label='Поиск']",
]
MOBILE_SEARCH_BUTTON_SELECTORS = [
    "button[aria-label='Search YouTube']",
    "button[aria-label='Search']",
    "button[aria-label='Поиск']",
    "form[action='/results'] button",
    "button[type='submit']",
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
MOBILE_VIDEO_SELECTORS = [
    "ytm-video-with-context-renderer",
    "ytm-rich-item-renderer",
    "ytm-compact-video-renderer",
    "ytm-item-section-renderer ytm-video-with-context-renderer",
    "a.media-item-thumbnail-container[href*='/watch']",
    "a.compact-media-item-image[href*='/watch']",
    "a[href*='/watch?v=']",
    "a[href*='/watch']",
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
MOBILE_RECOMMENDED_SELECTORS = [
    "ytm-item-section-renderer ytm-video-with-context-renderer",
    "ytm-video-with-context-renderer",
    "ytm-compact-video-renderer",
    "ytm-rich-item-renderer",
    "a[href*='/watch?v=']",
    "a[href*='/watch']",
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
    ".ytp-ad-skip-button-container, "
    "[class*='skip-ad'], "
    "[class*='ad-skip'], "
    "[aria-label*='Skip ad'], "
    "[aria-label*='Skip Ads'], "
    "[aria-label*='Пропустить'], "
    "[aria-label*='Sponsored'], "
    "[aria-label*='Реклама']"
)
AD_SKIP_SELECTOR = (
    "button.ytp-skip-ad-button, "
    "button.ytp-ad-skip-button, "
    "button.ytp-ad-skip-button-modern, "
    ".ytp-ad-skip-button-container button, "
    "button[aria-label*='Skip ad'], "
    "button[aria-label*='Skip Ads'], "
    "button[aria-label*='Пропустить'], "
    "button[class*='skip-ad'], "
    "button[class*='ad-skip']"
)
AD_INFO_SELECTOR = (
    ".ytp-ad-player-overlay-layout__ad-info-container, "
    ".ytp-ad-player-overlay-title, "
    ".ytp-ad-text-overlay-title, "
    ".ytp-ad-message-container, "
    ".ytp-ad-preview-container, "
    ".ytp-ad-simple-ad-badge, "
    ".ytp-ad-text, "
    "[aria-label*='Sponsored'], "
    "[aria-label*='Реклама'], "
    "[class*='sponsor']"
)
AD_BUTTON_SELECTOR = (
    "a.ytp-ad-visit-advertiser-button, "
    "button.ytp-ad-visit-advertiser-button, "
    ".ytp-ad-button, "
    ".ytp-ad-action-interstitial-link, "
    "a[aria-label*='Learn more'], "
    "button[aria-label*='Learn more'], "
    "a[aria-label*='Install'], "
    "button[aria-label*='Install'], "
    "a[aria-label*='Узнать больше'], "
    "button[aria-label*='Узнать больше'], "
    "a[aria-label*='Установить'], "
    "button[aria-label*='Установить']"
)
AD_CAPTION_SELECTOR = ".ytp-caption-segment"


def search_input_selectors(*, is_mobile: bool) -> list[str]:
    return MOBILE_SEARCH_INPUT_SELECTORS if is_mobile else SEARCH_INPUT_SELECTORS


def search_button_selectors(*, is_mobile: bool) -> list[str]:
    return MOBILE_SEARCH_BUTTON_SELECTORS if is_mobile else SEARCH_BUTTON_SELECTORS


def video_selectors(*, is_mobile: bool) -> list[str]:
    return MOBILE_VIDEO_SELECTORS if is_mobile else VIDEO_SELECTORS


def recommended_selectors(*, is_mobile: bool) -> list[str]:
    return MOBILE_RECOMMENDED_SELECTORS if is_mobile else RECOMMENDED_SELECTORS
