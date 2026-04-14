"""
stock_data.py - yfinance를 이용한 주식 가격 및 환율 조회, 수익 계산
"""

import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from functools import lru_cache


# ── 환율 심볼 매핑 ─────────────────────────────────────────────
CURRENCY_SYMBOLS = {
    "USD": "USDKRW=X",
    "JPY": "JPYKRW=X",
    "KRW": None,  # 원화는 변환 불필요
}

CURRENCY_LABELS = {
    "USD": "달러",
    "JPY": "엔화",
    "KRW": "원화",
}

MARKET_SUFFIX = {
    "KS": "한국 (KRX)",
    "T":  "일본 (TYO)",
    "":   "미국 (NYSE/NASDAQ)",
}


def get_market(ticker: str) -> str:
    """티커에서 시장 구분"""
    if ticker.endswith(".KS"):
        return "KRW"
    elif ticker.endswith(".T"):
        return "JPY"
    else:
        return "USD"


def get_stock_info(ticker: str) -> dict:
    """종목 기본 정보 조회 (종목명, 현재가, 통화)"""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        currency = info.get("currency", get_market(ticker))
        name = info.get("longName") or info.get("shortName") or ticker
        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
        return {
            "ticker": ticker,
            "name": name,
            "price": float(price),
            "currency": currency,
            "market": get_market(ticker),
        }
    except Exception as e:
        return {"ticker": ticker, "name": ticker, "price": 0.0,
                "currency": get_market(ticker), "market": get_market(ticker), "error": str(e)}


def get_current_price(ticker: str) -> float:
    """현재 주가 반환 (실패 시 0.0)"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if hist.empty:
            return 0.0
        return float(hist["Close"].iloc[-1])
    except Exception:
        return 0.0


def get_exchange_rate(currency: str) -> float:
    """
    통화 → 원화 환율 반환
    USD → 1달러당 원화, JPY → 1엔당 원화, KRW → 1.0
    """
    if currency == "KRW":
        return 1.0
    symbol = CURRENCY_SYMBOLS.get(currency)
    if not symbol:
        return 1.0
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="2d")
        if hist.empty:
            return 1.0
        return float(hist["Close"].iloc[-1])
    except Exception:
        return 1.0


def get_historical_exchange_rate(currency: str, date_str: str) -> tuple:
    """
    특정 날짜의 환율 조회 (주말·공휴일은 직전 거래일 기준).
    Returns: (rate: float, actual_date: str)
    """
    if currency == "KRW":
        return 1.0, date_str

    symbol = CURRENCY_SYMBOLS.get(currency)
    if not symbol:
        return 1.0, date_str

    target = datetime.strptime(date_str, "%Y-%m-%d")
    start = (target - timedelta(days=10)).strftime("%Y-%m-%d")
    end = (target + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        t = yf.Ticker(symbol)
        hist = t.history(start=start, end=end)
        if hist.empty:
            fallback = get_exchange_rate(currency)
            return fallback, "현재 (과거 데이터 없음)"

        # timezone 제거 후 날짜 비교
        if hist.index.tzinfo is not None:
            hist.index = hist.index.tz_localize(None)

        before = hist[hist.index.date <= target.date()]
        if before.empty:
            # target보다 이전 데이터가 없으면 가장 오래된 것 사용
            actual_date = hist.index[0].date().strftime("%Y-%m-%d")
            return float(hist["Close"].iloc[0]), actual_date

        actual_date = before.index[-1].date().strftime("%Y-%m-%d")
        return float(before["Close"].iloc[-1]), actual_date
    except Exception:
        fallback = get_exchange_rate(currency)
        return fallback, "현재 (조회 실패)"


def get_exchange_rate_history(currency: str, days: int = 30) -> pd.DataFrame:
    """환율 히스토리 DataFrame 반환"""
    if currency == "KRW":
        return pd.DataFrame()
    symbol = CURRENCY_SYMBOLS.get(currency)
    if not symbol:
        return pd.DataFrame()
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=f"{days}d")
        return hist[["Close"]].rename(columns={"Close": "rate"})
    except Exception:
        return pd.DataFrame()


def get_exchange_rate_long_history(currency: str, years: int = 10) -> pd.DataFrame:
    """환율 장기 히스토리 DataFrame 반환 (최대 10년, 일별 데이터)"""
    if currency == "KRW":
        return pd.DataFrame()
    symbol = CURRENCY_SYMBOLS.get(currency)
    if not symbol:
        return pd.DataFrame()
    try:
        t = yf.Ticker(symbol)
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=int(years * 365.25))
        hist = t.history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
        )
        if hist.empty:
            return pd.DataFrame()
        df = hist[["Close"]].rename(columns={"Close": "rate"})
        if df.index.tzinfo is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception:
        return pd.DataFrame()


def get_price_history(ticker: str, days: int = 30) -> pd.DataFrame:
    """주가 히스토리 DataFrame 반환"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{days}d")
        return hist[["Close", "Volume"]].rename(columns={"Close": "price"})
    except Exception:
        return pd.DataFrame()


def get_daily_change(ticker: str) -> dict:
    """당일 등락 정보 반환 (전일 대비)"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if len(hist) < 2:
            return {"change": 0.0, "change_pct": 0.0, "prev_close": 0.0, "current": 0.0}
        prev = float(hist["Close"].iloc[-2])
        curr = float(hist["Close"].iloc[-1])
        change = curr - prev
        change_pct = (change / prev * 100) if prev > 0 else 0.0
        return {
            "change": change,
            "change_pct": change_pct,
            "prev_close": prev,
            "current": curr,
        }
    except Exception:
        return {"change": 0.0, "change_pct": 0.0, "prev_close": 0.0, "current": 0.0}


def get_stock_news(ticker: str, max_items: int = 5) -> list[dict]:
    """yfinance 뉴스 조회"""
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        result = []
        for item in news[:max_items]:
            result.append({
                "title": item.get("content", {}).get("title", ""),
                "summary": item.get("content", {}).get("summary", ""),
                "published": item.get("content", {}).get("pubDate", ""),
                "url": item.get("content", {}).get("canonicalUrl", {}).get("url", ""),
            })
        return result
    except Exception:
        return []


def get_market_news(max_per_source: int = 5) -> list[dict]:
    """
    여러 경제 뉴스 RSS에서 최신 헤드라인 수집.
    반환: [{"source": "...", "title": "...", "summary": "...", "url": "..."}]
    """
    sources = [
        # 글로벌
        {"name": "Reuters Business",    "url": "https://feeds.reuters.com/reuters/businessNews"},
        {"name": "Reuters Markets",      "url": "https://feeds.reuters.com/reuters/marketsNews"},
        {"name": "MarketWatch",          "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
        {"name": "CNBC",                 "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
        # 일본
        {"name": "NHK Business",         "url": "https://www3.nhk.or.jp/rss/news/cat6.xml"},
        {"name": "Yahoo Finance JP",     "url": "https://finance.yahoo.co.jp/rss/category/stocks"},
        # 한국
        {"name": "한국경제",              "url": "https://www.hankyung.com/feed/all-news"},
        {"name": "매일경제",              "url": "https://www.mk.co.kr/rss/30100041/"},
        {"name": "연합뉴스",              "url": "https://www.yna.co.kr/RSS/news.xml"},
    ]

    results = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for src in sources:
        try:
            resp = requests.get(src["url"], headers=headers, timeout=6)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # RSS 2.0
            items = root.findall(".//item")
            count = 0
            for item in items:
                if count >= max_per_source:
                    break
                title = (item.findtext("title") or "").strip()
                summary = (item.findtext("description") or "").strip()[:200]
                url = (item.findtext("link") or "").strip()
                if title:
                    results.append({"source": src["name"], "title": title, "summary": summary, "url": url})
                    count += 1

            # Atom feed fallback
            if count == 0:
                entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
                for entry in entries[:max_per_source]:
                    title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                    summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()[:200]
                    link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                    url = link_el.get("href", "") if link_el is not None else ""
                    if title:
                        results.append({"source": src["name"], "title": title, "summary": summary, "url": url})
        except Exception:
            continue

    return results


def _get_stock_native_currency(ticker: str) -> str:
    """티커 접미사로 주식/암호화폐의 실제 거래 통화를 추정."""
    if ticker.endswith(".T") or ticker.endswith(".JP"):
        return "JPY"
    elif ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "KRW"
    elif ticker.endswith("-JPY"):
        return "JPY"  # 암호화폐 엔화 (BTC-JPY, ETH-JPY 등)
    elif ticker.endswith("-KRW"):
        return "KRW"
    else:
        return "USD"  # 미국 주식, BTC-USD 등 기본값


def calculate_profit_loss(stock: dict) -> dict:
    """
    원화 기준 수익/손실 계산.
    stock: portfolio 테이블의 행(dict)

    주식의 실제 거래 통화와 매수 통화가 다른 경우(예: TSLA를 원화로 구매)에도
    올바르게 계산합니다.
    """
    ticker = stock["ticker"]
    quantity = stock["quantity"]
    purchase_price = stock["purchase_price"]
    purchase_rate = stock["purchase_exchange_rate"]  # 매수 당시 환율
    currency = stock["purchase_currency"]

    current_price_native = get_current_price(ticker)  # 주식의 실제 통화 가격 (예: TSLA → USD)
    native_currency = _get_stock_native_currency(ticker)

    # ── 통화 불일치 처리 ──────────────────────────────────────
    # 예: TSLA(USD)를 KRW로 구매한 경우
    #   purchase_price = ₩334,224 (KRW), current_price = $248 (USD)
    #   → current_price를 KRW로 변환해서 비교해야 함
    if native_currency != currency:
        # 주식의 실제 통화 → KRW 환율 (예: USD → ₩1,400)
        native_to_krw = get_exchange_rate(native_currency)
        # 매수 통화 → KRW 환율 (예: KRW → 1.0, JPY → 9.4)
        purchase_to_krw = get_exchange_rate(currency) if currency != "KRW" else 1.0

        # current_price를 매수 통화로 변환
        if purchase_to_krw > 0:
            current_price = current_price_native * native_to_krw / purchase_to_krw
        else:
            current_price = current_price_native

        # 현재 환율: 주식의 실제 통화 기준 사용
        current_rate = native_to_krw
    else:
        current_price = current_price_native
        current_rate = get_exchange_rate(currency)

    # ── 원화 환산 금액 ─────────────────────────────────────────
    purchase_value_krw = purchase_price * purchase_rate * quantity

    if native_currency != currency:
        # 통화 불일치: 실제 주가 × 실제 통화의 KRW 환율로 현재 가치 계산
        current_value_krw = current_price_native * current_rate * quantity
        # 주가/환율 수익 분리 불가 → 전체 손익만 계산
        total_gain_krw = current_value_krw - purchase_value_krw
        total_gain_pct = (total_gain_krw / purchase_value_krw * 100) if purchase_value_krw > 0 else 0.0
        stock_gain_krw = total_gain_krw
        stock_gain_pct = total_gain_pct
        fx_gain_krw = 0.0
        fx_gain_pct = 0.0
    else:
        current_value_krw = current_price * current_rate * quantity
        # 주가 변동으로 인한 수익 (환율 고정)
        stock_gain_krw = (current_price - purchase_price) * purchase_rate * quantity
        stock_gain_pct = ((current_price - purchase_price) / purchase_price * 100) if purchase_price > 0 else 0.0
        # 환율 변동으로 인한 수익 (주가 고정)
        fx_gain_krw = current_price * (current_rate - purchase_rate) * quantity
        fx_gain_pct = ((current_rate - purchase_rate) / purchase_rate * 100) if purchase_rate > 0 else 0.0
        # 총 수익
        total_gain_krw = current_value_krw - purchase_value_krw
        total_gain_pct = (total_gain_krw / purchase_value_krw * 100) if purchase_value_krw > 0 else 0.0

    # 당일 등락
    daily = get_daily_change(ticker)

    return {
        "ticker": ticker,
        "name": stock.get("name", ticker),
        "quantity": quantity,
        "purchase_price": purchase_price,
        "current_price": current_price,
        "currency": currency,
        "purchase_rate": purchase_rate,
        "current_rate": current_rate,
        "purchase_value_krw": purchase_value_krw,
        "current_value_krw": current_value_krw,
        "stock_gain_krw": stock_gain_krw,
        "fx_gain_krw": fx_gain_krw,
        "total_gain_krw": total_gain_krw,
        "stock_gain_pct": stock_gain_pct,
        "fx_gain_pct": fx_gain_pct,
        "total_gain_pct": total_gain_pct,
        "daily_change_pct": daily["change_pct"],
        "daily_change": daily["change"],
    }


def aggregate_stocks_by_ticker(stocks: list) -> list:
    """
    같은 티커의 복수 매수 항목을 1개로 합산.
    가중평균 매수가·환율, 총 수량으로 반환.
    """
    from collections import defaultdict
    grouped = defaultdict(list)
    for s in stocks:
        grouped[s["ticker"]].append(s)

    result = []
    for ticker, entries in grouped.items():
        total_qty = sum(e["quantity"] for e in entries)
        total_cost_krw = sum(
            e["purchase_price"] * e["purchase_exchange_rate"] * e["quantity"]
            for e in entries
        )
        total_native = sum(e["purchase_price"] * e["quantity"] for e in entries)
        avg_price = total_native / total_qty if total_qty > 0 else 0.0
        avg_rate = total_cost_krw / total_native if total_native > 0 else 1.0
        earliest = min(entries, key=lambda e: e["purchase_date"])
        result.append({
            "id": earliest["id"],
            "ticker": ticker,
            "name": entries[0]["name"],
            "quantity": total_qty,
            "purchase_price": avg_price,
            "purchase_currency": entries[0]["purchase_currency"],
            "purchase_exchange_rate": avg_rate,
            "purchase_date": earliest["purchase_date"],
            "notes": " | ".join(e["notes"] for e in entries if e.get("notes")),
            "_entries": entries,
        })
    return result


def get_portfolio_summary(stocks: list[dict]) -> dict:
    """전체 포트폴리오 합계 계산"""
    if not stocks:
        return {
            "total_purchase_krw": 0,
            "total_current_krw": 0,
            "total_gain_krw": 0,
            "total_gain_pct": 0,
            "items": [],
        }

    items = []
    total_purchase = 0.0
    total_current = 0.0

    for stock in stocks:
        result = calculate_profit_loss(stock)
        items.append(result)
        total_purchase += result["purchase_value_krw"]
        total_current += result["current_value_krw"]

    total_gain = total_current - total_purchase
    total_gain_pct = (total_gain / total_purchase * 100) if total_purchase > 0 else 0.0

    return {
        "total_purchase_krw": total_purchase,
        "total_current_krw": total_current,
        "total_gain_krw": total_gain,
        "total_gain_pct": total_gain_pct,
        "items": items,
    }
