"""
analysis.py - Claude API를 이용한 가격 변동 이유 한국어 분석 생성
"""

from __future__ import annotations

import os
import base64
import anthropic
from datetime import datetime
from .stock_data import get_stock_news, get_daily_change, get_exchange_rate, get_market_news
from .database import save_report, save_snapshot


def _build_analysis_prompt(portfolio_items: list[dict], date: str) -> str:
    """Claude API에 전달할 프롬프트 구성"""
    lines = [f"오늘 날짜: {date}\n"]
    lines.append("=== 포트폴리오 변동 현황 ===\n")

    for item in portfolio_items:
        ticker = item["ticker"]
        name = item.get("name", ticker)
        currency = item.get("currency", "USD")
        daily_pct = item.get("daily_change_pct", 0)
        current_price = item.get("current_price", 0)
        current_rate = item.get("current_rate", 1)
        purchase_rate = item.get("purchase_rate", 1)
        fx_pct = item.get("fx_gain_pct", 0)

        symbol_map = {"USD": "$", "JPY": "¥", "KRW": "₩"}
        sym = symbol_map.get(currency, "")

        lines.append(f"[{name} ({ticker})]")
        lines.append(f"  현재가: {sym}{current_price:,.2f}")
        lines.append(f"  당일 등락: {daily_pct:+.2f}%")
        if currency != "KRW":
            lines.append(f"  환율: 매수 {purchase_rate:,.1f}원 → 현재 {current_rate:,.1f}원 ({fx_pct:+.2f}%)")

        # 관련 뉴스 추가
        news_list = get_stock_news(ticker, max_items=3)
        if news_list:
            lines.append("  관련 뉴스:")
            for news in news_list:
                title = news.get("title", "")
                summary = news.get("summary", "")
                if title:
                    lines.append(f"    - {title}")
                    if summary:
                        lines.append(f"      {summary[:150]}")
        lines.append("")

    # 환율 현황
    usd_rate = get_exchange_rate("USD")
    jpy_rate = get_exchange_rate("JPY")
    lines.append("=== 환율 현황 ===")
    lines.append(f"  USD/KRW: {usd_rate:,.1f}원")
    lines.append(f"  JPY/KRW: {jpy_rate:.2f}원\n")

    # 글로벌 경제 뉴스 (RSS)
    market_news = get_market_news(max_per_source=3)
    if market_news:
        lines.append("=== 글로벌 경제 뉴스 ===")
        current_source = ""
        for n in market_news:
            if n["source"] != current_source:
                current_source = n["source"]
                lines.append(f"  [{current_source}]")
            lines.append(f"    - {n['title']}")
            if n.get("summary"):
                lines.append(f"      {n['summary'][:120]}")
        lines.append("")

    return "\n".join(lines)


def generate_daily_report(portfolio_items: list[dict], date: str | None = None) -> str:
    """
    Claude API로 일일 한국어 분석 리포트 생성.
    API 키가 없으면 기본 텍스트 반환.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    context = _build_analysis_prompt(portfolio_items, date)

    if not api_key or not portfolio_items:
        # API 키 없을 때 기본 요약 텍스트 생성
        return _generate_simple_report(portfolio_items, date)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"""당신은 주식 투자 분석가입니다. 아래 포트폴리오 현황, 종목별 뉴스, 글로벌 경제 뉴스를 종합해
오늘의 주가 변동 이유를 한국어로 간결하게 설명해주세요.

각 종목별로:
1. 오늘 왜 올랐는지/내렸는지 이유 (종목 뉴스 + 글로벌 경제 뉴스 연계)
2. 환율 영향 (해당 시)
3. 투자자가 주목해야 할 포인트

마지막에:
- 오늘 글로벌 시장에서 주목할 거시경제 이슈 1~2줄 요약

형식: 이모지를 활용해 읽기 쉽게, 전체 700자 이내

---
{context}
""",
                }
            ],
        )
        return message.content[0].text
    except Exception as e:
        return _generate_simple_report(portfolio_items, date) + f"\n\n(AI 분석 오류: {e})"


def _generate_simple_report(portfolio_items: list[dict], date: str) -> str:
    """Claude API 없이 기본 텍스트 요약 생성"""
    if not portfolio_items:
        return f"[{date}] 보유 종목이 없습니다."

    lines = [f"📊 [{date}] 포트폴리오 변동 요약\n"]
    for item in portfolio_items:
        name = item.get("name", item["ticker"])
        ticker = item["ticker"]
        daily_pct = item.get("daily_change_pct", 0)
        total_pct = item.get("total_gain_pct", 0)
        arrow = "▲" if daily_pct >= 0 else "▼"
        lines.append(f"{'📈' if daily_pct >= 0 else '📉'} {name} ({ticker}): {arrow}{abs(daily_pct):.2f}% (총 수익률 {total_pct:+.2f}%)")
    return "\n".join(lines)


def parse_purchase_screenshot(image_bytes: bytes, media_type: str = "image/png") -> list:
    """
    매수 내역 스크린샷을 Claude Vision으로 파싱.
    반환: [
      {
        "ticker": "7203.T",
        "name": "Toyota",
        "quantity": 100,
        "purchase_price": 2850.0,
        "purchase_currency": "JPY",
        "purchase_date": "2024-03-15",
        "account_type": "NISA (성장투자)",
        "notes": "",
        "_raw": "..."   # 파싱 실패 시 모델 원본 응답 (디버그용)
      }, ...
    ]
    실패 시 {"_error": "...", "_raw": "..."} 포함 리스트 반환.
    """
    import json, re

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """이 이미지는 주식 매수 내역 화면입니다 (증권사 앱, 웹, 거래 확인서 등).
화면에 보이는 모든 주식 매수 정보를 추출해주세요.

**중요**: 아래 JSON 배열 형식으로만 답하세요. 설명이나 다른 텍스트는 절대 포함하지 마세요.

추출할 항목:
- ticker: 종목 코드/티커. 일본 주식이면 숫자+.T (예: 7203.T), 미국이면 알파벳 (예: AAPL), 한국이면 숫자+.KS (예: 005930.KS). 코드가 안 보이면 빈 문자열
- name: 종목명 (화면에 표시된 그대로)
- quantity: 매수 수량 (숫자만, 단위 제외)
- purchase_price: 1주당 체결가/매수가 (숫자만, 통화 기호 제외)
- purchase_currency: 통화. 엔화→"JPY", 달러→"USD", 원화→"KRW"
- purchase_date: **매우 중요** — 화면에서 매수일/체결일/約定日/거래일을 찾아 "YYYY-MM-DD" 형식으로 반환.
  변환 규칙:
  · "2025/08/26" → "2025-08-26"
  · "2025年8月26日" → "2025-08-26"
  · "R7/08/26" 또는 "令和7年8月26日" (令和7년=2025년) → "2025-08-26"
  · 연도가 불확실하거나 화면에 없으면 → 연도 자리에 "XXXX" 사용: 예) "XXXX-08-26"
    (절대 현재 연도를 임의로 추측하지 말 것 — XXXX로 남겨야 앱에서 주가 비교로 연도를 정확히 결정함)
  · 날짜를 전혀 찾을 수 없는 경우에만 ""
- account_type: 계좌 종류. NISA성장→"NISA (성장투자)", NISA적립→"NISA (적립투자)", 特定(源泉徴収あり)→"특정계좌 (원천징수)", 特定(源泉徴収なし)→"특정계좌 (확정신고)", 그 외→"일반계좌"
- broker: 증권사 자동 감지.
  · 화면 배경이 검정/다크 계열이거나 일본어 UI → "라쿠텐증권 (JP)"
  · 화면 배경이 흰색/밝은 계열이고 한국어 UI → "토스증권 (KR)"
  · 그 외 또는 불명확 → ""
- notes: 화면에서 추가로 참고할 만한 정보 (없으면 ""). 날짜는 여기 넣지 말고 반드시 purchase_date에 넣을 것.

[
  {
    "ticker": "",
    "name": "",
    "quantity": 0,
    "purchase_price": 0.0,
    "purchase_currency": "JPY",
    "purchase_date": "",
    "account_type": "일반계좌",
    "broker": "",
    "notes": ""
  }
]"""

    raw_text = ""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw_text = msg.content[0].text.strip()

        # JSON 추출: ```json ... ``` 또는 ``` ... ``` 또는 순수 JSON
        json_str = raw_text
        # 코드블록 제거
        code_block = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw_text)
        if code_block:
            json_str = code_block.group(1).strip()
        else:
            # 배열 [ ... ] 또는 객체 { ... } 직접 추출
            arr_match = re.search(r"(\[[\s\S]+\])", raw_text)
            if arr_match:
                json_str = arr_match.group(1)

        result = json.loads(json_str)

        # 결과가 dict 단일 항목이면 리스트로 감싸기
        if isinstance(result, dict):
            result = [result]

        return result

    except json.JSONDecodeError as e:
        return [{"_error": f"JSON 파싱 실패: {e}", "_raw": raw_text}]
    except Exception as e:
        return [{"_error": str(e), "_raw": raw_text}]


def parse_transaction_screenshot(image_bytes: bytes, media_type: str = "image/png") -> list:
    """
    매수·매도가 섞인 스크린샷을 Claude Vision으로 파싱.
    한 스크린샷에 매수(買付/買/매수)와 매도(売却/売付/매도)가 함께 있어도 각각 구분해서 감지.

    반환: [
      {
        "transaction_type": "buy" | "sell",
        "ticker": "7203.T",
        "name": "Toyota",
        "quantity": 30,
        "price": 2900.0,              # 단가 (매수가 또는 매도가)
        "currency": "JPY",
        "date": "2025-06-15",         # 체결일 (매수일 또는 매도일)
        "broker": "라쿠텐증권 (JP)",
        "account_type": "일반계좌",
        "notes": "",
      }, ...
    ]
    실패 시 [{"_error": "...", "_raw": "..."}] 반환.
    """
    import json, re

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """이 이미지는 주식 **매매 내역** 화면입니다 (증권사 앱, 웹, 거래 확인서 등).
화면에 **매수와 매도가 함께** 있을 수 있습니다. **모든 거래를 각각 구분해서** 배열로 추출해주세요.

**중요**: 아래 JSON 배열 형식으로만 답하세요. 설명·다른 텍스트는 절대 포함하지 마세요.

각 거래마다 추출할 항목:

- transaction_type: **거래 종류** — "buy" 또는 "sell"
  · 매수 표시: 買付 / 買 / 매수 / 매입 / Buy / BUY / 購入 → "buy"
  · 매도 표시: 売却 / 売付 / 売 / 매도 / 매각 / 처분 / Sell / SELL → "sell"
  · 빨간색·붉은색 숫자 = 매수 (일본 증권사 관행), 파란색·청색 숫자 = 매도 (일본 관행)
  · 한국: 파란색·적색이 반대일 수 있음. 글자 표시가 우선
  · 확실치 않으면 "buy"

- ticker: 종목 코드.
  · 미국 주식: 영어 티커 (예: AAPL, TSLA, PLTR, NVDA, AVGO)
  · 일본 주식: 숫자+.T (예: 7203.T)
  · 한국 주식: 숫자+.KS (예: 005930.KS)
  · **일본 투자신탁(投資信託/ファンド)**: 펀드 코드를 사용하세요. 예시:
    eMAXIS Slim 全世界株式(オール・カントリー) → 0331418A
    eMAXIS Slim 米国株式(S&P500) → 03311187
    iFreeNEXT FANG+インデックス → 04315177
    楽天・全米株式インデックス・ファンド → 9I312179
    楽天・S&P500インデックス・ファンド → 9I311215
    楽天・資産づくりファンド(がっちりコース) → 9I315216
    SBI・V・S&P500 → 89311199
    펀드 코드를 모르면 "" (앱에서 사용자가 검색 가능)
  · **한국어로 표시된 종목명은 반드시 영어 티커로 변환!** 예시:
    팔란티어 → PLTR, 테슬라 → TSLA, 엔비디아 → NVDA, 애플 → AAPL,
    브로드컴 → AVGO, 마이크로소프트 → MSFT, 아마존 → AMZN, 메타 → META,
    알파벳/구글 → GOOGL, 넷플릭스 → NFLX, AMD → AMD, 인텔 → INTC,
    코인베이스 → COIN, 스노우플레이크 → SNOW, 크라우드스트라이크 → CRWD,
    유니티 → U, 로블록스 → RBLX, 쇼피파이 → SHOP, 우버 → UBER
  · 위 목록에 없어도 한국어 종목명에서 영어 티커를 최대한 추론하세요
  · 정말 모르면 ""
- name: 종목명. **영어로 변환할 수 없는 종목(일본 투자신탁 등)은 원본 일본어 이름 그대로** 사용.
  영어 변환 가능한 종목만 영어로.
- quantity: 수량 (숫자만, 단위 제외). 투자신탁처럼 구좌수(口数)가 보이면 그 값. 없으면 0
- price: **1주당 단가 / 체결가 / 기준가액(基準価額)** (숫자만, 통화 기호 제외).
  · 투자신탁은 기준가액이 있으면 입력, 없으면 0
  · 총 거래금액만 보이면 → price에 총 금액을 넣고 notes에 "총액" 표시
- currency: 통화. 엔화→"JPY", 달러→"USD", 원화→"KRW"
- date: 체결일을 "YYYY-MM-DD" 형식으로.
  · "2025/08/26" → "2025-08-26"
  · "2025年8月26日" → "2025-08-26"
  · "R7/08/26" / "令和7年8月26日" → "2025-08-26"
  · 연도가 불확실하면 "XXXX-MM-DD" 사용 (절대 현재 연도를 임의로 추측하지 말 것)
  · 날짜를 전혀 찾을 수 없으면 ""
- account_type: 계좌. NISA성장→"NISA (성장투자)", NISA적립→"NISA (적립투자)",
  特定(源泉徴収あり)→"특정계좌 (원천징수)", 特定(源泉徴収なし)→"특정계좌 (확정신고)", 그 외→"일반계좌"
- broker: 증권사 자동 감지.
  · 화면 배경이 검정/다크 계열이거나 일본어 UI → "라쿠텐증권 (JP)"
  · 화면 배경이 흰색/밝은 계열이고 한국어 UI → "토스증권 (KR)"
  · 그 외 또는 불명확 → ""
- notes: 화면에서 참고할 정보 (없으면 ""). 투자금액(積立金額 등)이 있으면 반드시 여기에 기록 (예: "10,000円"). 날짜는 여기 넣지 말고 반드시 date에 넣을 것

[
  {
    "transaction_type": "buy",
    "ticker": "",
    "name": "",
    "quantity": 0,
    "price": 0.0,
    "currency": "JPY",
    "date": "",
    "account_type": "일반계좌",
    "broker": "",
    "notes": ""
  }
]"""

    raw_text = ""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw_text = msg.content[0].text.strip()

        json_str = raw_text
        code_block = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw_text)
        if code_block:
            json_str = code_block.group(1).strip()
        else:
            arr_match = re.search(r"(\[[\s\S]+\])", raw_text)
            if arr_match:
                json_str = arr_match.group(1)

        # 잘린 JSON 복구: max_tokens 초과로 응답이 잘린 경우
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError:
            last_brace = json_str.rfind("}")
            if last_brace > 0:
                truncated = json_str[:last_brace + 1].rstrip().rstrip(",") + "\n]"
                result = json.loads(truncated)
            else:
                raise

        if isinstance(result, dict):
            result = [result]

        # transaction_type 누락 시 "buy" 기본값
        for item in result:
            if isinstance(item, dict) and not item.get("_error"):
                if "transaction_type" not in item:
                    item["transaction_type"] = "buy"
        return result

    except json.JSONDecodeError as e:
        return [{"_error": f"JSON 파싱 실패: {e}", "_raw": raw_text}]
    except Exception as e:
        return [{"_error": str(e), "_raw": raw_text}]


def parse_sell_screenshot(image_bytes: bytes, media_type: str = "image/png") -> list:
    """
    매도 내역 스크린샷을 Claude Vision으로 파싱.
    반환: [
      {
        "ticker": "7203.T",
        "name": "Toyota",
        "quantity": 30,
        "sell_price": 3100.0,
        "sell_currency": "JPY",
        "sell_date": "2025-06-15",
        "broker": "라쿠텐증권 (JP)",
        "account_type": "NISA (성장투자)",
        "notes": "",
      }, ...
    ]
    실패 시 [{"_error": "...", "_raw": "..."}] 반환.
    """
    import json, re

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """이 이미지는 주식 **매도** 내역 화면입니다 (증권사 앱, 웹, 약정 화면, 체결 확인서 등).
화면에 보이는 모든 매도 정보를 추출해주세요. 여러 건이면 모두 배열로.

**중요**: 아래 JSON 배열 형식으로만 답하세요. 설명·다른 텍스트는 절대 포함하지 마세요.

추출할 항목:
- ticker: 종목 코드. 일본 주식이면 숫자+.T (예: 7203.T), 미국이면 알파벳 (예: AAPL), 한국이면 숫자+.KS (예: 005930.KS). 코드가 없으면 빈 문자열
- name: 종목명 (화면에 표시된 그대로)
- quantity: **매도 수량** (숫자만, 단위 제외)
- sell_price: **1주당 매도 단가 / 체결가** (숫자만, 통화 기호 제외). 총 매도금액이 아니라 단가임에 주의
- sell_currency: 통화. 엔화→"JPY", 달러→"USD", 원화→"KRW"
- sell_date: **매도 체결일 / 約定日 / 売却日**을 "YYYY-MM-DD" 형식으로 반환.
  · "2025/08/26" → "2025-08-26"
  · "2025年8月26日" → "2025-08-26"
  · "R7/08/26" / "令和7年8月26日" → "2025-08-26"
  · 연도가 불확실하면 "XXXX-MM-DD" 사용 (절대 현재 연도를 임의로 추측하지 말 것)
  · 날짜를 전혀 찾을 수 없으면 ""
- account_type: 계좌. NISA성장→"NISA (성장투자)", NISA적립→"NISA (적립투자)",
  特定(源泉徴収あり)→"특정계좌 (원천징수)", 特定(源泉徴収なし)→"특정계좌 (확정신고)", 그 외→"일반계좌"
- broker: 증권사 자동 감지.
  · 화면 배경이 검정/다크 계열이거나 일본어 UI → "라쿠텐증권 (JP)"
  · 화면 배경이 흰색/밝은 계열이고 한국어 UI → "토스증권 (KR)"
  · 그 외 또는 불명확 → ""
- notes: 화면에서 추가로 참고할 만한 정보 (없으면 ""). 날짜는 여기 넣지 말고 반드시 sell_date에 넣을 것.

[
  {
    "ticker": "",
    "name": "",
    "quantity": 0,
    "sell_price": 0.0,
    "sell_currency": "JPY",
    "sell_date": "",
    "account_type": "일반계좌",
    "broker": "",
    "notes": ""
  }
]"""

    raw_text = ""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw_text = msg.content[0].text.strip()

        json_str = raw_text
        code_block = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw_text)
        if code_block:
            json_str = code_block.group(1).strip()
        else:
            arr_match = re.search(r"(\[[\s\S]+\])", raw_text)
            if arr_match:
                json_str = arr_match.group(1)

        result = json.loads(json_str)
        if isinstance(result, dict):
            result = [result]
        return result

    except json.JSONDecodeError as e:
        return [{"_error": f"JSON 파싱 실패: {e}", "_raw": raw_text}]
    except Exception as e:
        return [{"_error": str(e), "_raw": raw_text}]


def translate_news_batch(news_items: list) -> list:
    """
    뉴스 제목·요약을 한국어로 일괄 번역 (Claude API).
    각 item에 title_ko, summary_ko 필드를 추가해 반환.
    API 키 없거나 번역 실패 시 원본 반환.

    news_items: [{"title": "...", "summary": "..."}, ...]
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or not news_items:
        return news_items

    # 번역 입력 구성 — [숫자T] 제목, [숫자S] 요약 형태
    input_lines = []
    for i, item in enumerate(news_items):
        t = (item.get("title") or "").strip()
        s = (item.get("summary") or "").strip()[:200]
        if t:
            input_lines.append(f"[{i}T] {t}")
        if s:
            input_lines.append(f"[{i}S] {s}")

    if not input_lines:
        return news_items

    prompt = (
        "아래 주식 뉴스 제목([숫자T])과 요약([숫자S])을 자연스러운 한국어로 번역하세요.\n\n"
        "규칙:\n"
        "- 회사명 (Apple, Toyota 등), 티커 (AAPL, NVDA 등), "
        "금융 용어 (AI, ETF, IPO, Fed, S&P500, GDP 등)는 영어 그대로 유지\n"
        "- 이해하기 쉬운 자연스러운 한국어로 번역\n"
        "- 번역 결과만 같은 태그 형식으로 반환 (설명·부연 없이)\n\n"
        + "\n".join(input_lines)
    )

    try:
        import re
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        response = msg.content[0].text

        result = [item.copy() for item in news_items]
        for line in response.strip().splitlines():
            line = line.strip()
            m_t = re.match(r"\[(\d+)T\]\s*(.+)", line)
            m_s = re.match(r"\[(\d+)S\]\s*(.+)", line)
            if m_t:
                idx = int(m_t.group(1))
                if 0 <= idx < len(result):
                    result[idx]["title_ko"] = m_t.group(2).strip()
            elif m_s:
                idx = int(m_s.group(1))
                if 0 <= idx < len(result):
                    result[idx]["summary_ko"] = m_s.group(2).strip()
        return result
    except Exception:
        return news_items


def run_daily_analysis(stocks: list[dict], portfolio_items: list[dict]) -> str:
    """
    일일 분석 실행 + DB 저장.
    stocks: portfolio 테이블 원본 리스트
    portfolio_items: calculate_profit_loss가 적용된 리스트
    """
    date = datetime.now().strftime("%Y-%m-%d")

    # 스냅샷 저장
    for item in portfolio_items:
        save_snapshot(
            date=date,
            ticker=item["ticker"],
            close_price=item["current_price"],
            current_exchange_rate=item["current_rate"],
            value_krw=item["current_value_krw"],
            daily_change_pct=item["daily_change_pct"],
        )

    # 리포트 생성 및 저장
    report_text = generate_daily_report(portfolio_items, date)
    save_report(date, report_text)

    return report_text
