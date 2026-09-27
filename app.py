import os
import re
import html
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=KR"
NAVER_NEWS_API = "https://openapi.naver.com/v1/search/news.json"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

RISK_KEYWORDS = {
    "정치": ["대통령", "국회", "정당", "선거", "후보", "장관", "의원", "탄핵", "정부"],
    "사건사고": ["사망", "살인", "폭행", "사고", "체포", "구속", "화재", "참사"],
    "금융/법률": ["세금", "증여세", "상속세", "대출", "금리", "주식", "코인", "법원", "판결"],
    "건강": ["질병", "암", "약", "치료", "부작용", "감염", "백신"],
}


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def traffic_to_number(value: str) -> int:
    if not value:
        return 0
    s = value.upper().replace(",", "").replace("+", "").strip()
    m = re.search(r"([\d.]+)\s*([KMB]?)", s)
    if not m:
        return 0
    num = float(m.group(1))
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2)]
    return int(num * mult)


def detect_risk(keyword: str) -> str:
    hits = []
    for category, words in RISK_KEYWORDS.items():
        if any(word in keyword for word in words):
            hits.append(category)
    return ", ".join(hits) if hits else "일반"


def calculate_score(traffic: int, published_at: datetime | None) -> float:
    traffic_score = min(60.0, 12.0 * math.log10(max(traffic, 1)))
    if not published_at:
        freshness = 20.0
    else:
        now = datetime.now(timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        hours = max((now - published_at).total_seconds() / 3600, 0)
        freshness = max(0.0, 40.0 - hours * 2.0)
    return round(min(100.0, traffic_score + freshness), 1)


@st.cache_data(ttl=600)
def fetch_google_trends() -> list[dict[str, Any]]:
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(GOOGLE_TRENDS_RSS, headers=headers, timeout=15)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    rows = []
    for entry in feed.entries:
        traffic = getattr(entry, "ht_approx_traffic", "")
        pub_raw = getattr(entry, "published", "") or getattr(entry, "pubDate", "")
        try:
            published_at = parsedate_to_datetime(pub_raw) if pub_raw else None
        except Exception:
            published_at = None

        keyword = strip_html(entry.get("title", ""))
        traffic_num = traffic_to_number(traffic)
        rows.append(
            {
                "keyword": keyword,
                "traffic": traffic,
                "traffic_num": traffic_num,
                "published_at": published_at,
                "risk": detect_risk(keyword),
                "score": calculate_score(traffic_num, published_at),
            }
        )

    rows.sort(key=lambda x: (x["score"], x["traffic_num"]), reverse=True)
    return rows


@st.cache_data(ttl=900)
def search_naver_news(query: str, display: int = 10) -> list[dict[str, str]]:
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다.")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": min(display, 100), "sort": "date"}
    response = requests.get(NAVER_NEWS_API, headers=headers, params=params, timeout=15)
    response.raise_for_status()

    items = []
    for item in response.json().get("items", []):
        items.append(
            {
                "title": strip_html(item.get("title", "")),
                "description": strip_html(item.get("description", "")),
                "pubDate": item.get("pubDate", ""),
                "originallink": item.get("originallink", "") or item.get("link", ""),
                "link": item.get("link", ""),
            }
        )
    return items


def build_source_pack(keyword: str, news_items: list[dict[str, str]]) -> str:
    chunks = []
    for i, item in enumerate(news_items, start=1):
        chunks.append(
            f"[{i}] 제목: {item['title']}\n"
            f"발행: {item['pubDate']}\n"
            f"요약: {item['description']}\n"
            f"원문: {item['originallink']}"
        )
    return f"키워드: {keyword}\n\n" + "\n\n".join(chunks)


def generate_blog_post(keyword: str, news_items: list[dict[str, str]], model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")

    client = OpenAI(api_key=api_key)
    sources = build_source_pack(keyword, news_items)

    prompt = f"""
너는 한국어 정보형 블로그 편집자다.
아래 자료만 근거로 네이버 블로그에 붙여넣을 수 있는 완성 초안을 작성한다.

[핵심 원칙]
- 확인되지 않은 내용은 사실처럼 단정하지 않는다.
- 여러 기사에 공통으로 확인되는 사실을 우선한다.
- 기사 문장을 길게 복사하지 말고 전부 새 문장으로 요약한다.
- 루머, 선정적 표현, 과도한 클릭 유도 표현을 쓰지 않는다.
- 최신 이슈라서 사실관계가 바뀔 수 있으면 그 점을 명시한다.
- 정치 관련 키워드는 특정 인물/정당을 지지·비난하지 말고 사실 중심으로 정리한다.
- 금융/법률/건강 주제는 일반 정보와 최신 사실 전달에 집중하고 개인별 결론을 대신 내리지 않는다.
- 검색자가 '왜 지금 이 키워드가 뜨는지'를 첫 3문단 안에서 이해하게 한다.
- SEO를 위해 키워드를 억지로 반복하지 않는다.

[출력 형식]
제목: 한 줄

요약: 2~3문장

본문:
소제목을 3~5개 사용한 자연스러운 한국어 글, 약 1,200~1,800자

핵심 포인트:
- 3~5개

FAQ:
Q/A 3개

참고한 자료:
- 기사 제목 — 원문 URL

해시태그:
#태그 5~8개

[수집 자료]
{sources}
""".strip()

    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text.strip()


st.set_page_config(page_title="Trend Blog Bot", page_icon="🔥", layout="wide")
st.title("🔥 Trend Blog Bot")
st.caption("Google Trends 급상승 → 네이버 최신 뉴스 수집 → 블로그 초안 생성")

with st.sidebar:
    st.subheader("설정")
    model = st.text_input("OpenAI 모델", value=DEFAULT_MODEL)
    news_count = st.slider("키워드당 뉴스 수", 3, 20, 10)
    show_risky = st.checkbox("검수 필요 키워드도 표시", value=True)
    st.caption("API 키는 .env에 저장하세요. 화면에 입력하지 않습니다.")

try:
    trends = fetch_google_trends()
except Exception as e:
    st.error(f"Google Trends 수집 실패: {e}")
    st.stop()

visible = trends if show_risky else [r for r in trends if r["risk"] == "일반"]

df = pd.DataFrame(visible)
if not df.empty:
    df["published_at"] = df["published_at"].apply(
        lambda x: x.astimezone().strftime("%m-%d %H:%M") if x is not None else "-"
    )
    st.dataframe(
        df[["keyword", "traffic", "score", "risk", "published_at"]].rename(
            columns={
                "keyword": "키워드",
                "traffic": "검색량",
                "score": "추천점수",
                "risk": "검수분류",
                "published_at": "감지시각",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("표시할 트렌드가 없습니다.")
    st.stop()

keywords = [r["keyword"] for r in visible]
selected = st.selectbox("초안을 만들 키워드", keywords)
selected_row = next(r for r in visible if r["keyword"] == selected)

c1, c2, c3 = st.columns(3)
c1.metric("검색량", selected_row["traffic"] or "-")
c2.metric("추천점수", selected_row["score"])
c3.metric("검수분류", selected_row["risk"])

if st.button("① 관련 뉴스 수집", type="primary"):
    try:
        st.session_state["news"] = search_naver_news(selected, news_count)
        st.session_state["news_keyword"] = selected
    except Exception as e:
        st.error(str(e))

news = st.session_state.get("news", [])
news_keyword = st.session_state.get("news_keyword")

if news and news_keyword == selected:
    st.subheader("관련 뉴스")
    for i, item in enumerate(news, start=1):
        st.markdown(f"**{i}. {item['title']}**")
        st.caption(item["pubDate"])
        st.write(item["description"])
        if item["originallink"]:
            st.link_button("원문 보기", item["originallink"])

    if st.button("② 블로그 초안 생성"):
        try:
            with st.spinner("수집 자료를 바탕으로 초안을 작성하고 있습니다..."):
                article = generate_blog_post(selected, news, model)
            st.session_state["article"] = article
            st.session_state["article_keyword"] = selected
        except Exception as e:
            st.error(str(e))

article = st.session_state.get("article")
article_keyword = st.session_state.get("article_keyword")
if article and article_keyword == selected:
    st.subheader("블로그 초안")
    st.text_area("네이버 블로그에 복사해서 사용", article, height=700)
    st.download_button(
        "TXT로 저장",
        data=article.encode("utf-8"),
        file_name=f"{selected}_blog.txt",
        mime="text/plain",
    )
