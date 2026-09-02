"""Step 11-a 뉴스 아카이브 단위 테스트."""

from datetime import datetime, timezone

import pytest

from trading_engine.external.news import rss

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Bitcoin ETF inflows hit record as Fed signals rate cut</title>
    <link>https://example.com/a/one?utm_source=rss&amp;id=7</link>
    <description>&lt;p&gt;Analysts say &lt;b&gt;BTC&lt;/b&gt; demand rose.&lt;/p&gt;</description>
    <pubDate>Wed, 02 Sep 2026 05:48:21 +0000</pubDate>
  </item>
  <item>
    <title>발행 시각이 없는 기사</title>
    <link>https://example.com/a/two</link>
  </item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Solana network upgrade ships</title>
    <link href="https://example.com/atom/1"/>
    <published>2026-09-02T04:00:00+00:00</published>
    <summary>SOL validators updated.</summary>
  </entry>
</feed>"""


# --- URL 정규화 · 중복 ---------------------------------------------------------


def test_normalize_url_strips_tracking_params_and_trailing_slash():
    a = rss.normalize_url("https://Example.com/Post/?utm_source=rss&utm_medium=x&id=7")
    b = rss.normalize_url("https://example.com/Post?id=7")

    assert a == b == "https://example.com/Post?id=7"


def test_url_hash_matches_for_same_article_shared_with_different_tracking():
    assert rss.url_hash("https://x.com/a?utm_campaign=q") == rss.url_hash("https://x.com/a/")


def test_url_hash_differs_for_different_articles():
    assert rss.url_hash("https://x.com/a") != rss.url_hash("https://x.com/b")


# --- 파싱 ---------------------------------------------------------------------


def test_parse_feed_reads_rss_and_strips_html_from_summary():
    articles = rss.parse_feed(RSS_SAMPLE)

    assert len(articles) == 1  # 발행 시각 없는 항목은 버린다
    article = articles[0]
    assert article.title.startswith("Bitcoin ETF inflows")
    assert article.summary == "Analysts say BTC demand rose."
    assert article.published_at == datetime(2026, 9, 2, 5, 48, 21, tzinfo=timezone.utc)


def test_parse_feed_reads_atom_with_href_links():
    articles = rss.parse_feed(ATOM_SAMPLE)

    assert len(articles) == 1
    assert articles[0].url == "https://example.com/atom/1"
    assert articles[0].symbols == ["SOL"]


def test_parse_feed_drops_items_without_publication_time():
    """시각을 모르면 시점 정확성을 보장할 수 없어 아카이브에 넣지 않는다."""
    titles = [a.title for a in rss.parse_feed(RSS_SAMPLE)]

    assert "발행 시각이 없는 기사" not in titles


def test_parse_feed_survives_malformed_xml():
    assert rss.parse_feed("<rss><channel><item>깨진") == []


def test_parse_published_accepts_rfc822_and_iso8601():
    rfc = rss.parse_published("Wed, 02 Sep 2026 05:48:21 +0000")
    iso = rss.parse_published("2026-09-02T05:48:21+00:00")

    assert rfc == iso
    assert rfc.tzinfo is timezone.utc


def test_parse_published_assumes_utc_when_timezone_missing():
    assert rss.parse_published("2026-09-02T05:48:21").tzinfo is timezone.utc


def test_parse_published_returns_none_for_garbage():
    assert rss.parse_published("어제쯤") is None
    assert rss.parse_published(None) is None


# --- 태깅 ---------------------------------------------------------------------


def test_tag_symbols_matches_names_and_tickers():
    assert rss.tag_symbols("Bitcoin rallies", None) == ["BTC"]
    assert rss.tag_symbols("ETH and XRP gain", None) == ["ETH", "XRP"]


def test_tag_symbols_returns_empty_for_unrelated_news():
    assert rss.tag_symbols("Local bakery opens", "Bread is nice.") == []


def test_tag_topics_covers_macro_axes():
    assert "FED" in rss.tag_topics("FOMC minutes signal caution", None)
    assert "GEOPOLITICS" in rss.tag_topics("Iran conflict escalates", None)
    assert "OIL" in rss.tag_topics("Crude slides as OPEC meets", None)


def test_tagging_reads_both_title_and_summary():
    assert rss.tag_symbols("Market update", "Dogecoin surged overnight.") == ["DOGE"]


# --- 분류기 안전장치 -----------------------------------------------------------


def test_classifier_is_skipped_without_a_configured_model(monkeypatch):
    from trading_engine.external.news import classifier

    monkeypatch.setenv("OPENAI_NEWS_MODEL", "")
    monkeypatch.setenv("OPENAI_MODEL", "REPLACE_WITH_ACTUAL_MODEL_NAME")

    assert classifier._model() is None


def test_classifier_prefers_the_news_specific_model(monkeypatch):
    from trading_engine.external.news import classifier

    monkeypatch.setenv("OPENAI_MODEL", "big-model")
    monkeypatch.setenv("OPENAI_NEWS_MODEL", "small-model")

    assert classifier._model() == "small-model"
    assert classifier.classifier_name() == "openai:small-model"


@pytest.mark.asyncio
async def test_classify_pending_returns_zero_without_api_key(monkeypatch):
    """수집은 분류 없이도 계속돼야 한다."""
    from trading_engine.external.news import classifier

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert await classifier.classify_pending() == 0
