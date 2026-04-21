from __future__ import annotations

from app.services.mobile_app.android.youtube.ads import AndroidYouTubeAdInteractor


def test_find_first_matching_node_center_via_adb_sync_matches_resource_id() -> None:
    hierarchy = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <node
    resource-id="com.google.android.youtube:id/modern_action_button"
    text=""
    content-desc=""
    bounds="[100,200][300,260]" />
</hierarchy>
"""

    center = AndroidYouTubeAdInteractor._find_first_matching_node_center_via_adb_sync(
        hierarchy=hierarchy,
        resource_ids=("com.google.android.youtube:id/modern_action_button",),
    )

    assert center == (200, 230)


def test_find_first_matching_node_center_via_adb_sync_matches_text_case_insensitively() -> None:
    hierarchy = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <node
    resource-id=""
    text="Learn more"
    content-desc=""
    bounds="[40,80][240,140]" />
</hierarchy>
"""

    center = AndroidYouTubeAdInteractor._find_first_matching_node_center_via_adb_sync(
        hierarchy=hierarchy,
        texts=("learn more",),
    )

    assert center == (140, 110)


def test_normalize_url_like_accepts_domain_and_rejects_sentence() -> None:
    assert AndroidYouTubeAdInteractor._normalize_url_like("hvoya.kiev.ua") == "hvoya.kiev.ua"
    assert AndroidYouTubeAdInteractor._normalize_url_like("Visit advertiser now") is None
