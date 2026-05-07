from pathlib import Path

from app.services.emulation.standalone_mapper import build_standalone_live_payload


def _map_single_banner(title: str, landing_url: str | None = None) -> dict[str, object]:
    payload = build_standalone_live_payload(
        topic_records=[
            {
                "topic": "gagner de largent en ligne",
                "banners": [
                    {
                        "title": title,
                        "landing_url": landing_url,
                        "screenshot": "banners/banner.png",
                        "landing_screenshot": "banners/banner_landing.png",
                        "captured_at": 1.0,
                    }
                ],
            }
        ],
        run_dir=Path("/tmp/standalone-run"),
        storage_base=Path("/tmp"),
        recorded_at=123.0,
    )
    return payload.watched_ads[0]


def test_banner_mapper_uses_compact_headline_from_structured_xml_text() -> None:
    ad = _map_single_banner(
        "Sponsored - Simulateur Investissement SCPI - Simulation d'investissement - Placement 2026\n"
        "Comparez les SCPI et trouvez la meilleure stratégie. Simulation d'investissement Gratuite.\n"
        "www.scpi-8.com/immobilier/scpi-investir - Visit site",
        landing_url="https://www.scpi-8.com/simulation-investissement/02-immobilier",
    )

    assert ad["headline_text"] == "Simulateur Investissement SCPI"
    assert (
        ad["description_text"]
        == "Comparez les SCPI et trouvez la meilleure stratégie. Simulation d'investissement Gratuite."
    )
    assert ad["advertiser_domain"] == "scpi-8.com"
    assert ad["display_url"] == "scpi-8.com"
    assert ad["cta_text"] == "Visit site"
    assert str(ad["full_text"]).startswith("Sponsored - Simulateur Investissement SCPI")


def test_banner_mapper_falls_back_to_domain_from_xml_when_landing_is_missing() -> None:
    ad = _map_single_banner(
        "Sponsored - Ваш постачальник гелію\n"
        "Замовляйте гелій просто зараз. Вигідна ціна, швидка доставка, гарантія якості.\n"
        "hems-helium.com - Visit store",
        landing_url=None,
    )

    assert ad["headline_text"] == "Ваш постачальник гелію"
    assert ad["advertiser_domain"] == "hems-helium.com"
    assert ad["cta_text"] == "Visit store"


def test_banner_mapper_uses_landing_domain_for_generic_cta_placeholder() -> None:
    ad = _map_single_banner(
        "Visit site banner",
        landing_url="https://www.lp2.mexem.com/start-investing-fr/?utm_source=google_fr",
    )

    assert ad["headline_text"] == "lp2.mexem.com"
    assert ad["advertiser_domain"] == "lp2.mexem.com"
    assert ad["full_text"] == "Visit site banner"
