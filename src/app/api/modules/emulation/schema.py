import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.common.schema import Pagination, PaginationParams


class StartEmulationRequest(BaseModel):
    duration_minutes: int = Field(ge=1, le=480, description="Session duration in minutes")
    topics: list[str] = Field(min_length=1, max_length=20, description="Search topics")
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="AdsPower profile id for this emulation session",
    )


class StartEmulationResponse(BaseModel):
    session_id: UUID
    status: str


class EmulationWatchedVideo(BaseModel):
    position: int
    action: str
    title: str
    url: str
    watched_seconds: float
    target_seconds: float
    watch_ratio: float | None = None
    completed: bool
    search_keyword: str | None = None
    matched_topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    recorded_at: float


class EmulationAdTextSample(BaseModel):
    offset_seconds: float
    visible_lines: list[str] = Field(default_factory=list)
    caption_lines: list[str] = Field(default_factory=list)
    skip_visible: bool = False


class EmulationWatchedAd(BaseModel):
    position: int
    started_at: float
    ended_at: float
    watched_seconds: float
    completed: bool
    skip_clicked: bool
    skip_visible: bool
    skip_text: str | None = None
    cta_text: str | None = None
    cta_candidates: list[str] = Field(default_factory=list)
    cta_href: str | None = None
    sponsor_label: str | None = None
    advertiser_domain: str | None = None
    display_url: str | None = None
    display_url_decoded: str | None = None
    landing_urls: list[str] = Field(default_factory=list)
    headline_text: str | None = None
    description_text: str | None = None
    description_lines: list[str] = Field(default_factory=list)
    ad_pod_position: int | None = None
    ad_pod_total: int | None = None
    ad_duration_seconds: float | None = None
    my_ad_center_visible: bool = False
    full_text: str = ""
    full_text_source: str = "overlay"
    full_visible_text: str = ""
    full_caption_text: str = ""
    visible_lines: list[str] = Field(default_factory=list)
    caption_lines: list[str] = Field(default_factory=list)
    text_samples: list[EmulationAdTextSample] = Field(default_factory=list)
    end_reason: str | None = None
    recorded_at: float


class EmulationAnalyticsAd(BaseModel):
    watched_seconds: float
    completed: bool
    skip_clicked: bool
    skip_visible: bool
    skip_text: str | None = None
    cta_text: str | None = None
    cta_href: str | None = None
    sponsor_label: str | None = None
    advertiser_domain: str | None = None
    display_url: str | None = None
    landing_urls: list[str] = Field(default_factory=list)
    headline_text: str | None = None
    description_text: str | None = None
    ad_pod_position: int | None = None
    ad_pod_total: int | None = None
    ad_duration_seconds: float | None = None
    my_ad_center_visible: bool = False
    full_text: str = ""
    full_visible_text: str = ""
    full_caption_text: str = ""


class EmulationCurrentWatch(BaseModel):
    action: str
    title: str
    url: str
    started_at: float
    watched_seconds: float = 0.0
    target_seconds: float | None = None
    search_keyword: str | None = None
    matched_topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class EmulationSessionStatus(BaseModel):
    session_id: UUID
    status: str
    profile_id: str | None = None
    elapsed_minutes: float | None = None
    orchestration_enabled: bool = False
    orchestration_phase: str | None = None
    next_resume_at: float | None = None
    active_budget_seconds: int | None = None
    active_spent_seconds: int | None = None
    bytes_downloaded: int = 0
    topics_searched: list[str] = Field(default_factory=list)
    videos_watched: int = 0
    watched_videos_count: int = 0
    total_duration_seconds: int = 0
    watched_videos: list[EmulationWatchedVideo] = Field(default_factory=list)
    current_watch: EmulationCurrentWatch | None = None
    watched_ads_count: int = 0
    watched_ads: list[EmulationWatchedAd] = Field(default_factory=list)
    watched_ads_analytics: list[EmulationAnalyticsAd] = Field(default_factory=list)
    mode: str | None = None
    fatigue: float | None = None
    error: str | None = None


class EmulationAdCaptureScreenshotPath(BaseModel):
    offset_ms: int
    file_path: str


class EmulationAdCaptureHistory(BaseModel):
    ad_position: int
    advertiser_domain: str | None = None
    cta_href: str | None = None
    display_url: str | None = None
    headline_text: str | None = None
    ad_duration_seconds: float | None = None
    landing_url: str | None = None
    landing_dir: str | None = None
    landing_status: str
    video_src_url: str | None = None
    video_file: str | None = None
    video_status: str
    screenshot_paths: list[EmulationAdCaptureScreenshotPath] = Field(default_factory=list)


class EmulationCaptureSummary(BaseModel):
    ads_total: int = 0
    video_captures: int = 0
    screenshot_fallbacks: int = 0


class EmulationHistoryItem(BaseModel):
    session_id: UUID
    status: str
    requested_duration_minutes: int
    requested_topics: list[str] = Field(default_factory=list)
    queued_at: datetime.datetime
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    elapsed_minutes: float | None = None
    mode: str | None = None
    fatigue: float | None = None
    bytes_downloaded: int = 0
    total_duration_seconds: int = 0
    videos_watched: int = 0
    watched_videos_count: int = 0
    watched_ads_count: int = 0
    topics_searched: list[str] = Field(default_factory=list)
    watched_videos: list[EmulationWatchedVideo] | None = None
    watched_ads: list[EmulationWatchedAd] | None = None
    watched_ads_analytics: list[EmulationAnalyticsAd] | None = None
    error: str | None = None
    captures: EmulationCaptureSummary = Field(default_factory=EmulationCaptureSummary)
    ad_captures: list[EmulationAdCaptureHistory] | None = None


class EmulationHistoryDetailResponse(EmulationHistoryItem):
    pass


class EmulationHistoryResponse(Pagination[EmulationHistoryItem]):
    model_config = ConfigDict(from_attributes=True)


class EmulationHistoryParams(PaginationParams):
    session_id: UUID | None = None
    status: str | None = None
    mode: str | None = None
    topic__search: str | None = None
    has_ads: bool | None = None
    has_video_capture: bool | None = None
    has_screenshot_capture: bool | None = None
    queued_from: datetime.datetime | None = None
    queued_to: datetime.datetime | None = None
    started_from: datetime.datetime | None = None
    started_to: datetime.datetime | None = None
    finished_from: datetime.datetime | None = None
    finished_to: datetime.datetime | None = None
    include_details: bool = False
    include_captures: bool = False
    include_raw_ads: bool = False
