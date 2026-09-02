"""RSS 수집 · 정규화 · 태깅.

원문 본문은 가져오지 않는다. RSS 가 주는 제목·요약·링크·발행시각까지만 쓴다.
전문 스크래핑은 대부분 약관 위반이고, prompt.md §5 에서 트레이딩뷰 비공식
스크래핑을 거부한 것과 같은 원칙이다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import aiohttp

log = logging.getLogger(__name__)

FETCH_TIMEOUT_SEC = 20.0
# RSS 는 매체 서버를 쓰는 일이라 신원을 밝힌다.
USER_AGENT = "upsignal-news-archiver/1.0 (+https://upsignal.mycafe24.com)"

# 광고 추적 파라미터. 같은 기사를 다른 URL 로 보이게 만들어 중복을 낳는다.
TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "source")

# 심볼 귀속: RSS 에 코인 태그가 없어 제목·요약 문자열로 맞춘다.
# 뉴스 대부분이 BTC·거시라 소형 심볼은 거의 안 잡힌다 — 알려진 한계다.
SYMBOL_PATTERNS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "XRP": ("xrp", "ripple"),
    "SOL": ("solana", "sol"),
    "DOGE": ("dogecoin", "doge"),
}

# 매크로 주제 태깅. Step 11-b 의 매크로 시계열과 이어붙일 축이다.
TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "FED": ("federal reserve", "fed ", "fomc", "powell", "rate cut", "rate hike", "interest rate"),
    "INFLATION": ("inflation", "cpi", "pce"),
    "GEOPOLITICS": ("war", "sanction", "iran", "israel", "ukraine", "conflict", "tariff"),
    "OIL": ("oil price", "crude", "wti", "brent", "opec"),
    "GOLD": ("gold price", "bullion"),
    "BONDS": ("treasury yield", "bond market", "10-year"),
    "REGULATION": ("sec ", "regulator", "lawsuit", "etf approval", "legislation"),
    "ETF": ("etf",),
}


@dataclass(frozen=True)
class Article:
    url: str
    url_hash: str
    title: str
    summary: str | None
    published_at: datetime
    symbols: list[str]
    topics: list[str]


def normalize_url(url: str) -> str:
    """추적 파라미터와 프래그먼트를 떼고 소문자 호스트로 맞춘다."""
    parts = urlsplit(url.strip())
    query = "&".join(
        piece
        for piece in parts.query.split("&")
        if piece and not piece.lower().startswith(TRACKING_PARAM_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def strip_html(text: str | None) -> str | None:
    """RSS description 은 대부분 HTML 조각이다. 태그를 걷어낸다."""
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain or None


def parse_published(value: str | None) -> datetime | None:
    """RFC822(RSS) 와 ISO8601(Atom) 을 모두 받는다. 항상 UTC 로 돌려준다."""
    if not value:
        return None
    for parse in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            parsed = parse(value.strip())
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _match(text: str, patterns: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = f" {text.lower()} "
    return sorted(
        key for key, needles in patterns.items() if any(n in lowered for n in needles)
    )


def tag_symbols(title: str, summary: str | None) -> list[str]:
    return _match(f"{title} {summary or ''}", SYMBOL_PATTERNS)


def tag_topics(title: str, summary: str | None) -> list[str]:
    return _match(f"{title} {summary or ''}", TOPIC_PATTERNS)


def _text(node: Any, *names: str) -> str | None:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
        # Atom 은 링크가 속성에 있다
        if found is not None and found.get("href"):
            return found.get("href")
    return None


def parse_feed(xml_text: str) -> list[Article]:
    """RSS 2.0 과 Atom 을 모두 받는다. 파싱 못 한 항목은 조용히 건너뛴다."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        log.warning("피드 XML 파싱 실패")
        return []

    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry"
    )
    articles: list[Article] = []
    for item in items:
        atom = "{http://www.w3.org/2005/Atom}"
        link = _text(item, "link", f"{atom}link")
        title = _text(item, "title", f"{atom}title")
        if not link or not title:
            continue
        published = parse_published(
            _text(item, "pubDate", f"{atom}published", f"{atom}updated", "date")
        )
        if published is None:
            # 발행 시각을 모르면 시점 정확성을 보장할 수 없어 버린다.
            log.debug("발행 시각 없는 항목 제외: %s", title[:60])
            continue
        summary = strip_html(
            _text(item, "description", f"{atom}summary", f"{atom}content")
        )
        articles.append(
            Article(
                url=link,
                url_hash=url_hash(link),
                title=title[:500],
                summary=summary[:2000] if summary else None,
                published_at=published,
                symbols=tag_symbols(title, summary),
                topics=tag_topics(title, summary),
            )
        )
    return articles


async def fetch_feed(session: aiohttp.ClientSession, feed_url: str) -> list[Article]:
    """한 피드를 받아 파싱한다. 실패하면 빈 리스트 — 다른 피드는 계속 돈다."""
    try:
        async with session.get(feed_url, allow_redirects=True) as resp:
            resp.raise_for_status()
            # 매체마다 charset 선언이 제각각이라 추측에 맡기지 않는다.
            body = await resp.text(errors="replace")
    except Exception:
        log.exception("피드 수신 실패: %s", feed_url)
        return []
    return parse_feed(body)


def make_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SEC),
        headers={"User-Agent": USER_AGENT},
    )
