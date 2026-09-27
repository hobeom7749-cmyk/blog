# Trend Blog Bot

Google Trends 대한민국 급상승 검색어를 가져오고, 선택한 키워드의 네이버 최신 뉴스를 수집한 뒤 OpenAI API로 네이버 블로그용 초안을 만듭니다.

## 설치

```bash
pip install -r requirements.txt
```

`.env.example`을 `.env`로 복사하고 키를 입력합니다.

```bash
OPENAI_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```

## 실행

```bash
streamlit run app.py
```

## 동작 순서

1. Google Trends RSS에서 대한민국 급상승 키워드 수집
2. 검색량 + 감지 시각으로 단순 추천점수 계산
3. 선택 키워드로 네이버 뉴스 Search API 호출
4. 최신 뉴스 제목/요약/링크를 OpenAI API에 전달
5. 블로그 초안 생성
6. 화면에서 검수 후 네이버 블로그에 직접 붙여넣기

## 주의

- 생성 결과는 반드시 원문 링크와 함께 최종 검수하세요.
- 정치, 사건사고, 금융/법률, 건강 키워드는 검수분류가 붙습니다.
- Google Trends RSS는 공식 장기 안정성을 보장하는 개발자 API가 아니므로 구조 변경 시 수집부 수정이 필요할 수 있습니다.
