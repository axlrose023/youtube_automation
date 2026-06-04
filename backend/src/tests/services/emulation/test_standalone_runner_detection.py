from __future__ import annotations

import pytest
from standalone_topic_runner import runner


class _Driver:
    def __init__(self, page_source: str) -> None:
        self.page_source = page_source

    def get_window_size(self) -> dict[str, int]:
        return {"width": 1320, "height": 2868}


FR_RESULTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node package="com.google.android.youtube" class="android.support.v7.widget.RecyclerView"
        resource-id="com.google.android.youtube:id/results" bounds="[0,274][1320,2592]">
    <node package="com.google.android.youtube" class="android.view.ViewGroup"
          content-desc="3 appli gratuites qui rapportent de l'argent, 172 mille vues, Monsieur Astuces, il y a 2 ans - lire le Short"
          clickable="true" bounds="[35,274][649,1065]" />
    <node package="com.google.android.youtube" class="android.view.ViewGroup"
          content-desc="8 Sites Internet pour Gagner de l'Argent au Togo en 2025 - 11 minutes et 26 secondes - Acceder a la chaine BLACKSIOO - BLACKSIOO - 1,6 mille vues - il y a 10 mois - Regarder la video"
          clickable="true" bounds="[0,1155][1320,2105]" />
    <node package="com.google.android.youtube" class="android.view.ViewGroup"
          content-desc="Sponsorise - Le Calculateur Boursier - Meilleure Plateforme 2026
90 pourcent des gens regardent au mauvais endroit
www.allezur.fr/ - Visitez le site"
          clickable="true" bounds="[0,2197][1320,2592]" />
  </node>
</hierarchy>
"""


def test_french_results_detect_video_short_and_sponsored_banner() -> None:
    driver = _Driver(FR_RESULTS_XML)

    videos = runner.collect_video_tiles(driver)
    shorts = runner.collect_short_tiles(driver)
    banner = runner.find_top_sponsored_banner(driver)

    assert len(videos) == 1
    assert "Regarder la video" in videos[0].title
    assert len(shorts) == 1
    assert "lire le Short" in shorts[0].title
    assert banner is not None
    assert "Calculateur Boursier" in banner.title
    assert banner.tap_bounds == (0, 2197, 1320, 2592)


def test_french_pixel_launcher_anr_closes_app() -> None:
    page_source = """<hierarchy>
      <node package="android" text="Lanceur d'applications Pixel ne répond pas." bounds="[85,1300][1235,1400]" />
      <node package="android" text="Fermer l'application" bounds="[280,1500][780,1600]" />
      <node package="android" text="Attendre" bounds="[280,1650][560,1750]" />
    </hierarchy>"""

    action = runner._system_anr_action(page_source)

    assert action is not None
    assert action[0] == "close_app"
    assert action[1] == (280, 1500, 780, 1600)


def test_french_cloudflare_landing_is_rejected() -> None:
    page_source = """<hierarchy>
      <node package="com.android.chrome" text="financeactus.com" />
      <node package="com.android.chrome" text="Vérification de sécurité en cours" />
      <node package="com.android.chrome" text="Ce site utilise un service de sécurité pour se protéger contre les bots malveillants." />
      <node package="com.android.chrome" text="Vérifiez que vous êtes humain." />
    </hierarchy>"""

    assert runner._landing_source_has_error(page_source) is True


@pytest.mark.asyncio
async def test_rejected_large_landing_screenshot_is_removed(tmp_path, monkeypatch) -> None:
    page_source = """<hierarchy>
      <node package="com.android.chrome" text="Traduire la page ?" bounds="[80,200][860,340]" />
      <node package="com.android.chrome" text="Traduire" bounds="[710,240][850,315]" />
    </hierarchy>"""
    driver = _Driver(page_source)
    screenshot_path = tmp_path / "ad_landing.png"

    async def _no_dialog(*args, **kwargs) -> bool:
        return False

    def _write_large_screenshot(serial, path) -> bool:
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"0" * 130_000))
        return True

    monkeypatch.setattr(runner, "dismiss_browser_permission_prompt_if_present", _no_dialog)
    monkeypatch.setattr(runner, "dismiss_browser_interstitial_if_present", _no_dialog)
    monkeypatch.setattr(runner, "accept_landing_cookie_banner_if_present", _no_dialog)
    monkeypatch.setattr(runner, "read_landing_url", lambda serial, youtube_pkg: "https://example.test")
    monkeypatch.setattr(runner, "adb_screencap", _write_large_screenshot)

    taken = await runner.capture_settled_landing_screenshot(
        driver=driver,
        serial="emulator-5554",
        path=screenshot_path,
        youtube_pkg="com.google.android.youtube",
        timeout=0,
        min_bytes=120_000,
    )

    assert taken is False
    assert not screenshot_path.exists()
