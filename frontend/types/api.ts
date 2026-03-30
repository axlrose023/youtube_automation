export interface Pagination<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
}

export interface User {
  id: string;
  username: string;
  is_admin: boolean;
  is_active: boolean;
}

export interface StartEmulationRequest {
  duration_minutes: number;
  topics: string[];
  profile_id?: string | null;
}

export interface StartEmulationResponse {
  session_id: string;
  status: string;
}

export interface EmulationSessionActionRequest {
  session_id: string;
}

export interface EmulationStatusBatchRequest {
  session_ids: string[];
}

export interface CaptureSummary {
  ads_total: number;
  video_captures: number;
  screenshot_fallbacks: number;
}

export interface PostProcessingProgress {
  done: number;
  total: number;
}

export interface EmulationAdCapture {
  ad_position: number;
  advertiser_domain?: string | null;
  cta_href?: string | null;
  display_url?: string | null;
  headline_text?: string | null;
  ad_duration_seconds?: number | null;
  landing_url?: string | null;
  landing_dir?: string | null;
  landing_status: string;
  video_src_url?: string | null;
  video_file?: string | null;
  video_status: string;
  analysis_status?: string | null;
  analysis_summary?: Record<string, unknown> | null;
  screenshot_paths: Array<{ offset_ms: number; file_path: string }>;
}

export interface EmulationLiveAdCapture {
  video_src_url?: string | null;
  video_status?: string | null;
  video_file?: string | null;
  landing_url?: string | null;
  landing_status?: string | null;
  landing_dir?: string | null;
  analysis_status?: string | null;
  analysis_summary?: Record<string, unknown> | null;
  screenshot_paths: Array<{ offset_ms: number; file_path: string }>;
}

export interface EmulationWatchedVideo {
  position: number;
  action: string;
  title: string;
  url: string;
  watched_seconds: number;
  target_seconds: number;
  watch_ratio?: number | null;
  completed: boolean;
  search_keyword?: string | null;
  matched_topics: string[];
  keywords: string[];
  recorded_at: number;
}

export interface EmulationWatchedAd {
  position: number;
  started_at: number;
  ended_at: number;
  watched_seconds: number;
  completed: boolean;
  advertiser_domain?: string | null;
  display_url?: string | null;
  headline_text?: string | null;
  description_text?: string | null;
  landing_urls: string[];
  skip_visible: boolean;
  skip_clicked: boolean;
  cta_text?: string | null;
  full_text: string;
  ad_duration_seconds?: number | null;
  end_reason?: string | null;
  capture?: EmulationLiveAdCapture | null;
  recorded_at: number;
}

export interface EmulationAnalyticsAd {
  watched_seconds: number;
  completed: boolean;
  skip_clicked: boolean;
  skip_visible: boolean;
  advertiser_domain?: string | null;
  display_url?: string | null;
  landing_urls: string[];
  headline_text?: string | null;
  description_text?: string | null;
  ad_duration_seconds?: number | null;
  full_text: string;
}

export interface EmulationHistoryItem {
  session_id: string;
  status: string;
  post_processing_status?: string | null;
  post_processing_progress?: PostProcessingProgress | null;
  requested_duration_minutes: number;
  requested_topics: string[];
  queued_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_minutes?: number | null;
  mode?: string | null;
  fatigue?: number | null;
  bytes_downloaded: number;
  total_duration_seconds: number;
  videos_watched: number;
  watched_videos_count: number;
  watched_ads_count: number;
  topics_searched: string[];
  watched_videos?: EmulationWatchedVideo[] | null;
  watched_ads?: EmulationWatchedAd[] | null;
  watched_ads_analytics?: EmulationAnalyticsAd[] | null;
  error?: string | null;
  captures: CaptureSummary;
  ad_captures?: EmulationAdCapture[] | null;
}

export interface EmulationHistoryDetail extends EmulationHistoryItem {}

export interface EmulationSessionStatus {
  session_id: string;
  status: string;
  post_processing_status?: string | null;
  post_processing_progress?: PostProcessingProgress | null;
  profile_id?: string | null;
  elapsed_minutes?: number | null;
  orchestration_enabled: boolean;
  orchestration_phase?: string | null;
  next_resume_at?: number | null;
  active_budget_seconds?: number | null;
  active_spent_seconds?: number | null;
  bytes_downloaded: number;
  requested_topics: string[];
  topics_searched: string[];
  videos_watched: number;
  watched_videos_count: number;
  total_duration_seconds: number;
  watched_videos: EmulationWatchedVideo[];
  current_watch?: {
    action: string;
    title: string;
    url: string;
    started_at: number;
    watched_seconds: number;
    target_seconds?: number | null;
    search_keyword?: string | null;
    matched_topics: string[];
    keywords: string[];
  } | null;
  watched_ads_count: number;
  watched_ads: EmulationWatchedAd[];
  watched_ads_analytics: EmulationAnalyticsAd[];
  mode?: string | null;
  fatigue?: number | null;
  error?: string | null;
}

export interface EmulationStatusBatchResponse {
  statuses: Record<string, EmulationSessionStatus>;
}

export interface DashboardSummaryItem {
  label: string;
  value: number;
}

export interface EmulationDashboardSummary {
  total_sessions: number;
  completed: number;
  running: number;
  failed: number;
  stopped: number;
  total_videos_watched: number;
  avg_videos_per_session: number;
  total_ads_watched: number;
  total_ad_captures: number;
  video_captures: number;
  screenshot_fallbacks: number;
  landing_completed: number;
  relevant_ads: number;
  not_relevant_ads: number;
  analyzed_ads: number;
  top_advertisers: DashboardSummaryItem[];
  top_topics: DashboardSummaryItem[];
}
