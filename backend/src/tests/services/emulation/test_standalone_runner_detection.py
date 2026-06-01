from __future__ import annotations

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
