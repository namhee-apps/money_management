"""
app.py - 주식 포트폴리오 모니터링 대시보드 (한국어)
실행: streamlit run app.py
"""

import os
import sys
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

# Streamlit Cloud secrets → 환경변수에 주입 (.env가 없는 클라우드 환경용)
try:
    for _sk, _sv in st.secrets.items():
        if isinstance(_sv, str) and _sk not in os.environ:
            os.environ[_sk] = _sv
except Exception:
    pass

from modules.database import (
    get_all_stocks, add_stock, update_stock, update_stock_name, update_stock_date,
    update_stock_exchange_rate, update_stock_broker, update_stock_account_type, update_stock_ticker,
    delete_stock, reduce_stock_quantity, get_reports, get_snapshots, get_snapshots_by_date, get_portfolio_value_history, save_snapshot, update_stock_group,
    add_recurring_investment, get_recurring_investments, update_recurring_last_added, toggle_recurring_investment, delete_recurring_investment,
    add_sold_record, get_sold_history, get_sold_summary_by_ticker, delete_sold_record,
    add_dividend, get_dividends, get_dividend_summary, delete_dividend,
    save_settings_bulk, get_settings_bulk, save_setting, get_setting,
)
from modules.stock_data import (
    get_stock_info, get_portfolio_summary, get_exchange_rate,
    get_exchange_rate_history, get_historical_exchange_rate,
    get_exchange_rate_long_history,
    aggregate_stocks_by_ticker, get_price_history, get_stock_news, get_market_news, CURRENCY_LABELS,
    _get_stock_native_currency,
)
from modules.analysis import run_daily_analysis, translate_news_batch, parse_transaction_screenshot
import json as _json
from modules.notifications import (
    send_telegram_message, detect_chat_id,
    format_daily_message, send_daily_notification, fetch_watchlist_news,
    _load_env, _save_env_value,
)

# ── 페이지 설정 ────────────────────────────────────────────────
st.set_page_config(
    page_title="주식 포트폴리오 모니터링",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 비밀번호 인증 ────────────────────────────────────────────
def _check_password():
    """비밀번호 인증. Streamlit Cloud에서는 secrets.toml, 로컬에서는 .env 사용."""
    # 비밀번호 설정이 없으면 인증 건너뛰기 (로컬 개발용)
    _pw = ""
    try:
        _pw = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        pass
    if not _pw:
        _pw = os.getenv("APP_PASSWORD", "")
    if not _pw:
        return True  # 비밀번호 미설정 시 인증 없이 통과

    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 로그인")
    _input_pw = st.text_input("비밀번호를 입력하세요", type="password", key="login_pw")
    if st.button("로그인", type="primary"):
        if _input_pw == _pw:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False

if not _check_password():
    st.stop()

# ── 공통 스타일 ────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #1e1e2e;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 8px;
}
.gain { color: #f38ba8; font-weight: bold; }
.loss { color: #89dceb; font-weight: bold; }
.ticker { font-size: 0.85em; color: #a6adc8; }
</style>
""", unsafe_allow_html=True)


def format_krw(v: float) -> str:
    return f"₩{v:,.0f}"


def color_pct(v: float) -> str:
    c = "#f38ba8" if v >= 0 else "#89dceb"
    arrow = "▲" if v >= 0 else "▼"
    return f"<span style='color:{c};font-weight:bold'>{arrow}{abs(v):.2f}%</span>"


# ── 사이드바 네비게이션 ────────────────────────────────────────
st.sidebar.title("📊 주식 포트폴리오")
page = st.sidebar.radio(
    "메뉴",
    ["포트폴리오 현황", "종목 추가/관리", "투자 추천", "경제적 자유 계획", "환율 분석", "일일 리포트", "설정"],
    format_func=lambda x: {
        "포트폴리오 현황": "📊 포트폴리오 현황",
        "종목 추가/관리": "➕ 종목 추가/관리",
        "투자 추천": "💡 투자 추천",
        "경제적 자유 계획": "🏖️ 경제적 자유 계획",
        "환율 분석": "💱 환율 분석",
        "일일 리포트": "📅 일일 리포트",
        "설정": "⚙️ 설정",
    }[x],
)

st.sidebar.markdown("---")
st.sidebar.caption("데이터 출처: Yahoo Finance")
st.sidebar.caption(f"갱신: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# ── JPY 변환 헬퍼 (포트폴리오 전체에서 사용) ─────────────────
@st.cache_data(ttl=300)
def _jpy_rate_cached():
    return get_exchange_rate("JPY")


@st.cache_data(ttl=3600, show_spinner=False)
def _fx_long_history_cached(currency: str, years: int) -> pd.DataFrame:
    return get_exchange_rate_long_history(currency, years)


@st.cache_data(ttl=3600, show_spinner=False)
def _fx_usdjpy_history_cached(years: int) -> pd.DataFrame:
    """USD/JPY = (USD/KRW) ÷ (JPY/KRW) 계산"""
    df_usd = get_exchange_rate_long_history("USD", years)
    df_jpy = get_exchange_rate_long_history("JPY", years)
    if df_usd.empty or df_jpy.empty:
        return pd.DataFrame()
    df = df_usd.join(df_jpy, how="inner", lsuffix="_usd", rsuffix="_jpy")
    if df.empty:
        return pd.DataFrame()
    df["rate"] = df["rate_usd"] / df["rate_jpy"]
    return df[["rate"]]


@st.cache_data(ttl=300, show_spinner=False)
def _determine_year_by_price(ticker: str, purchase_price: float, month: int, day: int) -> tuple:
    """
    주가와 비교하여 매수 연도 추정.
    후보 연도(현재 ~ 4년 전) 중 해당 날짜 실제 주가와 매수가가 가장 가까운 연도를 선택.
    Returns: (year: int, diff_pct: float)
    """
    import yfinance as yf
    from datetime import datetime as _dt2, timedelta

    today = date.today()
    # 과거 포함, 오늘 이전인 날짜만 후보
    candidate_years = []
    for yr in range(today.year, today.year - 5, -1):
        try:
            candidate_dt = date(yr, month, day)
            if candidate_dt <= today:
                candidate_years.append(yr)
        except ValueError:
            pass

    if not candidate_years:
        return today.year, 100.0
    if len(candidate_years) == 1:
        return candidate_years[0], 0.0

    best_year = candidate_years[0]
    best_diff_pct = float("inf")

    for yr in candidate_years:
        try:
            target_dt = _dt2(yr, month, day)
            start_s = (target_dt - timedelta(days=10)).strftime("%Y-%m-%d")
            end_s   = (target_dt + timedelta(days=4)).strftime("%Y-%m-%d")
            t = yf.Ticker(ticker)
            hist = t.history(start=start_s, end=end_s)
            if hist.empty:
                continue
            if hist.index.tzinfo is not None:
                hist.index = hist.index.tz_localize(None)
            before = hist[hist.index.date <= target_dt.date()]
            closest_price = float((before if not before.empty else hist)["Close"].iloc[-1])
            diff_pct = abs(closest_price - purchase_price) / purchase_price * 100
            if diff_pct < best_diff_pct:
                best_diff_pct = diff_pct
                best_year = yr
        except Exception:
            continue

    return best_year, best_diff_pct


def format_jpy(krw: float, jpy_rate: float) -> str:
    """KRW → JPY 변환 후 포맷"""
    return f"¥{(krw / jpy_rate):,.0f}" if jpy_rate > 0 else f"¥{krw:,.0f}"


def generate_offline_html_snapshot():
    """
    현재 포트폴리오 상태를 단일 HTML 파일로 내보내기.
    Plotly JS를 인라인으로 포함해 오프라인에서도 차트가 동작함.
    비행기 등 인터넷이 없을 때 핸드폰 브라우저로 확인하는 용도.
    """
    stocks = get_all_stocks()
    if not stocks:
        return None

    aggregated = aggregate_stocks_by_ticker(stocks)
    summary = get_portfolio_summary(aggregated)
    jpy_rate = _jpy_rate_cached()
    items = summary["items"]

    sold_history = get_sold_history(limit=10000)
    realized_total_krw = sum((r.get("realized_gain_krw") or 0) for r in sold_history)

    gain_krw = summary["total_gain_krw"]
    gain_pct = summary["total_gain_pct"]
    total_net_krw = gain_krw + realized_total_krw

    def _j(v):
        return v / jpy_rate if jpy_rate > 0 else v

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sym_map = {"USD": "$", "JPY": "¥", "KRW": "₩"}

    # ── 상단 요약 카드 ─────────────────────────────────────
    mis_cls = "gain" if gain_krw >= 0 else "loss"
    rel_cls = "gain" if realized_total_krw >= 0 else "loss"
    net_cls = "gain" if total_net_krw >= 0 else "loss"
    sold_count = len(sold_history)

    metrics_html = f"""
    <div class="metrics">
      <div class="metric"><div class="label">총 투자금 (JPY)</div>
        <div class="value">¥{_j(summary['total_purchase_krw']):,.0f}</div></div>
      <div class="metric"><div class="label">현재 평가액 (JPY)</div>
        <div class="value">¥{_j(summary['total_current_krw']):,.0f}</div></div>
      <div class="metric"><div class="label">미실현 손익 (JPY)</div>
        <div class="value {mis_cls}">¥{_j(gain_krw):+,.0f}<br><small>{gain_pct:+.2f}%</small></div></div>
      <div class="metric"><div class="label">확정 수익 (JPY)</div>
        <div class="value {rel_cls}">¥{_j(realized_total_krw):+,.0f}<br><small>{sold_count}건 매도</small></div></div>
      <div class="metric"><div class="label">보유 종목 수</div>
        <div class="value">{len(items)}종목</div></div>
    </div>
    <div class="total-net {net_cls}">
      💰 <b>전체 순손익 (미실현 + 확정)</b> —
      <span class="big">¥{_j(total_net_krw):+,.0f}</span>
      <span class="muted">(₩{total_net_krw:+,.0f})</span>
    </div>
    """

    # ── 종목 테이블 ────────────────────────────────────────
    holdings_rows = []
    for item in items:
        sym = sym_map.get(item["currency"], "$")
        gc  = "gain" if item["total_gain_pct"] >= 0 else "loss"
        dc  = "gain" if item["daily_change_pct"] >= 0 else "loss"
        holdings_rows.append(f"""
          <tr>
            <td><b>{item['name']}</b><br><small>{item['ticker']}</small></td>
            <td>{item['quantity']:.0f}주</td>
            <td>{sym}{item['purchase_price']:,.2f}</td>
            <td>{sym}{item['current_price']:,.2f}</td>
            <td class="{dc}">{item['daily_change_pct']:+.2f}%</td>
            <td class="{gc}">{item['total_gain_pct']:+.2f}%</td>
            <td>¥{_j(item['current_value_krw']):,.0f}</td>
            <td class="{gc}">¥{_j(item['total_gain_krw']):+,.0f}</td>
          </tr>
        """)
    holdings_html = f"""
    <h2>📋 종목별 현황</h2>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th>종목</th><th>수량</th><th>평단가</th><th>현재가</th>
          <th>당일</th><th>총수익</th><th>평가액(¥)</th><th>손익(¥)</th>
        </tr></thead>
        <tbody>{''.join(holdings_rows)}</tbody>
      </table>
    </div>
    """

    # ── 매도 이력 테이블 ────────────────────────────────────
    sold_html_block = ""
    if sold_history:
        sold_rows = []
        for r in sold_history[:100]:
            sym_s   = sym_map.get(r.get("sell_currency", "JPY"), "¥")
            gkrw    = r.get("realized_gain_krw", 0) or 0
            gjpy    = _j(gkrw)
            gpct    = r.get("realized_gain_pct", 0) or 0
            gc      = "gain" if gkrw >= 0 else "loss"
            sold_rows.append(f"""
              <tr>
                <td>{r.get('sell_date','')}</td>
                <td><b>{r.get('name','')}</b><br><small>{r.get('ticker','')}</small></td>
                <td>{r.get('broker','') or '-'}</td>
                <td>{r.get('quantity',0):.0f}주</td>
                <td>{sym_s}{r.get('sell_price',0):,.2f}</td>
                <td class="{gc}">¥{gjpy:+,.0f}<br><small>{gpct:+.2f}%</small></td>
              </tr>
            """)
        sold_html_block = f"""
        <h2>📉 매도 이력 (최근 {len(sold_rows)}건)</h2>
        <div class="tablewrap">
          <table>
            <thead><tr>
              <th>매도일</th><th>종목</th><th>증권사</th><th>수량</th><th>매도가</th><th>실현손익</th>
            </tr></thead>
            <tbody>{''.join(sold_rows)}</tbody>
          </table>
        </div>
        """

    # ── 차트 섹션 (종목별 90일) ─────────────────────────────
    chart_htmls = []
    plotly_js_included = False
    for item in items:
        try:
            df_price = get_price_history(item["ticker"], 90)
            if df_price.empty:
                continue
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_price.index, y=df_price["price"],
                mode="lines",
                line=dict(color="#3b82f6", width=2),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
            ))
            fig.add_hline(
                y=item["purchase_price"], line_dash="dash", line_color="#f38ba8",
                annotation_text=f"평단가 {item['purchase_price']:,.2f}",
                annotation_position="bottom right",
            )
            fig.update_layout(
                height=280,
                margin=dict(t=40, b=30, l=45, r=20),
                showlegend=False,
                title=f"{item['name']} ({item['ticker']})",
                xaxis_title="", yaxis_title=f"주가 ({item['currency']})",
                paper_bgcolor="#fff", plot_bgcolor="#fafafa",
            )
            chart_html = fig.to_html(
                include_plotlyjs=(False if plotly_js_included else "inline"),
                full_html=False,
                div_id=f"chart_{item['ticker'].replace('.', '_').replace(':', '_')}",
            )
            plotly_js_included = True
            chart_htmls.append(f'<div class="chart-card">{chart_html}</div>')
        except Exception:
            continue

    charts_section = ""
    if chart_htmls:
        charts_section = f"""
        <h2>📈 주가 차트 (최근 90일)</h2>
        <div class="charts">{''.join(chart_htmls)}</div>
        """

    # ── 전체 HTML 조립 ──────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>포트폴리오 스냅샷 — {now_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
      max-width: 1000px; margin: auto; padding: 20px;
      background: #fafafa; color: #1a1a1a;
    }}
    h1 {{ margin: 0 0 4px 0; font-size: 1.6em; }}
    h2 {{ margin-top: 32px; border-bottom: 2px solid #e5e5e5; padding-bottom: 6px; font-size: 1.15em; }}
    .timestamp {{ color: #888; font-size: 0.9em; margin-bottom: 20px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
    .metric {{
      background: #fff; border: 1px solid #e5e5e5;
      border-radius: 10px; padding: 14px 18px;
    }}
    .metric .label {{ color: #666; font-size: 0.85em; margin-bottom: 4px; }}
    .metric .value {{ font-size: 1.25em; font-weight: bold; }}
    .metric small {{ color: #888; font-size: 0.75em; font-weight: normal; }}
    .gain {{ color: #2e7d32; }}
    .loss {{ color: #c62828; }}
    .total-net {{
      background: #f0f8f1; border: 1px solid #c8e6c9;
      border-radius: 10px; padding: 14px 20px; margin: 16px 0;
      font-size: 1.0em;
    }}
    .total-net.loss {{ background: #fbf1f1; border-color: #f5c6c8; }}
    .total-net .big {{ font-size: 1.3em; font-weight: bold; }}
    .total-net.gain .big {{ color: #2e7d32; }}
    .total-net.loss .big {{ color: #c62828; }}
    .total-net .muted {{ color: #888; font-size: 0.9em; }}
    .tablewrap {{ overflow-x: auto; border-radius: 8px; }}
    table {{
      width: 100%; border-collapse: collapse; margin-top: 12px;
      background: #fff; border: 1px solid #e5e5e5;
    }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #f5f5f7; font-size: 0.85em; font-weight: 600; }}
    td small {{ color: #888; }}
    .charts {{ display: grid; gap: 20px; margin-top: 16px; }}
    .chart-card {{
      background: #fff; border: 1px solid #e5e5e5;
      border-radius: 10px; padding: 6px;
    }}
    .footer {{
      text-align: center; color: #888; margin-top: 40px;
      font-size: 0.85em; border-top: 1px solid #e5e5e5; padding-top: 20px;
    }}
    @media (max-width: 600px) {{
      body {{ padding: 12px; }}
      h1 {{ font-size: 1.3em; }}
      .metric .value {{ font-size: 1.05em; }}
      table {{ font-size: 0.82em; }}
      th, td {{ padding: 6px 8px; }}
    }}
  </style>
</head>
<body>
  <h1>📊 포트폴리오 스냅샷</h1>
  <div class="timestamp">생성: {now_str} · 참고 환율: 1엔 = ₩{jpy_rate:,.2f}</div>
  {metrics_html}
  {holdings_html}
  {sold_html_block}
  {charts_section}
  <div class="footer">
    이 파일은 <b>오프라인에서도 열립니다</b>. 비행기 등 인터넷이 없을 때 핸드폰 브라우저로 열어보세요.<br>
    생성 당시 상태의 정적 스냅샷이므로 실시간 가격은 반영되지 않습니다.
  </div>
</body>
</html>
"""
    return html


@st.cache_data(ttl=3600, show_spinner=False)
def _translate_news_cached(news_json: str) -> str:
    """
    뉴스 일괄 번역 (1시간 캐시).
    입출력 모두 JSON 문자열 — Streamlit 캐시 해싱에 안전.
    """
    items = _json.loads(news_json)
    translated = translate_news_batch(items)
    return _json.dumps(translated, ensure_ascii=False)


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_and_translate_article(url: str) -> str:
    """
    뉴스 URL에서 본문을 가져와 한국어로 번역/요약 (1시간 캐시).
    """
    import requests
    from bs4 import BeautifulSoup

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "⚠️ ANTHROPIC_API_KEY가 설정되지 않았습니다."

    # 기사 본문 가져오기
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 불필요한 태그 제거
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "ad"]):
            tag.decompose()

        # 본문 추출: article 태그 우선, 없으면 body
        article = soup.find("article")
        if article:
            text = article.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # 빈 줄 정리 & 길이 제한
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = "\n".join(lines)[:8000]

        if len(text) < 100:
            return "⚠️ 기사 본문을 가져올 수 없습니다. 원본 링크에서 직접 확인해주세요."

    except Exception as e:
        return f"⚠️ 기사를 가져올 수 없습니다: {e}"

    # Claude로 번역
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": (
                    "아래 영문 뉴스 기사를 한국어로 번역해주세요.\n\n"
                    "규칙:\n"
                    "- 회사명(Apple, Toyota), 티커(AAPL, NVDA), 금융 용어(AI, ETF, Fed, S&P500)는 영어 유지\n"
                    "- 자연스럽고 읽기 쉬운 한국어로 번역\n"
                    "- 핵심 내용을 빠짐없이 전달하되, 광고/관련기사 링크 등은 제외\n"
                    "- 마크다운 형식으로 소제목(###)과 단락을 구분해서 읽기 쉽게 정리\n\n"
                    f"기사 본문:\n{text}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"⚠️ 번역 실패: {e}"


# ── 통합 키워드 추출 헬퍼 ────────────────────────────────────
import re as _re

# 완전히 무시할 영어 단어 (일반 동사/전치사/대명사/형용사 포함)
_EN_STOP = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","up","about","into","over","after","is","are","was","were",
    "be","been","have","has","had","do","does","did","will","would","could",
    "should","may","might","this","that","as","so","than","just","also",
    "it","its","he","she","him","her","hers","his","they","them","their",
    "we","our","you","your","who","what","which","when","where","there",
    "these","those","some","any","all","both","each","every","other",
    "not","says","said","say","gets","got","make","made","take","took",
    "come","came","know","think","look","see","seen","want","need",
    "tell","told","keep","kept","turn","move","give","given","find",
    "found","seem","show","showed","feel","felt","become","became",
    "new","old","good","bad","big","high","low","long","more","most",
    "much","many","few","last","next","first","own","same","such",
    "only","well","very","too","quite","really","never","always",
    "often","again","still","even","here","then","now","while","once",
    "amid","despite","against","during","through","without","near","per",
    "cent","week","month","year","years","quarter","report","reports",
    "stock","stocks","share","shares","inc","corp","ltd","plc","group",
    "best","pick","picks","plan","plans","deal","deals","loss","losses",
    "rally","cheap","dirt","hers","story","stories","weight","threat",
    "climb","climbs","spark","change","changes","following","within",
    "before","ahead","after","since","already","said","says","cited",
    "data","back","down","rise","fall","drop","gain","flat","hold",
    "beat","miss","push","pull","open","close","want","like","hims",
    "muse","pick","while","model","rally","misunderstood","confronts",
    "reported","spark","still","story","weight","despite","coreweave",
    "advancing","climbs","plans","really","hers","hims",
}

# 무시할 한국어 단어 (일반 동사/형용사/부사/접속사/조사/수식어)
_KR_STOP = {
    # 동사/형용사
    "있다","이다","하다","되다","않다","없다","보다","오다","가다","주다",
    "받다","들다","나다","서다","살다","먹다","알다","쓰다","열다","닫다",
    "듣다","보이다","나오다","들어가다","올리다","내리다","떨어지다",
    # 일반 부사/수식어
    "껑충","급격","주목","확대","축소","전환","지속","유지","강화","완화",
    "소폭","대폭","다소","비교적","상대적","본격","잠정","잠시","일단",
    "오히려","여전히","결국","마침내","드디어","갑자기","계속","아직",
    "다시","또","더","매우","가장","모두","함께","거의","약","총","각",
    # 접속사/조사/대명사
    "위해","통해","대한","관련","이번","지난","이후","현재","최근",
    "따른","따라","위한","이상","이하","대해","으로","에서","에게",
    "까지","부터","보다","처럼","때문","이런","저런","이와","또는",
    "그리고","하지만","그러나","따라서","그래서","한편","반면","다만",
    # 일반 명사 (투자 무관)
    "부분","경우","내용","분기","상황","가능","정도","방향","방법",
    "시작","시점","수준","결과","영향","가운데","속에서","것으로",
    "측면","차원","모습","움직임","분위기","기대","우려","예상","전망",
    "발표","계획","검토","조사","확인","설명","강조","지적","평가",
}

# 투자/금융 핵심 용어 — 단 1회 등장해도 무조건 포함
_FINANCE_TERMS = {
    # 반도체/기술
    "hbm","nvidia","tsmc","amd","intel","arm","qualcomm","broadcom",
    "semiconductor","chip","chips","memory","gpu","cpu","foundry","wafer",
    "lithography","datacenter","cloudcomputing","quantum",
    # AI/소프트웨어
    "openai","anthropic","gemini","chatgpt","llm","deepseek",
    "artificial","intelligence","machine","learning","inference",
    # 거시경제/Fed
    "fed","federal","inflation","deflation","stagflation","recession",
    "gdp","cpi","ppi","pce","rate","rates","interest","yield","yields",
    "treasury","tariff","tariffs","trade","deficit","surplus","fiscal",
    # 기업 실적/밸류에이션
    "earnings","revenue","profit","margin","guidance","forecast","outlook",
    "ebitda","cashflow","valuation","multiple","buyback","dividend","debt",
    "leverage","ipo","merger","acquisition","spinoff",
    # 시장 지표
    "nasdaq","dowjones","russell","vix","volatility","liquidity",
    "correction","bearish","bullish","shortsell","longsell",
    # 섹터
    "biotech","pharma","healthcare","defense","automotive","housing",
    "energy","renewable","nuclear","fintech","crypto","bitcoin",
    # 한국어 투자 핵심어
    "반도체","메모리","hbm","낸드","디램","파운드리","인공지능","데이터센터",
    "금리","기준금리","인플레이션","디플레이션","경기침체","스태그플레이션",
    "실적","매출","영업이익","순이익","배당","자사주","밸류에이션",
    "관세","무역전쟁","공급망","서플라이체인","환율","원달러","원엔",
    "증시","코스피","나스닥","다우","러셀","변동성","유동성",
    "삼성전자","하이닉스","엔비디아","애플","테슬라","마이크로소프트",
    "알파벳","메타","아마존","구글","오픈ai","앤트로픽",
    "바이오","제약","방산","자동차","이차전지","배터리","태양광",
}


# 한국어 동사/형용사형 어미 패턴 (이런 패턴으로 끝나면 키워드에서 제외)
_KR_VERB_SUFFIXES = _re.compile(
    r'(다|했다|된다|한다|는다|ㄴ다|됐다|갔다|왔다|났다|졌다|려다|겠다|이다'
    r'|하는|되는|된|하며|하고|해서|하면|되면|하여|에서|으로|까지|부터'
    r'|인가|인지|할까|될까|습니까|합니다|됩니다|입니다|했습니다'
    r'|하게|되게|라며|라고|라는|이라|에게|에서|으며|이며)$'
)


def _extract_words_from_title(title: str) -> set:
    """
    한국어/영어/혼합 제목에서 투자 관련 의미 있는 키워드만 추출.
    우선순위: 금융 핵심 용어 > 대문자 약어(HBM/AI) > 고유명사 > 긴 영어 단어 > 한국어 명사
    한국어는 동사/형용사형 어미를 자동으로 필터링합니다.
    """
    words = set()
    for raw in title.split():
        cleaned = _re.sub(r'^[^\w가-힣]+|[^\w가-힣]+$', '', raw)
        if not cleaned or len(cleaned) < 2:
            continue

        lower = cleaned.lower()

        # 1. 한국어 포함 단어 (2자 이상)
        if _re.search(r'[가-힣]', cleaned):
            if lower in _KR_STOP or cleaned in _KR_STOP:
                continue
            # 금융 핵심어면 무조건 포함
            if lower in _FINANCE_TERMS:
                words.add(cleaned)
                continue
            # 동사/형용사형 어미로 끝나면 제외
            if _KR_VERB_SUFFIXES.search(cleaned):
                continue
            # 2자는 너무 짧아 노이즈 → 3자 이상만 허용 (금융 핵심어 제외)
            if len(cleaned) >= 3:
                words.add(cleaned)
            continue

        # 2. 대문자 약어 / 고유명사 (ALL-CAPS: HBM, AI, Fed, GDP, ETF)
        if cleaned.isupper() and 2 <= len(cleaned) <= 8 and lower not in _EN_STOP:
            words.add(cleaned)
            continue

        # 3. 금융 핵심 용어 리스트에 있으면 무조건 포함
        if lower in _FINANCE_TERMS and lower not in _EN_STOP:
            words.add(lower)
            continue

        # 4. TitleCase 고유명사 (회사명 등, 4자 이상)
        if (cleaned[0].isupper() and cleaned[1:].islower()
                and len(cleaned) >= 4 and lower not in _EN_STOP):
            words.add(cleaned)
            continue

        # 5. 일반 영어 단어는 5자 이상만 허용 (노이즈 최소화)
        if cleaned.isalpha() and len(cleaned) >= 5 and lower not in _EN_STOP:
            words.add(lower)

    return words


def _extract_bigrams_from_title(title: str) -> set:
    """
    뉴스 제목에서 문맥 있는 2어절 구문을 추출.
    예: "NVIDIA 주가 껑충" → "NVIDIA 주가", "주가 껑충"
    금융 핵심어나 고유명사가 포함된 구문만 반환.
    """
    raw_words = title.split()
    cleaned_pairs = []
    for raw in raw_words:
        c = _re.sub(r'^[^\w가-힣]+|[^\w가-힣]+$', '', raw)
        if c and len(c) >= 2:
            cleaned_pairs.append(c)

    bigrams = set()
    for i in range(len(cleaned_pairs) - 1):
        w1, w2 = cleaned_pairs[i], cleaned_pairs[i + 1]
        l1, l2 = w1.lower(), w2.lower()
        # 둘 중 하나가 금융 핵심어, 대문자 약어, 또는 고유명사(TitleCase 4자+)면 구문 채택
        is_important_1 = (l1 in _FINANCE_TERMS or (w1.isupper() and len(w1) >= 2) or
                          (w1[0].isupper() and len(w1) >= 4))
        is_important_2 = (l2 in _FINANCE_TERMS or (w2.isupper() and len(w2) >= 2) or
                          (w2[0].isupper() and len(w2) >= 4))
        if is_important_1 or is_important_2:
            pair = f"{w1} {w2}"
            # 너무 긴 구문 제외
            if len(pair) <= 30:
                bigrams.add(pair)
    return bigrams


@st.cache_data(ttl=1800, show_spinner=False)
def build_unified_keywords_ai(titles_json: str) -> list:
    """
    Claude AI로 뉴스 제목을 분석해 투자에 의미 있는 키워드만 추출.
    기계적 단어 분리 대신 문맥을 이해해서 추출하므로 "CEO", "How Will" 같은
    무의미한 키워드가 나오지 않음.
    titles_json: JSON 문자열 (캐시 해싱용)
    반환: [(keyword, importance, label), ...]
    """
    import anthropic as _anth_kw
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    titles = _json.loads(titles_json)

    if not api_key or not titles:
        return _build_keywords_fallback(titles)

    # 제목 목록 구성 (최대 80개)
    title_lines = []
    for i, t in enumerate(titles[:80]):
        title_lines.append(f"{i+1}. {t}")
    titles_text = "\n".join(title_lines)

    prompt = f"""아래 주식/경제 뉴스 제목 목록에서 **투자자에게 의미 있는 핵심 키워드**를 15~20개 추출하세요.

규칙:
- "CEO", "NEWS", "How Will", "Too Late" 같은 일반적인 단어/구문은 절대 포함하지 마세요
- 투자 판단에 도움이 되는 구체적인 키워드만 추출 (예: "유가 하락", "HBM 수요", "Fed 금리 동결", "NVIDIA 실적")
- 여러 기사에서 반복되는 주제일수록 중요도를 높게
- 회사명+이슈 형태가 좋습니다 (예: "Apple AI 전략", "삼성 반도체 투자")
- 거시경제 이슈도 포함 (예: "미-이란 협상", "관세 리스크")
- 한국어로 작성, 고유명사(회사명/기술명)는 영어 유지
- JSON 배열로만 답변, 설명 없이

형식: [{{"keyword": "유가 하락", "importance": 5}}, ...]
importance: 1(낮음) ~ 5(매우 중요)

뉴스 제목:
{titles_text}"""

    try:
        _client = _anth_kw.Anthropic(api_key=api_key)
        _msg = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _msg.content[0].text.strip()
        import re as _re_kw
        # JSON 추출
        code_block = _re_kw.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        if code_block:
            raw = code_block.group(1).strip()
        else:
            arr = _re_kw.search(r"(\[[\s\S]+\])", raw)
            if arr:
                raw = arr.group(1)
        items = _json.loads(raw)
        if not isinstance(items, list):
            return _build_keywords_fallback(titles)

        def _emoji(imp):
            if imp >= 5: return "🔴"
            if imp >= 4: return "🟠"
            if imp >= 3: return "🟡"
            return ""

        return [
            (it["keyword"], it.get("importance", 3),
             f"{_emoji(it.get('importance', 3))} {it['keyword']}".strip())
            for it in items if it.get("keyword")
        ]
    except Exception:
        return _build_keywords_fallback(titles)


def _build_keywords_fallback(titles: list) -> list:
    """AI 키워드 추출 실패 시 기존 방식으로 폴백 (금융 핵심어 위주)."""
    from collections import Counter
    freq = Counter()
    for t in titles:
        freq.update(_extract_words_from_title(t))
    if not freq:
        return []
    # 금융 핵심어만 우선
    result = []
    for w, c in freq.most_common(20):
        if w.lower() in _FINANCE_TERMS or (w.isupper() and len(w) >= 2 and w.lower() not in _EN_STOP):
            result.append((w, c, f"{'🟡' if c >= 2 else ''} {w}".strip()))
    return result


def build_unified_keywords(news_by_ticker: dict, market_news: list) -> list:
    """
    뉴스 제목을 모아서 Claude AI로 의미 있는 키워드를 추출.
    AI 실패 시 금융 핵심어 기반 폴백.
    """
    all_titles = []
    for ticker, news_list in news_by_ticker.items():
        for n in news_list:
            t = n.get("title_ko") or n.get("title", "")
            if t:
                all_titles.append(t)
    for n in market_news:
        t = n.get("title_ko") or n.get("title", "")
        if t:
            all_titles.append(t)

    if not all_titles:
        return []

    # 캐시를 위해 JSON 문자열로 변환
    titles_json = _json.dumps(all_titles[:80], ensure_ascii=False)
    return build_unified_keywords_ai(titles_json)


# ══════════════════════════════════════════════════════════════
# 페이지 1: 포트폴리오 현황
# ══════════════════════════════════════════════════════════════
if page == "포트폴리오 현황":
    st.title("📊 포트폴리오 현황")

    stocks = get_all_stocks()
    if not stocks:
        st.info("보유 종목이 없습니다. '종목 추가/관리' 메뉴에서 종목을 추가하세요.")
        st.stop()

    # 그룹 필터
    _all_groups = sorted({s.get("portfolio_group", "개별주식") for s in stocks})
    if len(_all_groups) > 1:
        _group_tabs = ["전체"] + _all_groups
        _sel_group = st.pills("포트폴리오", _group_tabs, default="전체", key="pf_group_filter")
    else:
        _sel_group = "전체"

    if _sel_group and _sel_group != "전체":
        stocks = [s for s in stocks if s.get("portfolio_group", "개별주식") == _sel_group]

    # 같은 티커 합산 후 계산
    with st.spinner("주가 및 환율 데이터 불러오는 중..."):
        aggregated = aggregate_stocks_by_ticker(stocks)
        summary = get_portfolio_summary(aggregated)
        jpy_rate = _jpy_rate_cached()

    items = summary["items"]
    gain_krw = summary["total_gain_krw"]
    gain_pct = summary["total_gain_pct"]

    # ── 오늘 스냅샷 자동 저장 (변동 추이 그래프용) ──────────────
    _today_str = date.today().isoformat()
    _today_snaps = get_snapshots_by_date(_today_str)
    if not _today_snaps:
        for _item in items:
            save_snapshot(
                date=_today_str,
                ticker=_item["ticker"],
                close_price=_item["current_price"],
                current_exchange_rate=_item["current_rate"],
                value_krw=_item["current_value_krw"],
                daily_change_pct=_item.get("daily_change_pct", 0),
            )

    # ── 확정 수익 (매도 손익 + 배당금) ────────────────────────
    _all_sold = get_sold_history(limit=10000)
    realized_total_krw = sum((r.get("realized_gain_krw") or 0) for r in _all_sold)
    realized_count     = len(_all_sold)

    _div_summary = get_dividend_summary()
    _div_total_krw = _div_summary.get("total_krw") or 0
    _div_count     = _div_summary.get("count") or 0
    _confirmed_total_krw = realized_total_krw + _div_total_krw

    # ── 상단 요약 카드 (JPY 기준) ─────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(
            "총 투자금 (JPY)",
            format_jpy(summary["total_purchase_krw"], jpy_rate),
            help="매수 당시 환율 기준 원화 환산 투자금 → 현재 JPY/KRW 기준으로 엔화 표시",
        )
    with col2:
        st.metric("현재 평가액 (JPY)", format_jpy(summary["total_current_krw"], jpy_rate))
    with col3:
        gain_sign = "+" if gain_krw >= 0 else ""
        st.metric(
            "미실현 손익 (JPY)",
            format_jpy(gain_krw, jpy_rate),
            f"{gain_sign}{gain_pct:.2f}%",
            help="현재 보유 종목의 평가 손익 (아직 매도하지 않은 분)",
        )
    with col4:
        _confirmed_jpy = (_confirmed_total_krw / jpy_rate) if jpy_rate > 0 else _confirmed_total_krw
        _conf_detail = f"{realized_count}건 매도"
        if _div_count:
            _conf_detail += f" + {_div_count}건 배당"
        st.metric(
            "확정 수익 (JPY)",
            f"¥{_confirmed_jpy:+,.0f}",
            _conf_detail,
            help="매도 손익 + 배당금 합계",
        )
    with col5:
        st.metric("보유 종목 수", f"{len(aggregated)}종목")

    # ── 전체 순손익 (미실현 + 확정) ───────────────────────────
    total_net_krw = gain_krw + _confirmed_total_krw
    total_net_jpy = (total_net_krw / jpy_rate) if jpy_rate > 0 else total_net_krw
    if total_net_krw >= 0:
        _net_bg     = "#f0f8f1"   # 연한 민트
        _net_border = "#c8e6c9"
        _net_color  = "#2e7d32"   # 짙은 그린
    else:
        _net_bg     = "#fbf1f1"   # 연한 분홍
        _net_border = "#f5c6c8"
        _net_color  = "#c62828"   # 짙은 레드
    _net_sign  = "+" if total_net_krw >= 0 else ""
    st.markdown(
        f"<div style='background:{_net_bg}; border:1px solid {_net_border}; "
        f"border-radius:10px; padding:14px 20px; margin:8px 0;'>"
        f"<span style='color:#555;'>💰 <b>전체 순손익 (미실현 + 확정)</b> — </span>"
        f"<span style='color:{_net_color}; font-size:1.25em; font-weight:bold;'>"
        f"¥{_net_sign}{total_net_jpy:,.0f}</span>"
        f"  <span style='color:#888;'>(₩{_net_sign}{total_net_krw:,.0f})</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # 원화 참고 (접혀있음)
    with st.expander("₩ 원화 기준 참고", expanded=False):
        st.caption(
            f"투자금 {format_krw(summary['total_purchase_krw'])} / "
            f"평가액 {format_krw(summary['total_current_krw'])} / "
            f"미실현 {format_krw(gain_krw)} ({gain_pct:+.2f}%) / "
            f"확정 수익 {format_krw(realized_total_krw)} ({realized_count}건)"
        )

    st.markdown("---")

    # ── 종목별 상세 테이블 ─────────────────────────────────────
    st.subheader("종목별 현황 (티커별 합산)")

    # 정렬 옵션
    _sort_col1, _sort_col2 = st.columns([3, 2])
    with _sort_col1:
        _sort_by = st.selectbox("정렬 기준", [
            "총수익률 높은 순", "총수익률 낮은 순",
            "평가액 큰 순", "평가액 작은 순",
            "당일등락 높은 순", "당일등락 낮은 순",
            "종목명 (가나다순)",
        ], key="sort_table")

    # 정렬 적용
    _sort_map = {
        "총수익률 높은 순":   lambda i: -i["total_gain_pct"],
        "총수익률 낮은 순":   lambda i: i["total_gain_pct"],
        "평가액 큰 순":       lambda i: -i["current_value_krw"],
        "평가액 작은 순":     lambda i: i["current_value_krw"],
        "당일등락 높은 순":   lambda i: -i["daily_change_pct"],
        "당일등락 낮은 순":   lambda i: i["daily_change_pct"],
        "종목명 (가나다순)":  lambda i: i["name"],
    }
    _sorted_items = sorted(items, key=_sort_map.get(_sort_by, lambda i: -i["total_gain_pct"]))

    sym_map = {"USD": "$", "JPY": "¥", "KRW": "₩"}
    table_data = []
    for item in _sorted_items:
        _purchase_sym = sym_map.get(item["currency"], "$")
        _native_cur = _get_stock_native_currency(item["ticker"])
        _native_sym = sym_map.get(_native_cur, "$")

        if _native_cur != item["currency"]:
            _native_price = item.get("current_price", 0)
            _native_to_krw = get_exchange_rate(_native_cur)
            _purchase_to_krw = get_exchange_rate(item["currency"]) if item["currency"] != "KRW" else 1.0
            if _native_to_krw > 0 and _purchase_to_krw > 0:
                _orig_native = _native_price * _purchase_to_krw / _native_to_krw
            else:
                _orig_native = _native_price
            _current_display = f"{_native_sym}{_orig_native:,.2f} ({_purchase_sym}{_native_price:,.0f})"
            _purchase_display = f"{_purchase_sym}{item['purchase_price']:,.0f}"
        else:
            _current_display = f"{_purchase_sym}{item['current_price']:,.2f}"
            _purchase_display = f"{_purchase_sym}{item['purchase_price']:,.2f}"

        curr_jpy = format_jpy(item["current_value_krw"], jpy_rate)
        gain_jpy = format_jpy(item["total_gain_krw"], jpy_rate)
        agg = next((a for a in aggregated if a["ticker"] == item["ticker"]), None)
        n_buys = len(agg["_entries"]) if agg and "_entries" in agg else 1
        qty_label = f"{item['quantity']:.0f}주" + (f" ({n_buys}회 매수)" if n_buys > 1 else "")

        table_data.append({
            "종목명": item["name"],
            "티커": item["ticker"],
            "수량": qty_label,
            "평균매수가": _purchase_display,
            "현재가": _current_display,
            "당일등락": f"{item['daily_change_pct']:+.2f}%",
            "총수익률": f"{item['total_gain_pct']:+.2f}%",
            "평가액(¥)": curr_jpy,
            "손익(¥)": gain_jpy,
        })

    st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── 자산 변동 추이 (보유 종목 주가 히스토리 기반) ─────────────
    st.subheader("📈 내 자산 변동 추이")

    _period_opts = {"1개월": 30, "3개월": 90, "6개월": 180, "1년": 365, "3년": 1095, "5년": 1825}
    _period_sel = st.pills("기간", list(_period_opts.keys()), default="3개월", key="val_hist_period")
    _period_days = _period_opts.get(_period_sel, 90)

    @st.cache_data(ttl=3600, show_spinner=False)
    def _calc_portfolio_history(_stocks_json: str, _days: int) -> tuple:
        """보유 종목의 주가+환율 히스토리로 포트폴리오 가치 추이 계산."""
        import yfinance as _yf_hist
        from datetime import timedelta as _td_hist
        _stocks_list = _json.loads(_stocks_json)
        if not _stocks_list:
            return [], []

        _end_dt = datetime.now().strftime("%Y-%m-%d")
        _start_dt = (datetime.now() - _td_hist(days=_days + 10)).strftime("%Y-%m-%d")

        # 티커별 보유 정보 집계: {ticker: [(quantity, purchase_date, currency), ...]}
        _holdings = {}
        for s in _stocks_list:
            tk = s["ticker"]
            if tk not in _holdings:
                _holdings[tk] = []
            _holdings[tk].append({
                "qty": s["quantity"],
                "date": s["purchase_date"],
                "currency": s["purchase_currency"],
            })

        # 주가 히스토리 조회
        _tickers = list(_holdings.keys())
        _price_data = {}  # {ticker: {date_str: close_price}}
        for tk in _tickers:
            try:
                _t = _yf_hist.Ticker(tk)
                _hist = _t.history(start=_start_dt, end=_end_dt)
                if _hist.empty:
                    continue
                if _hist.index.tzinfo is not None:
                    _hist.index = _hist.index.tz_localize(None)
                _price_data[tk] = {
                    idx.strftime("%Y-%m-%d"): float(row["Close"])
                    for idx, row in _hist.iterrows()
                }
            except Exception:
                continue

        # 환율 히스토리 조회
        _currencies = set()
        for lots in _holdings.values():
            for lot in lots:
                if lot["currency"] != "KRW":
                    _currencies.add(lot["currency"])

        _fx_data = {}  # {currency: {date_str: rate_krw}}
        _fx_symbols = {"USD": "USDKRW=X", "JPY": "JPYKRW=X"}
        for cur in _currencies:
            sym = _fx_symbols.get(cur)
            if not sym:
                continue
            try:
                _t = _yf_hist.Ticker(sym)
                _hist = _t.history(start=_start_dt, end=_end_dt)
                if _hist.empty:
                    continue
                if _hist.index.tzinfo is not None:
                    _hist.index = _hist.index.tz_localize(None)
                _fx_data[cur] = {
                    idx.strftime("%Y-%m-%d"): float(row["Close"])
                    for idx, row in _hist.iterrows()
                }
            except Exception:
                continue

        # 주가/환율 forward-fill: 휴일에는 직전 거래일 가격 사용
        def _ffill(data_dict):
            """날짜→값 dict를 정렬 후, 빠진 날짜를 직전 값으로 채운 dict 반환."""
            if not data_dict:
                return {}
            sorted_dates = sorted(data_dict.keys())
            return {d: data_dict[d] for d in sorted_dates}

        def _get_price_ffill(data_dict, target_date):
            """target_date 이하의 가장 가까운 가격 반환."""
            if not data_dict:
                return 0
            if target_date in data_dict:
                return data_dict[target_date]
            # 직전 거래일 가격 사용
            _prev = [d for d in data_dict if d <= target_date]
            return data_dict[_prev[-1]] if _prev else 0

        # 공통 날짜 구하기 (모든 시장의 거래일 합집합)
        _all_dates = set()
        for prices in _price_data.values():
            _all_dates.update(prices.keys())
        if not _all_dates:
            return [], []
        _all_dates = sorted(_all_dates)[-_days:]

        # 날짜별 포트폴리오 가치 계산
        _dates_out = []
        _values_out = []
        for d in _all_dates:
            _total = 0.0
            _valid = False
            for tk, lots in _holdings.items():
                if tk not in _price_data:
                    continue
                # 해당 날짜 주가 없으면 직전 거래일 가격 사용
                _price = _get_price_ffill(_price_data[tk], d)
                if _price <= 0:
                    continue
                # 이 날짜에 보유 중인 수량 합산 (매수일 <= 날짜)
                _qty = sum(lot["qty"] for lot in lots if lot["date"] <= d)
                if _qty <= 0:
                    continue
                _cur = lots[0]["currency"]
                if _cur == "KRW":
                    _fx = 1.0
                else:
                    _fx = _get_price_ffill(_fx_data.get(_cur, {}), d)
                    if _fx <= 0:
                        continue
                _total += _price * _qty * _fx
                _valid = True
            if _valid and _total > 0:
                _dates_out.append(d)
                _values_out.append(_total)
        return _dates_out, _values_out

    # 캐시 키용 JSON (stocks 변경 시 재계산)
    _stocks_for_hist = _json.dumps(
        [{"ticker": s["ticker"], "quantity": s["quantity"],
          "purchase_date": s["purchase_date"], "purchase_currency": s["purchase_currency"]}
         for s in stocks],
        ensure_ascii=False,
    )

    with st.spinner("자산 변동 추이 계산 중..."):
        _vh_dates, _vh_values = _calc_portfolio_history(_stocks_for_hist, _period_days)

    if _vh_dates and len(_vh_dates) >= 2:
        # 투자금 라인 계산 (매수일 <= 날짜인 종목의 매수금액 합산)
        _invest_values = []
        for d in _vh_dates:
            _inv_total = 0.0
            for s in stocks:
                if s["purchase_date"] <= d:
                    if s["purchase_currency"] == "KRW":
                        _inv_total += s["purchase_price"] * s["quantity"]
                    else:
                        _inv_total += s["purchase_price"] * s["quantity"] * s["purchase_exchange_rate"]
            _invest_values.append(_inv_total)

        _vh_change = _vh_values[-1] - _vh_values[0]
        _vh_change_pct = (_vh_change / _vh_values[0] * 100) if _vh_values[0] else 0
        _vh_jpy = jpy_rate if jpy_rate > 0 else 1
        _mc1, _mc2, _mc3, _mc4 = st.columns(4)
        with _mc1:
            st.metric("현재 평가금", f"¥{_vh_values[-1] / _vh_jpy:,.0f}")
            st.caption(f"₩{_vh_values[-1]:,.0f}")
        with _mc2:
            st.metric("기간 변동", f"¥{_vh_change / _vh_jpy:+,.0f}", f"{_vh_change_pct:+.2f}%")
            st.caption(f"₩{_vh_change:+,.0f}")
        with _mc3:
            st.metric("기간 최고", f"¥{max(_vh_values) / _vh_jpy:,.0f}")
            st.caption(f"₩{max(_vh_values):,.0f}")
        with _mc4:
            st.metric("기간 최저", f"¥{min(_vh_values) / _vh_jpy:,.0f}")
            st.caption(f"₩{min(_vh_values):,.0f}")

        _vh_color = "#2196F3" if _vh_values[-1] >= _vh_values[0] else "#f44336"
        _fig_vh = go.Figure()
        _fig_vh.add_trace(go.Scatter(
            x=_vh_dates, y=_vh_values,
            name="평가금액",
            mode="lines",
            line=dict(color=_vh_color, width=2.5),
            fill="tonexty" if _invest_values else "tozeroy",
            hovertemplate="<b>%{x}</b><br>평가: ₩%{y:,.0f}<extra></extra>",
        ))
        if _invest_values:
            # 투자금 라인을 먼저 그려야 fill="tonexty"가 제대로 작동
            _fig_vh.data = ()  # 초기화 후 순서 재배치
            _fig_vh.add_trace(go.Scatter(
                x=_vh_dates, y=_invest_values,
                name="투자금",
                mode="lines",
                line=dict(color="#9E9E9E", width=1.5, dash="dot"),
                hovertemplate="<b>%{x}</b><br>투자금: ₩%{y:,.0f}<extra></extra>",
            ))
            _fig_vh.add_trace(go.Scatter(
                x=_vh_dates, y=_vh_values,
                name="평가금액",
                mode="lines",
                line=dict(color=_vh_color, width=2.5),
                fill="tonexty",
                fillcolor="rgba(33,150,243,0.15)" if _vh_color == "#2196F3" else "rgba(244,67,54,0.15)",
                hovertemplate="<b>%{x}</b><br>평가: ₩%{y:,.0f}<extra></extra>",
            ))
        _fig_vh.update_layout(
            height=400,
            margin=dict(t=10, b=40, l=60, r=20),
            yaxis_title="금액 (KRW)",
            yaxis_tickformat=",",
            xaxis_title="",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
        )
        st.plotly_chart(_fig_vh, use_container_width=True)
    elif _vh_dates:
        st.info("데이터가 부족합니다. 보유 기간이 길어지면 그래프가 표시됩니다.")
    else:
        st.info("주가 히스토리를 가져올 수 없습니다. 종목을 추가하면 자동으로 표시됩니다.")

    st.markdown("---")

    # ── 차트 ──────────────────────────────────────────────────
    st.subheader("포트폴리오 비중")
    # 보유량 큰 순으로 정렬 (시계방향)
    _pie_items = sorted(items, key=lambda i: i["current_value_krw"], reverse=True)
    _pie_labels = [i["name"] for i in _pie_items]
    _pie_values = [i["current_value_krw"] for i in _pie_items]

    # 수익률 기반 색상: 양수=파란색(진할수록 높음), 음수=빨간색(진할수록 낮음)
    _pie_colors = []
    for i in _pie_items:
        pct = i["total_gain_pct"]
        if pct >= 0:
            # 파란 계열: 수익률 높을수록 진한 파랑
            intensity = min(1.0, pct / 50.0)  # 50%에서 최대 진하기
            r = int(200 - 140 * intensity)
            g = int(210 - 100 * intensity)
            b = int(255 - 30 * intensity)
            _pie_colors.append(f"rgb({r},{g},{b})")
        else:
            # 빨간 계열: 손실 클수록 진한 빨강
            intensity = min(1.0, abs(pct) / 50.0)
            r = int(255 - 30 * intensity)
            g = int(200 - 140 * intensity)
            b = int(200 - 140 * intensity)
            _pie_colors.append(f"rgb({r},{g},{b})")

    _n_items = len(_pie_labels)
    _pie_height = max(500, 450 + _n_items * 15)
    _pie_margin = max(100, 60 + _n_items * 8)
    fig = go.Figure(data=[go.Pie(
        labels=_pie_labels,
        values=_pie_values,
        hole=0.4,
        textinfo="label+percent",
        textposition="outside",
        pull=[0.02] * len(_pie_labels),
        marker=dict(colors=_pie_colors),
        sort=False,  # 이미 정렬했으므로 Plotly 자동 정렬 OFF
        direction="clockwise",
        hovertemplate="<b>%{label}</b><br>비중: %{percent}<extra></extra>",
        automargin=True,
    )])
    fig.update_layout(
        margin=dict(t=_pie_margin, b=_pie_margin, l=_pie_margin, r=_pie_margin),
        height=_pie_height,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    st.subheader("수익 구성 (주가 vs 환율)")
    fx_items = [i for i in items if i["currency"] != "KRW"]
    if fx_items:
        fig2 = go.Figure(data=[
            go.Bar(name="주가 수익 (¥)",
                   x=[i["ticker"] for i in fx_items],
                   y=[i["stock_gain_krw"] / jpy_rate for i in fx_items],
                   marker_color="#a6e3a1"),
            go.Bar(name="환율 수익 (¥)",
                   x=[i["ticker"] for i in fx_items],
                   y=[i["fx_gain_krw"] / jpy_rate for i in fx_items],
                   marker_color="#89b4fa"),
        ])
        fig2.update_layout(barmode="group", height=400,
                           margin=dict(t=20, b=40, l=60, r=20),
                           yaxis_title="JPY")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("외화 종목이 없어 환율 수익 비교를 표시할 수 없습니다.")

    st.markdown("---")

    # ── 배당금 섹션 ──────────────────────────────────────────────
    st.subheader("💰 배당금")

    _div_tab_info, _div_tab_auto, _div_tab_input, _div_tab_history = st.tabs([
        "📅 배당 일정 & 수익률", "🔍 배당 자동 감지", "✏️ 배당금 수동 입력", "📋 배당 이력"
    ])

    # ═══ 탭1: 배당 일정 & 수익률 (yfinance 자동) ═══════════════
    with _div_tab_info:
        st.caption("보유 종목의 배당 정보를 Yahoo Finance에서 자동 조회합니다.")
        import yfinance as _yf_div

        _div_data = []
        with st.spinner("배당 정보 조회 중..."):
            for item in items:
                try:
                    _tk = _yf_div.Ticker(item["ticker"])
                    _info = _tk.info

                    # 주식의 실제 통화 (yfinance에서 직접 가져옴)
                    _stock_cur = (_info.get("currency") or "USD").upper()
                    _stock_sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_stock_cur, "$")

                    # 배당수익률: yfinance는 이미 % 값으로 줌 (예: 1.81 = 1.81%)
                    _yield_raw = _info.get("dividendYield") or 0
                    _yield = _yield_raw if _yield_raw < 1 else _yield_raw  # 이미 % 값

                    _rate_annual = _info.get("dividendRate") or 0  # 주식 통화 기준

                    # dividendRate가 None인 종목 → 최근 배당 이력에서 연간 금액 추정
                    _divs = _tk.dividends
                    if _rate_annual <= 0 and not _divs.empty:
                        # 최근 1년치 배당 합산 or 최근 4건 합산
                        from datetime import timedelta as _td_div2
                        _one_year_ago = datetime.now() - _td_div2(days=400)
                        _recent = _divs[_divs.index >= str(_one_year_ago)]
                        if not _recent.empty:
                            _rate_annual = float(_recent.sum())
                        else:
                            _rate_annual = float(_divs.tail(4).sum())

                    _ex_date = _info.get("exDividendDate")
                    if _ex_date and isinstance(_ex_date, (int, float)):
                        from datetime import datetime as _dt_div
                        _ex_date = _dt_div.fromtimestamp(_ex_date).strftime("%Y-%m-%d")
                    elif not _ex_date:
                        _ex_date = "-"

                    # 최근 배당 이력 (_divs는 위에서 이미 조회됨)
                    _last_div = f"{_stock_sym}{float(_divs.iloc[-1]):,.2f}" if not _divs.empty else "-"
                    _last_date = _divs.index[-1].strftime("%Y-%m-%d") if not _divs.empty else "-"

                    # 예상 연간 배당 (엔화 변환)
                    if _rate_annual > 0 and jpy_rate > 0:
                        if _stock_cur == "JPY":
                            _est_annual_jpy = _rate_annual * item["quantity"]
                        elif _stock_cur == "USD":
                            _usd_krw = get_exchange_rate("USD")
                            _est_annual_jpy = _rate_annual * item["quantity"] * _usd_krw / jpy_rate
                        else:
                            _est_annual_jpy = _rate_annual * item["quantity"]
                        _est_str = f"¥{_est_annual_jpy:,.0f}"
                    else:
                        _est_annual_jpy = 0
                        _est_str = "-"

                    _div_data.append({
                        "종목": f"{item['name']} ({item['ticker']})",
                        "배당수익률": f"{_yield:.2f}%" if _yield > 0 else "-",
                        "연간배당금": f"{_stock_sym}{_rate_annual:,.2f}" if _rate_annual > 0 else "-",
                        "최근배당": _last_div,
                        "최근배당일": _last_date,
                        "배당락일": str(_ex_date),
                        "보유수량": f"{item['quantity']:.0f}주",
                        "예상연간(¥)": _est_str,
                    })
                except Exception:
                    _div_data.append({
                        "종목": f"{item['name']} ({item['ticker']})",
                        "배당수익률": "-", "연간배당금": "-",
                        "최근배당": "-", "최근배당일": "-",
                        "배당락일": "-", "보유수량": f"{item['quantity']:.0f}주",
                        "예상연간(¥)": "-",
                    })

        if _div_data:
            st.dataframe(pd.DataFrame(_div_data), use_container_width=True, hide_index=True)

            # 예상 연간 배당금 합계
            _total_est_div = sum(
                float(d["예상연간(¥)"].replace("¥","").replace(",",""))
                for d in _div_data if d["예상연간(¥)"] != "-"
            )
            if _total_est_div > 0:
                st.info(f"💰 예상 연간 배당 수입: **¥{_total_est_div:,.0f}**")

    # ═══ 탭2: 배당 자동 감지 ═══════════════════════════════════
    with _div_tab_auto:
        st.caption("보유 종목의 실제 배당 히스토리를 조회해서, 보유 기간 중 받았어야 할 배당을 자동으로 찾습니다.")

        # session_state로 감지 결과 유지
        if "div_auto_missing" not in st.session_state:
            st.session_state.div_auto_missing = None

        if st.button("🔍 배당 자동 감지 실행", type="primary", key="div_auto_detect"):
            import yfinance as _yf_div_auto
            _all_stocks_raw = get_all_stocks()
            _existing_divs = get_dividends()

            _existing_set = set()
            for _ed in _existing_divs:
                _existing_set.add((_ed["ticker"].upper(), _ed["payment_date"]))

            _holdings_by_ticker = {}
            for _s in _all_stocks_raw:
                _tk = _s["ticker"].upper()
                if _tk not in _holdings_by_ticker:
                    _holdings_by_ticker[_tk] = []
                _holdings_by_ticker[_tk].append({
                    "purchase_date": _s["purchase_date"],
                    "quantity": _s["quantity"],
                    "currency": _s["purchase_currency"],
                    "broker": _s.get("broker", ""),
                    "account_type": _s.get("account_type", "일반계좌"),
                    "name": _s["name"],
                })

            _missing_divs = []
            _found_count = 0

            with st.spinner("보유 종목 배당 히스토리 조회 중..."):
                for _tk, _lots in _holdings_by_ticker.items():
                    try:
                        _t = _yf_div_auto.Ticker(_tk)
                        _divs = _t.dividends
                        if _divs is None or _divs.empty:
                            continue
                        _stock_cur = (_t.info.get("currency") or "USD").upper()
                        _stock_sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_stock_cur, "$")

                        if _divs.index.tzinfo is not None:
                            _divs.index = _divs.index.tz_localize(None)

                        for _div_date, _div_amt in _divs.items():
                            _div_date_str = _div_date.strftime("%Y-%m-%d")
                            _found_count += 1

                            if (_tk, _div_date_str) in _existing_set:
                                continue

                            _qty = sum(
                                lot["quantity"] for lot in _lots
                                if lot["purchase_date"] <= _div_date_str
                            )
                            if _qty <= 0:
                                continue

                            _missing_divs.append({
                                "ticker": _tk,
                                "name": _lots[0]["name"],
                                "date": _div_date_str,
                                "amount_per_share": float(_div_amt),
                                "quantity": _qty,
                                "total": float(_div_amt) * _qty,
                                "currency": _stock_cur,
                                "symbol": _stock_sym,
                                "broker": _lots[0]["broker"],
                                "account_type": _lots[0]["account_type"],
                            })
                    except Exception:
                        continue

            _missing_divs.sort(key=lambda x: x["date"], reverse=True)
            st.session_state.div_auto_missing = _missing_divs
            st.session_state.div_auto_found = _found_count

        # 감지 결과 표시
        _missing = st.session_state.div_auto_missing
        if _missing is not None:
            if _missing:
                st.warning(f"📌 **{len(_missing)}건**의 미등록 배당을 발견했습니다!")

                _preview_data = []
                for _md in _missing:
                    _preview_data.append({
                        "지급일": _md["date"],
                        "종목": f"{_md['name']} ({_md['ticker']})",
                        "1주당": f"{_md['symbol']}{_md['amount_per_share']:,.4f}",
                        "보유수량": f"{_md['quantity']:.0f}주",
                        "배당합계": f"{_md['symbol']}{_md['total']:,.2f}",
                        "계좌": _md["account_type"],
                    })
                st.dataframe(pd.DataFrame(_preview_data), use_container_width=True, hide_index=True)

                if st.button(f"✅ {len(_missing)}건 전체 배당 일괄 등록", type="primary", key="div_auto_register"):
                    _reg_count = 0
                    with st.spinner("배당 등록 중..."):
                        for _md in _missing:
                            _cur = _md["currency"]
                            if _cur != "KRW":
                                try:
                                    _fx, _ = get_historical_exchange_rate(_cur, _md["date"])
                                except Exception:
                                    _fx = get_exchange_rate(_cur)
                            else:
                                _fx = 1.0
                            _total_krw = _md["total"] * _fx
                            add_dividend(
                                ticker=_md["ticker"],
                                name=_md["name"],
                                payment_date=_md["date"],
                                amount_per_share=_md["amount_per_share"],
                                currency=_cur,
                                exchange_rate=_fx,
                                quantity=_md["quantity"],
                                total_amount=_md["total"],
                                total_amount_krw=_total_krw,
                                broker=_md["broker"],
                                account_type=_md["account_type"],
                                notes="자동 감지",
                            )
                            _reg_count += 1
                    st.session_state.div_auto_missing = None
                    st.success(f"✅ {_reg_count}건 배당 등록 완료! '📋 배당 이력' 탭에서 확인하세요.")
                    st.rerun()
            elif st.session_state.get("div_auto_found", 0) > 0:
                st.success("✅ 모든 배당이 이미 등록되어 있습니다!")
            else:
                st.info("배당 이력이 있는 종목이 없습니다.")

    # ═══ 탭3: 배당금 수동 입력 ═══════════════════════════════════
    with _div_tab_input:
        st.caption("받은 배당금을 입력하면 확정 수익에 자동 합산됩니다.")

        _div_stocks = get_all_stocks()
        if not _div_stocks:
            st.info("보유 종목이 없습니다.")
        else:
            _div_seen, _div_unique = set(), []
            for _s in sorted(_div_stocks, key=lambda x: x["ticker"]):
                if _s["ticker"] not in _div_seen:
                    _div_seen.add(_s["ticker"])
                    _div_unique.append(_s)
            _div_opts = [f"{_s['ticker']} — {_s['name']}" for _s in _div_unique]

            with st.form("div_input_form", clear_on_submit=True):
                _dc1, _dc2, _dc3 = st.columns(3)
                with _dc1:
                    _div_sel = st.selectbox("종목", _div_opts)
                    _div_ticker = _div_sel.split(" — ")[0] if _div_sel else ""
                    _div_stock = next((_s for _s in _div_unique if _s["ticker"] == _div_ticker), None)
                    _div_cur = _div_stock["purchase_currency"] if _div_stock else "USD"
                    _div_sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_div_cur, "$")
                    _div_amt = st.number_input(f"1주당 배당금 ({_div_sym})", min_value=0.0, step=0.01)
                with _dc2:
                    _div_date = st.date_input("배당 지급일", value=date.today())
                    _div_qty = st.number_input("배당 적용 수량",
                        min_value=0.0001,
                        value=float(_div_stock["quantity"]) if _div_stock else 1.0,
                        step=1.0)
                with _dc3:
                    _div_notes = st.text_input("메모", value="")
                    _div_rate_ovr = st.number_input(
                        "환율 (0=자동)", min_value=0.0, value=0.0, step=0.1)

                if _div_amt > 0 and _div_qty > 0:
                    _div_total = _div_amt * _div_qty
                    if _div_cur != "KRW":
                        _div_fx = _div_rate_ovr if _div_rate_ovr > 0 else get_exchange_rate(_div_cur)
                    else:
                        _div_fx = 1.0
                    _div_total_krw = _div_total * _div_fx
                    _div_total_jpy = _div_total_krw / jpy_rate if jpy_rate > 0 else _div_total_krw
                    st.info(f"💰 배당금: {_div_sym}{_div_total:,.2f} (¥{_div_total_jpy:,.0f})")

                if st.form_submit_button("✅ 배당금 등록", type="primary"):
                    if _div_amt <= 0:
                        st.error("배당금을 입력하세요.")
                    else:
                        _div_total = _div_amt * _div_qty
                        if _div_cur != "KRW":
                            _div_fx = _div_rate_ovr if _div_rate_ovr > 0 else get_exchange_rate(_div_cur)
                        else:
                            _div_fx = 1.0
                        _div_total_krw = _div_total * _div_fx
                        add_dividend(
                            ticker=_div_ticker,
                            name=_div_stock["name"] if _div_stock else _div_ticker,
                            payment_date=str(_div_date),
                            amount_per_share=_div_amt,
                            currency=_div_cur,
                            exchange_rate=_div_fx,
                            quantity=_div_qty,
                            total_amount=_div_total,
                            total_amount_krw=_div_total_krw,
                            broker=_div_stock.get("broker","") if _div_stock else "",
                            account_type=_div_stock.get("account_type","일반계좌") if _div_stock else "일반계좌",
                            notes=_div_notes,
                        )
                        st.success(f"✅ 배당금 등록 완료! {_div_sym}{_div_total:,.2f}")
                        st.rerun()

    # ═══ 탭3: 배당 이력 ═══════════════════════════════════════
    with _div_tab_history:
        _all_divs = get_dividends()
        if not _all_divs:
            st.info("등록된 배당금이 없습니다.")
        else:
            _div_hist_data = []
            _div_hist_total_krw = 0
            for _d in _all_divs:
                _sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_d.get("currency","USD"), "$")
                _tkrw = _d.get("total_amount_krw") or 0
                _div_hist_total_krw += _tkrw
                _tjpy = _tkrw / jpy_rate if jpy_rate > 0 else _tkrw
                _div_hist_data.append({
                    "지급일": _d["payment_date"],
                    "종목": f"{_d.get('name','')} ({_d['ticker']})",
                    "1주당": f"{_sym}{_d['amount_per_share']:,.3f}",
                    "수량": f"{_d['quantity']:.0f}주",
                    "합계": f"{_sym}{_d['total_amount']:,.2f}",
                    "엔화": f"¥{_tjpy:,.0f}",
                    "메모": _d.get("notes",""),
                    "_id": _d["id"],
                })
            st.dataframe(
                pd.DataFrame(_div_hist_data).drop(columns=["_id"]),
                use_container_width=True, hide_index=True,
            )
            _div_hist_jpy = _div_hist_total_krw / jpy_rate if jpy_rate > 0 else _div_hist_total_krw
            st.success(f"💰 누적 배당 수입: **¥{_div_hist_jpy:,.0f}**")

            with st.expander("배당 이력 삭제"):
                for _dh in _div_hist_data:
                    if st.checkbox(f"{_dh['지급일']} | {_dh['종목']} | {_dh['합계']}", key=f"del_div_{_dh['_id']}"):
                        if st.button("삭제", key=f"do_del_div_{_dh['_id']}"):
                            delete_dividend(_dh["_id"])
                            st.rerun()


# ══════════════════════════════════════════════════════════════
# 페이지 2: 종목 추가/관리
# ══════════════════════════════════════════════════════════════
elif page == "종목 추가/관리":
    st.title("➕ 종목 추가/관리")

    # ── 상수 정의 ─────────────────────────────────────────────
    ACCOUNT_TYPES = [
        "일반계좌",
        "특정계좌 (원천징수)",
        "특정계좌 (확정신고)",
        "NISA (성장투자)",
        "NISA (적립투자)",
    ]
    TAX_RATE = {
        "일반계좌": 0.20315,
        "특정계좌 (원천징수)": 0.20315,
        "특정계좌 (확정신고)": 0.20315,
        "NISA (성장투자)": 0.0,
        "NISA (적립투자)": 0.0,
    }
    NISA_ACCOUNT_TYPES = {"NISA (성장투자)", "NISA (적립투자)"}

    # 포트폴리오 그룹
    PORTFOLIO_GROUPS = ["개별주식", "積立NISA"]

    # 증권사 목록 (국가별 구분)
    BROKER_LIST = [
        "",                  # 미설정
        # 일본
        "라쿠텐증권 (JP)",
        "SBI증권 (JP)",
        "마네ックス증권 (JP)",
        # 한국
        "토스증권 (KR)",
        "키움증권 (KR)",
        "미래에셋증권 (KR)",
        "삼성증권 (KR)",
        "NH투자증권 (KR)",
        "신한투자증권 (KR)",
        # 미국
        "Charles Schwab (US)",
        "Fidelity (US)",
        "Interactive Brokers (US)",
        "기타",
    ]

    # ── session_state 초기화 ───────────────────────────────────
    if "stock_preview" not in st.session_state:
        st.session_state.stock_preview = None
    if "screenshot_confirmed" not in st.session_state:
        st.session_state.screenshot_confirmed = {}
    if "sell_target_id" not in st.session_state:
        st.session_state.sell_target_id = None
    if "bulk_action" not in st.session_state:
        st.session_state.bulk_action = "선택하세요"
    if "avg_cost_preview" not in st.session_state:
        st.session_state.avg_cost_preview = None
    if "transaction_parsed" not in st.session_state:
        st.session_state.transaction_parsed = []

    # ══ 매매 내역 스크린샷 (매수+매도 통합) ════════════════════
    with st.expander("📷 매매 내역 스크린샷 (매수·매도 자동 감지)", expanded=False):
        st.caption(
            "증권사 앱의 매매 내역 스크린샷을 올리면 Claude AI가 **매수와 매도를 자동으로 구분**해서 읽습니다.  \n"
            "매도는 같은 종목·같은 증권사의 기존 매수 항목에서 **이동평균법**으로 실현손익을 계산하고, "
            "**오래된 로트부터 FIFO로 차감**됩니다."
        )

        # ── 헬퍼: 이동평균 매도 손익 계산 ──────────────────────
        def _calc_sell_moving_avg(ticker, broker, sell_qty, sell_price, sell_currency, sell_rate):
            """같은 ticker+broker 의 포트폴리오 항목들을 이동평균법으로 매도 처리."""
            _all_s = get_all_stocks()
            _matching = [
                _s for _s in _all_s
                if _s["ticker"].upper() == ticker.upper()
                and _s.get("broker", "") == broker
            ]
            if not _matching:
                return {"error": f"{ticker} ({broker or '미설정'}) 포트폴리오에 매수 내역이 없습니다."}

            _tot_qty = sum(_s["quantity"] for _s in _matching)
            if sell_qty > _tot_qty + 0.0001:
                return {"error": f"매도 수량 {sell_qty:g} > 보유 수량 {_tot_qty:g}"}

            _tot_cost_orig = sum(_s["purchase_price"] * _s["quantity"] for _s in _matching)
            _mov_avg_price = _tot_cost_orig / _tot_qty if _tot_qty > 0 else 0
            _tot_cost_krw = sum(_s["purchase_price"] * _s["quantity"] * _s["purchase_exchange_rate"]
                                for _s in _matching)
            _mov_avg_rate = (_tot_cost_krw / _tot_cost_orig) if _tot_cost_orig > 0 else 1.0

            _realized_orig = (sell_price - _mov_avg_price) * sell_qty
            _sell_val_krw  = sell_price * sell_qty * sell_rate
            _cost_bas_krw  = _mov_avg_price * sell_qty * _mov_avg_rate
            _realized_krw  = _sell_val_krw - _cost_bas_krw
            _realized_pct  = (_realized_orig / (_mov_avg_price * sell_qty) * 100) if _mov_avg_price > 0 else 0

            _lot_plan = []
            _remaining = sell_qty
            _sorted = sorted(_matching, key=lambda x: (x.get("purchase_date", "") or "", x.get("id", 0)))
            for _s in _sorted:
                if _remaining <= 0:
                    break
                _take = min(_s["quantity"], _remaining)
                _lot_plan.append({
                    "id": _s["id"],
                    "old_qty": _s["quantity"],
                    "new_qty": _s["quantity"] - _take,
                    "sold": _take,
                    "purchase_date":  _s.get("purchase_date", ""),
                    "purchase_price": _s["purchase_price"],
                })
                _remaining -= _take

            return {
                "error": None,
                "moving_avg_price":    _mov_avg_price,
                "moving_avg_rate":     _mov_avg_rate,
                "realized_gain_orig":  _realized_orig,
                "realized_gain_krw":   _realized_krw,
                "realized_gain_pct":   _realized_pct,
                "lot_plan":            _lot_plan,
                "total_available":     _tot_qty,
            }

        def _execute_sell(ticker, name, broker, account_type, sell_qty, sell_price,
                          sell_currency, sell_rate, sell_date, notes, calc_result):
            add_sold_record(
                ticker=ticker, name=name, broker=broker, account_type=account_type,
                purchase_date=calc_result["lot_plan"][0]["purchase_date"] if calc_result["lot_plan"] else "",
                purchase_price=calc_result["moving_avg_price"],
                purchase_currency=sell_currency,
                purchase_exchange_rate=calc_result["moving_avg_rate"],
                sell_date=sell_date, sell_price=sell_price,
                sell_currency=sell_currency, sell_exchange_rate=sell_rate,
                quantity=sell_qty,
                realized_gain_krw=calc_result["realized_gain_krw"],
                realized_gain_pct=calc_result["realized_gain_pct"],
                notes=notes or f"이동평균법 매도 (평단 {calc_result['moving_avg_price']:,.2f})",
            )
            for _lot in calc_result["lot_plan"]:
                reduce_stock_quantity(_lot["id"], _lot["new_qty"])

        # ── 탭: 스크린샷 · 매도 수동 입력 ───────────────────────
        _ut_shot, _ut_manual = st.tabs(["📷 스크린샷 업로드", "✏️ 매도 수동 입력"])

        # ═══ 스크린샷 탭 ═══════════════════════════════════════
        with _ut_shot:
            _has_api = bool(os.getenv("ANTHROPIC_API_KEY", ""))
            if not _has_api:
                st.warning("⚙️ 설정 메뉴에서 **Anthropic API 키**를 먼저 입력하세요.")
            else:
                _u = st.file_uploader(
                    "매매 스크린샷 (PNG / JPG / WEBP)",
                    type=["png", "jpg", "jpeg", "webp"],
                    key="tx_screenshot_uploader",
                )
                if _u is not None:
                    _ci, _cb = st.columns([4, 1])
                    with _ci:
                        st.image(_u, use_column_width=True)
                    with _cb:
                        st.write(""); st.write("")
                        if st.button("🔍 분석 시작", type="primary", use_container_width=True, key="tx_parse_btn"):
                            _ext = _u.name.rsplit(".", 1)[-1].lower()
                            _mm = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                                   "png": "image/png", "webp": "image/webp"}
                            _mt = _mm.get(_ext, "image/png")
                            with st.spinner("AI가 매매 내역을 읽고 있습니다..."):
                                _p = parse_transaction_screenshot(_u.read(), _mt)
                            if _p and "_error" in _p[0]:
                                _err, _raw = _p[0]["_error"], _p[0].get("_raw", "")
                                if "credit balance" in _err or "402" in _err:
                                    st.error("💳 **Anthropic API 크레딧이 부족합니다.**\n\n👉 https://console.anthropic.com/settings/billing")
                                elif "invalid_api_key" in _err or "401" in _err:
                                    st.error("🔑 **API 키가 올바르지 않습니다.**")
                                elif "JSON 파싱 실패" in _err:
                                    st.warning("⚠️ AI가 화면을 읽었지만 형식 변환에 실패했습니다.")
                                    with st.expander("🔍 AI가 읽은 내용 보기"):
                                        st.text(_raw[:1500] if _raw else "(응답 없음)")
                                else:
                                    st.error(f"오류: {_err}")
                                    if _raw:
                                        with st.expander("🔍 AI 응답 보기"):
                                            st.text(_raw[:1500])
                            elif not _p:
                                st.warning("매매 내역을 찾지 못했습니다. 스크린샷을 다시 확인해주세요.")
                            else:
                                # 티커 또는 종목명이 있으면 유효 (티커 없어도 사용자가 수동 입력 가능)
                                _valid = [x for x in _p
                                          if str(x.get("ticker", "")).strip() or str(x.get("name", "")).strip()]
                                if not _valid:
                                    st.warning("종목 정보를 찾지 못했습니다.")
                                else:
                                    # 원본 데이터 보존 (자동 계산 전 — 중복 체크에 사용)
                                    for x in _valid:
                                        x["_orig_price"] = x.get("price", 0)
                                        x["_orig_date"]  = x.get("date", "")

                                    # 종목명 영어 정규화
                                    with st.spinner("종목명 영어로 정규화 중..."):
                                        for x in _valid:
                                            tkr = x.get("ticker", "").strip()
                                            if tkr:
                                                try:
                                                    info = get_stock_info(tkr)
                                                    en = info.get("name", "")
                                                    if en and en != tkr:
                                                        x["name"] = en
                                                except Exception:
                                                    pass

                                    # 매수 항목만 연도 보정
                                    import re as _re_yr2
                                    def _needs_yr_fix(d):
                                        if not d or d.startswith("XXXX-"):
                                            return True
                                        try:
                                            return date.fromisoformat(d) > date.today()
                                        except Exception:
                                            return True

                                    _yr_fix = [
                                        x for x in _valid
                                        if x.get("transaction_type", "buy") == "buy"
                                        and x.get("ticker") and float(x.get("price", 0)) > 0
                                        and _needs_yr_fix(x.get("date", ""))
                                    ]
                                    if _yr_fix:
                                        with st.spinner(f"📅 {len(_yr_fix)}건 주가 비교로 매수 연도 추정 중..."):
                                            for x in _yr_fix:
                                                d_raw = x.get("date", "")
                                                tkr_ = x.get("ticker", "")
                                                pr   = float(x.get("price", 0))
                                                m_md = _re_yr2.search(r'(?:XXXX-)?(\d{1,2})-(\d{1,2})$', d_raw)
                                                if m_md:
                                                    mm, dd = int(m_md.group(1)), int(m_md.group(2))
                                                else:
                                                    mm, dd = date.today().month, date.today().day
                                                try:
                                                    det_y, diff_p = _determine_year_by_price(tkr_, pr, mm, dd)
                                                    x["date"] = f"{det_y}-{mm:02d}-{dd:02d}"
                                                    x["_year_det_pct"] = diff_p
                                                except Exception:
                                                    x["date"] = f"{date.today().year}-{mm:02d}-{dd:02d}"

                                    # ── 소수점 매매: 수량/단가 불명확 시 자동 계산 ──
                                    import yfinance as _yf_auto

                                    def _extract_amount_from_text(text):
                                        """메모/노트에서 금액 추출 (예: '총 구매금액 99,892원' → 99892)"""
                                        if not text:
                                            return 0.0
                                        m = _re_yr2.search(r'([\d,]+(?:\.\d+)?)\s*[원₩]', text)
                                        if m:
                                            return float(m.group(1).replace(",", ""))
                                        m2 = _re_yr2.search(r'[\$\¥]\s*([\d,]+(?:\.\d+)?)', text)
                                        if m2:
                                            return float(m2.group(1).replace(",", ""))
                                        m3 = _re_yr2.search(r'([\d,]{3,}(?:\.\d+)?)', text)
                                        if m3:
                                            return float(m3.group(1).replace(",", ""))
                                        return 0.0

                                    # ── 원본 날짜 보존 (XXXX/빈 날짜 → 나중에 티커 설정 후 재추정 가능) ──
                                    for x in _valid:
                                        x["_original_date"] = x.get("date", "")

                                    # ── notes에서 금액 추출 → price 자동 설정 ──
                                    # 투자신탁 등 price=0이지만 notes에 "10,000円" 같은 금액이 있는 경우
                                    import re as _re_notes_amt
                                    for x in _valid:
                                        _xprice = float(x.get("price") or 0)
                                        if _xprice <= 0:
                                            _notes_text = x.get("notes", "")
                                            _amt_m = _re_notes_amt.search(r'([\d,]+)\s*[円¥]', _notes_text)
                                            if _amt_m:
                                                _amt_val = float(_amt_m.group(1).replace(",", ""))
                                                if _amt_val > 0:
                                                    x["price"] = _amt_val
                                                    x["quantity"] = x.get("quantity") or 1
                                                    x["notes"] = _notes_text + " (총액→단가 자동변환)"

                                    # Case A: qty=0, price=총금액 (AI가 총금액을 price에 넣은 경우)
                                    # Case B: price=0, qty=기본값, 총금액이 메모에 있는 경우
                                    _qty_fix = []
                                    for x in _valid:
                                        if not x.get("ticker"):
                                            continue
                                        # 날짜 없으면 오늘 날짜로 대체
                                        if not x.get("date") or x.get("date", "").startswith("XXXX"):
                                            x["date"] = str(date.today())
                                        _xqty = float(x.get("quantity") or 0)
                                        _xprice = float(x.get("price") or 0)
                                        _xnotes = x.get("notes", "")

                                        if _xqty < 0.001 and _xprice > 0:
                                            # Case A: 수량 없음, 가격에 총금액이 들어있음
                                            x["_total_amount"] = _xprice
                                            _qty_fix.append(x)
                                        elif _xprice <= 0:
                                            # Case B: 단가 없음 → 메모에서 금액 추출
                                            _amt = _extract_amount_from_text(_xnotes)
                                            if _amt <= 0:
                                                _amt = _extract_amount_from_text(x.get("name", ""))
                                            if _amt > 0:
                                                x["_total_amount"] = _amt
                                                _qty_fix.append(x)

                                    if _qty_fix:
                                        with st.spinner(f"📊 {len(_qty_fix)}건 수량 불명확 → 주가 조회 후 자동 계산 중..."):
                                            from datetime import timedelta as _td_auto
                                            for x in _qty_fix:
                                                try:
                                                    _tkr = x["ticker"]
                                                    _total_amt = x["_total_amount"]
                                                    _dt_str = x["date"]
                                                    _dt_obj = datetime.strptime(_dt_str, "%Y-%m-%d")
                                                    _start = (_dt_obj - _td_auto(days=7)).strftime("%Y-%m-%d")
                                                    _end   = (_dt_obj + _td_auto(days=3)).strftime("%Y-%m-%d")

                                                    _tk_obj = _yf_auto.Ticker(_tkr)
                                                    _hist = _tk_obj.history(start=_start, end=_end)
                                                    if _hist.empty:
                                                        continue
                                                    if _hist.index.tzinfo is not None:
                                                        _hist.index = _hist.index.tz_localize(None)
                                                    _before = _hist[_hist.index.date <= _dt_obj.date()]
                                                    _row = _before.iloc[-1] if not _before.empty else _hist.iloc[0]
                                                    _per_share = float(_row["Close"])

                                                    # 통화 변환: 주가 통화 ≠ 투자 통화 (예: TSLA=$250, 투자=₩99,892)
                                                    _stock_cur = "USD"
                                                    try:
                                                        _stock_cur = (_tk_obj.info.get("currency") or "USD").upper()
                                                    except Exception:
                                                        if _tkr.endswith(".T"):
                                                            _stock_cur = "JPY"
                                                        elif _tkr.endswith(".KS") or _tkr.endswith(".KQ"):
                                                            _stock_cur = "KRW"

                                                    _inv_cur = x.get("currency", "JPY")
                                                    if _stock_cur != _inv_cur and _inv_cur != "KRW":
                                                        # 주가 통화 → 투자 통화 변환 불필요 (같은 통화)
                                                        _price_in_inv = _per_share
                                                    elif _stock_cur != _inv_cur:
                                                        # 예: TSLA(USD) 투자(KRW) → USD→KRW 환율 필요
                                                        _fx, _ = get_historical_exchange_rate(_stock_cur, _dt_str)
                                                        _price_in_inv = _per_share * _fx
                                                    else:
                                                        _price_in_inv = _per_share

                                                    if _price_in_inv > 0 and _total_amt / _price_in_inv < _total_amt:
                                                        # 총 금액 / 주당 가격 = 수량
                                                        _auto_qty = _total_amt / _price_in_inv
                                                        x["quantity"] = round(_auto_qty, 6)
                                                        x["price"] = _price_in_inv
                                                        x["_qty_auto"] = True
                                                        x["_qty_auto_info"] = (
                                                            f"{_tkr} {_dt_str} 종가 "
                                                            f"{'$' if _stock_cur=='USD' else '¥' if _stock_cur=='JPY' else '₩'}"
                                                            f"{_per_share:,.2f}"
                                                            + (f" × 환율 {_fx:,.2f}" if _stock_cur != _inv_cur and _inv_cur == "KRW" else "")
                                                        )
                                                except Exception:
                                                    continue

                                    # 이전 파싱 결과의 위젯 캐시 초기화 (Streamlit이 이전 값을 유지하는 문제 방지)
                                    for _old_k in list(st.session_state.keys()):
                                        if _old_k.startswith("tx_"):
                                            del st.session_state[_old_k]

                                    # 파싱된 값을 위젯 키에 직접 설정 (Streamlit이 value= 무시하는 문제 방지)
                                    for _vi, _vx in enumerate(_valid):
                                        if _vx.get("ticker"):
                                            st.session_state[f"tx_tk_{_vi}"] = _vx["ticker"]
                                        if _vx.get("name"):
                                            st.session_state[f"tx_nm_{_vi}"] = _vx["name"]

                                    st.session_state.transaction_parsed = _valid
                                    _n_b = sum(1 for x in _valid if x.get("transaction_type", "buy") == "buy")
                                    _n_s = sum(1 for x in _valid if x.get("transaction_type") == "sell")
                                    _n_qf = sum(1 for x in _valid if x.get("_qty_auto"))
                                    _msg = f"✅ {len(_valid)}건 감지됨 — 📈매수 {_n_b}건 · 📉매도 {_n_s}건"
                                    if _yr_fix:
                                        _msg += f"  (📅 매수 {len(_yr_fix)}건 연도 자동 추정)"
                                    if _n_qf:
                                        _msg += f"  (📊 소수점 {_n_qf}건 수량 자동 계산)"
                                    st.success(_msg)

                # ── 파싱 결과 표시 및 등록 ───────────────────────
                _tx_list = st.session_state.get("transaction_parsed", [])
                if _tx_list:
                    st.markdown("---")
                    st.markdown("**감지된 매매 내역 — 매수(📈) / 매도(📉) 자동 구분**")

                    # ── 티커 검색으로 일괄 설정 ──────────────
                    st.markdown("---")
                    st.markdown("**🔍 티커 검색 → 전체 항목에 일괄 적용**")
                    st.caption("검색한 티커/종목명을 모든 항목에 일괄 적용합니다.")
                    _bulk_mode = st.radio(
                        "검색 방식",
                        ["종목 이름으로 검색", "종목번호 직접 입력 (한국)", "티커/펀드코드 직접 입력"],
                        horizontal=True, key="tx_bulk_mode",
                    )
                    _bulk_found_ticker = None
                    _bulk_found_name = None

                    if _bulk_mode == "종목 이름으로 검색":
                        _name_q = st.text_input(
                            "🔍 종목 이름 검색",
                            placeholder="예: KODEX S&P, TIGER 나스닥100, PLUS 글로벌희토, 삼성전자",
                            key="tx_name_search_bulk",
                        )
                        if _name_q and len(_name_q.strip()) >= 2:
                            try:
                                import yfinance as _yf_ns
                                _ns_sr = _yf_ns.Search(_name_q.strip(), max_results=10)
                                if _ns_sr.quotes:
                                    _ns_opts = [
                                        f"{q['symbol']} — {q.get('shortname','') or q.get('longname','')}"
                                        for q in _ns_sr.quotes
                                    ]
                                    _ns_sel = st.selectbox(
                                        "검색 결과에서 선택", _ns_opts,
                                        key="tx_name_search_sel",
                                    )
                                    if _ns_sel:
                                        _bulk_found_ticker = _ns_sel.split(" — ")[0]
                                        _bulk_found_name = _ns_sel.split(" — ")[1] if " — " in _ns_sel else _bulk_found_ticker
                                else:
                                    st.warning("검색 결과가 없습니다. 다른 키워드로 시도해보세요.")
                            except Exception:
                                st.error("검색 오류 — 다른 키워드로 시도해보세요.")
                    elif _bulk_mode == "티커/펀드코드 직접 입력":
                        _dc1, _dc2 = st.columns(2)
                        with _dc1:
                            _direct_ticker = st.text_input(
                                "티커 또는 펀드 코드",
                                placeholder="예: 9I315216, 0331418A, AAPL, 7203.T",
                                key="tx_direct_ticker_bulk",
                            )
                        with _dc2:
                            _direct_name = st.text_input(
                                "종목명 (선택)",
                                placeholder="예: 楽天・資産づくりファンド",
                                key="tx_direct_name_bulk",
                            )
                        if _direct_ticker and _direct_ticker.strip():
                            _bulk_found_ticker = _direct_ticker.strip()
                            _bulk_found_name = _direct_name.strip() if _direct_name and _direct_name.strip() else _bulk_found_ticker
                            st.success(f"✅ **{_bulk_found_ticker}** — {_bulk_found_name}")
                    else:
                        _kr_code = st.text_input(
                            "종목번호 입력 (6자리 숫자)",
                            placeholder="예: 415920, 069500, 379800",
                            key="tx_kr_code_bulk",
                        )
                        if _kr_code and len(_kr_code.strip()) >= 5:
                            _code = _kr_code.strip()
                            import yfinance as _yf_kr
                            for _suffix in [".KS", ".KQ"]:
                                try:
                                    _tk_try = f"{_code}{_suffix}"
                                    _t_kr = _yf_kr.Ticker(_tk_try)
                                    _n_kr = _t_kr.info.get("longName") or _t_kr.info.get("shortName")
                                    if _n_kr:
                                        _bulk_found_ticker = _tk_try
                                        _bulk_found_name = _n_kr
                                        break
                                except Exception:
                                    continue
                            if not _bulk_found_ticker:
                                st.warning(f"'{_code}' 종목을 찾을 수 없습니다.")

                    if _bulk_found_ticker:
                        st.success(f"✅ **{_bulk_found_ticker}** — {_bulk_found_name}")
                        # 통화 자동 감지
                        _auto_currency = None
                        if _bulk_found_ticker.endswith(".KS") or _bulk_found_ticker.endswith(".KQ"):
                            _auto_currency = "KRW"
                        elif _bulk_found_ticker.endswith(".T"):
                            _auto_currency = "JPY"
                        if st.button("📌 모든 항목에 티커/종목명 일괄 적용", type="primary", key="tx_bulk_apply"):
                            import re as _re_bulk
                            with st.spinner("티커/종목명 적용 및 날짜 재추정 중..."):
                                for _idx, x in enumerate(_tx_list):
                                    x["ticker"] = _bulk_found_ticker
                                    x["name"] = _bulk_found_name
                                    if _auto_currency:
                                        x["currency"] = _auto_currency
                                    # 위젯 키에 직접 새 값 설정
                                    st.session_state[f"tx_tk_{_idx}"] = _bulk_found_ticker
                                    st.session_state[f"tx_nm_{_idx}"] = _bulk_found_name
                                    if _auto_currency:
                                        st.session_state[f"tx_cu_{_idx}"] = _auto_currency

                                    # ── 날짜 재추정: 원본 날짜가 XXXX/빈값이었으면 티커로 연도 추정 ──
                                    _orig_d = x.get("_original_date", "")
                                    _price = float(x.get("price") or 0)
                                    if _orig_d and _orig_d.startswith("XXXX") and _price > 0:
                                        _m_md = _re_bulk.search(r'(?:XXXX-)?(\d{1,2})-(\d{1,2})$', _orig_d)
                                        if _m_md:
                                            _mm, _dd = int(_m_md.group(1)), int(_m_md.group(2))
                                            try:
                                                _det_y, _det_p = _determine_year_by_price(
                                                    _bulk_found_ticker, _price, _mm, _dd
                                                )
                                                _new_date = f"{_det_y}-{_mm:02d}-{_dd:02d}"
                                                x["date"] = _new_date
                                                x["_year_det_pct"] = _det_p
                                                st.session_state[f"tx_dt_{_idx}"] = date.fromisoformat(_new_date)
                                            except Exception:
                                                _new_date = f"{date.today().year}-{_mm:02d}-{_dd:02d}"
                                                x["date"] = _new_date
                                                try:
                                                    st.session_state[f"tx_dt_{_idx}"] = date.fromisoformat(_new_date)
                                                except Exception:
                                                    pass
                            st.session_state.transaction_parsed = _tx_list
                            st.rerun()
                    st.markdown("---")

                    _existing_tx = get_all_stocks()
                    import re as _re_dup2
                    def _norm_dt(d):
                        if not d:
                            return ""
                        m = _re_dup2.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', str(d).strip())
                        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else str(d).strip()

                    def _is_dup_buy(row):
                        tk = str(row.get("ticker", "")).upper().strip()
                        # 날짜: 연도 보정 후 값 사용 (XXXX→2025 등), 가격: 원본 값 사용 (소수점 변환 전)
                        dt = _norm_dt(row.get("date", ""))
                        pr = float(row.get("_orig_price") or row.get("price") or 0)
                        if not tk or not dt or pr <= 0:
                            return False
                        for ex in _existing_tx:
                            ex_tk = str(ex.get("ticker", "")).upper()
                            ex_dt = _norm_dt(ex.get("purchase_date", ""))
                            ex_pr = float(ex.get("purchase_price") or 0)
                            if ex_tk != tk or ex_dt != dt or ex_pr <= 0:
                                continue
                            # 같은 티커 + 같은 날짜 + 같은 가격 (0.1% 이내) = 중복
                            if abs(ex_pr - pr) / max(pr, 1.0) < 0.001:
                                return True
                        return False

                    _tx_rows = []
                    for _tidx, _ti in enumerate(_tx_list):
                        _ttype = _ti.get("transaction_type", "buy")
                        _is_sell = (_ttype == "sell")
                        _tag = "📉 매도" if _is_sell else "📈 매수"
                        _dup = (not _is_sell) and _is_dup_buy(_ti)

                        with st.container():
                            _badge = " 🔁 이미 등록됨" if _dup else ""
                            st.markdown(f"**{_tidx+1}. {_tag} — {_ti.get('name','?')} ({_ti.get('ticker','?')}){_badge}**")
                            _tc1, _tc2, _tc3 = st.columns(3)
                            _tsym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_ti.get("currency", "JPY"), "¥")

                            with _tc1:
                                _e_tk = st.text_input("티커", value=_ti.get("ticker", ""), key=f"tx_tk_{_tidx}")
                                # 티커 검색 (한국 ETF 등 이름으로 티커 찾기)
                                if not _e_tk or _e_tk.strip() == "":
                                    _srch_q = st.text_input(
                                        "🔍 종목 검색 (이름 일부 입력)",
                                        placeholder="예: KODEX S&P, TIGER 나스닥, 삼성 배당",
                                        key=f"tx_search_{_tidx}",
                                    )
                                    if _srch_q and len(_srch_q) >= 2:
                                        try:
                                            import yfinance as _yf_srch
                                            _sr = _yf_srch.Search(_srch_q, max_results=5)
                                            if _sr.quotes:
                                                _srch_opts = [
                                                    f"{q['symbol']} — {q.get('shortname','') or q.get('longname','')}"
                                                    for q in _sr.quotes
                                                ]
                                                _srch_sel = st.selectbox(
                                                    "검색 결과에서 선택", _srch_opts,
                                                    key=f"tx_search_sel_{_tidx}",
                                                )
                                                if _srch_sel:
                                                    _found_tk = _srch_sel.split(" — ")[0]
                                                    _found_nm = _srch_sel.split(" — ")[1] if " — " in _srch_sel else _found_tk
                                                    if st.button(f"📌 적용: {_found_tk}", key=f"tx_search_apply_{_tidx}", type="primary"):
                                                        import re as _re_ind
                                                        _tx_list[_tidx]["ticker"] = _found_tk
                                                        _tx_list[_tidx]["name"] = _found_nm
                                                        _auto_cu = None
                                                        if _found_tk.endswith(".KS") or _found_tk.endswith(".KQ"):
                                                            _auto_cu = "KRW"
                                                        elif _found_tk.endswith(".T"):
                                                            _auto_cu = "JPY"
                                                        if _auto_cu:
                                                            _tx_list[_tidx]["currency"] = _auto_cu
                                                        # 날짜 재추정
                                                        _orig_d2 = _tx_list[_tidx].get("_original_date", "")
                                                        _pr2 = float(_tx_list[_tidx].get("price") or 0)
                                                        if _orig_d2 and _orig_d2.startswith("XXXX") and _pr2 > 0:
                                                            _m2 = _re_ind.search(r'(?:XXXX-)?(\d{1,2})-(\d{1,2})$', _orig_d2)
                                                            if _m2:
                                                                _mm2, _dd2 = int(_m2.group(1)), int(_m2.group(2))
                                                                try:
                                                                    _dy, _dp = _determine_year_by_price(_found_tk, _pr2, _mm2, _dd2)
                                                                    _nd = f"{_dy}-{_mm2:02d}-{_dd2:02d}"
                                                                    _tx_list[_tidx]["date"] = _nd
                                                                    st.session_state[f"tx_dt_{_tidx}"] = date.fromisoformat(_nd)
                                                                except Exception:
                                                                    _nd = f"{date.today().year}-{_mm2:02d}-{_dd2:02d}"
                                                                    _tx_list[_tidx]["date"] = _nd
                                                                    try:
                                                                        st.session_state[f"tx_dt_{_tidx}"] = date.fromisoformat(_nd)
                                                                    except Exception:
                                                                        pass
                                                        st.session_state.transaction_parsed = _tx_list
                                                        st.session_state[f"tx_tk_{_tidx}"] = _found_tk
                                                        st.session_state[f"tx_nm_{_tidx}"] = _found_nm
                                                        if _auto_cu:
                                                            st.session_state[f"tx_cu_{_tidx}"] = _auto_cu
                                                        st.rerun()
                                            else:
                                                st.caption("검색 결과가 없습니다.")
                                        except Exception:
                                            st.caption("검색 오류 — 다른 키워드로 시도해보세요.")
                                _e_nm = st.text_input("종목명", value=_ti.get("name", ""), key=f"tx_nm_{_tidx}")

                                # 수량 표시 — 자동 계산된 경우 안내 포함
                                _qty_val = max(0.0001, float(_ti.get("quantity") or 1))
                                _is_auto_qty = _ti.get("_qty_auto", False)
                                if _is_auto_qty:
                                    st.caption(f"📊 수량 자동 계산됨: {_ti.get('_qty_auto_info', '')}")
                                    _e_q = st.number_input("수량 (자동 계산)", value=_qty_val,
                                                           min_value=0.000001, step=0.000001,
                                                           format="%.6f", key=f"tx_q_{_tidx}")
                                else:
                                    _e_q = st.number_input("수량", value=_qty_val,
                                                           min_value=0.0001, step=1.0, key=f"tx_q_{_tidx}")
                            with _tc2:
                                _e_pr = st.number_input(f"단가 ({_tsym})", value=max(0.0, float(_ti.get("price") or 0)),
                                                        min_value=0.0, step=0.01, key=f"tx_pr_{_tidx}")
                                _e_cu = st.selectbox("통화", ["JPY", "USD", "KRW"],
                                    index=["JPY", "USD", "KRW"].index(_ti.get("currency", "JPY"))
                                        if _ti.get("currency", "JPY") in ["JPY", "USD", "KRW"] else 0,
                                    key=f"tx_cu_{_tidx}")
                                _draw = _ti.get("date", "")
                                _pdt = None
                                try:
                                    from datetime import datetime as _dtx2
                                    _pdt = _dtx2.strptime(_draw, "%Y-%m-%d").date() \
                                        if _draw and not _draw.startswith("XXXX") else None
                                except Exception:
                                    _pdt = None
                                _e_dt = st.date_input(
                                    "매도일" if _is_sell else "매수일",
                                    value=_pdt or date.today(), key=f"tx_dt_{_tidx}"
                                )
                                if not _pdt and not _is_sell:
                                    _year_auto = _ti.get("_year_det_pct")
                                    if _year_auto is not None:
                                        _diff_s = f"{_year_auto:.1f}%" if _year_auto < 99 else "데이터 부족"
                                        st.caption(f"📅 주가 비교로 연도 추정 (매수가 오차 {_diff_s})")
                            with _tc3:
                                _bdet = _ti.get("broker", "")
                                _bidx = BROKER_LIST.index(_bdet) if _bdet in BROKER_LIST else 0
                                _e_br = st.selectbox("증권사", BROKER_LIST, index=_bidx, key=f"tx_br_{_tidx}")
                                _e_ac = st.selectbox("계좌 종류", ACCOUNT_TYPES,
                                    index=ACCOUNT_TYPES.index(_ti.get("account_type", "일반계좌"))
                                        if _ti.get("account_type", "일반계좌") in ACCOUNT_TYPES else 0,
                                    key=f"tx_ac_{_tidx}")
                                _e_no = st.text_input("메모", value=_ti.get("notes", ""), key=f"tx_no_{_tidx}")

                            # 매도면 이동평균 실현손익 미리보기
                            if _is_sell and _e_tk and _e_q > 0 and _e_pr > 0:
                                if _e_cu != "KRW":
                                    _psr, _ = get_historical_exchange_rate(_e_cu, str(_e_dt))
                                else:
                                    _psr = 1.0
                                _pvc = _calc_sell_moving_avg(_e_tk, _e_br, _e_q, _e_pr, _e_cu, _psr)
                                if _pvc.get("error"):
                                    st.error(f"⚠️ {_pvc['error']}")
                                    _e_in = st.checkbox("등록", value=False, key=f"tx_in_{_tidx}", disabled=True)
                                else:
                                    st.info(
                                        f"📊 이동평균 매수가: **{_tsym}{_pvc['moving_avg_price']:,.2f}**  | "
                                        f"실현손익: **{_tsym}{_pvc['realized_gain_orig']:+,.0f}** "
                                        f"({_pvc['realized_gain_pct']:+.2f}%)  | "
                                        f"KRW: ₩{_pvc['realized_gain_krw']:+,.0f}"
                                    )
                                    _e_in = st.checkbox("등록", value=True, key=f"tx_in_{_tidx}")
                            else:
                                if _dup:
                                    st.caption("🔁 **이미 등록된 항목 — 등록 안 됨**")
                                    _e_in = False
                                else:
                                    _e_in = st.checkbox("등록", value=True, key=f"tx_in_{_tidx}")

                            _tx_rows.append({
                                "type": _ttype, "ticker": _e_tk, "name": _e_nm, "quantity": _e_q,
                                "price": _e_pr, "currency": _e_cu, "date": str(_e_dt),
                                "broker": _e_br, "account_type": _e_ac, "notes": _e_no,
                                "_include": _e_in,
                            })
                            st.markdown("---")

                    _n_inc = sum(1 for r in _tx_rows if r.get("_include"))
                    _tx_group = st.selectbox("포트폴리오 그룹", PORTFOLIO_GROUPS, key="tx_pf_group")
                    _cr, _cc2 = st.columns([2, 1])
                    with _cr:
                        if st.button(f"✅ {_n_inc}건 등록", type="primary",
                                     use_container_width=True, disabled=_n_inc == 0, key="tx_commit_btn"):
                            _ok_b, _ok_s, _fail2 = 0, 0, []
                            for row in _tx_rows:
                                if not row.get("_include"):
                                    continue
                                if not row["ticker"] or row["price"] <= 0:
                                    continue
                                try:
                                    if row["currency"] != "KRW":
                                        _rate, _ = get_historical_exchange_rate(row["currency"], row["date"])
                                    else:
                                        _rate = 1.0

                                    if row["type"] == "sell":
                                        _ccx = _calc_sell_moving_avg(
                                            row["ticker"].upper(), row["broker"],
                                            row["quantity"], row["price"], row["currency"], _rate,
                                        )
                                        if _ccx.get("error"):
                                            _fail2.append(f"{row['ticker']} 매도: {_ccx['error']}")
                                            continue
                                        _execute_sell(
                                            ticker=row["ticker"].upper(),
                                            name=row["name"] or row["ticker"],
                                            broker=row["broker"],
                                            account_type=row["account_type"],
                                            sell_qty=row["quantity"],
                                            sell_price=row["price"],
                                            sell_currency=row["currency"],
                                            sell_rate=_rate,
                                            sell_date=row["date"],
                                            notes=row["notes"],
                                            calc_result=_ccx,
                                        )
                                        _ok_s += 1
                                    else:
                                        add_stock(
                                            ticker=row["ticker"].upper().strip(),
                                            name=row["name"] or row["ticker"],
                                            quantity=row["quantity"],
                                            purchase_price=row["price"],
                                            purchase_currency=row["currency"],
                                            purchase_exchange_rate=_rate,
                                            purchase_date=row["date"],
                                            broker=row.get("broker", ""),
                                            account_type=row["account_type"],
                                            notes=row["notes"],
                                            portfolio_group=_tx_group,
                                        )
                                        _ok_b += 1
                                except Exception as _ex:
                                    _fail2.append(f"{row['ticker']}: {_ex}")
                            st.session_state.transaction_parsed = []
                            _smsg = []
                            if _ok_b:
                                _smsg.append(f"📈매수 {_ok_b}건")
                            if _ok_s:
                                _smsg.append(f"📉매도 {_ok_s}건")
                            if _smsg:
                                st.success(f"✅ {' · '.join(_smsg)} 등록 완료!")
                            for _ms in _fail2:
                                st.warning(_ms)
                            st.rerun()
                    with _cc2:
                        if st.button("취소", use_container_width=True, key="tx_cancel_btn"):
                            st.session_state.transaction_parsed = []
                            st.rerun()

        # ═══ 매도 수동 입력 탭 ══════════════════════════════════
        with _ut_manual:
            _m_stocks = get_all_stocks()
            _m_tickers = sorted({s["ticker"] for s in _m_stocks})
            if not _m_tickers:
                st.info("등록된 종목이 없습니다. 먼저 매수 내역을 등록하세요.")
            else:
                _m_seen, _m_unique = set(), []
                for _s in sorted(_m_stocks, key=lambda x: x["ticker"]):
                    if _s["ticker"] not in _m_seen:
                        _m_seen.add(_s["ticker"])
                        _m_unique.append(_s)
                _m_opts = [f"{_s['ticker']} — {_s['name']}" for _s in _m_unique]
                _m_sel = st.selectbox("종목 선택", _m_opts, key="sell_m_sel")
                _m_ticker = _m_sel.split(" — ")[0] if _m_sel else ""
                _m_stock = next((_s for _s in _m_unique if _s["ticker"] == _m_ticker), None)
                _m_currency = _m_stock["purchase_currency"] if _m_stock else "JPY"
                _m_sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_m_currency, "¥")

                _m_entries_t = [s for s in _m_stocks if s["ticker"] == _m_ticker]
                _m_brokers = sorted({s.get("broker", "") for s in _m_entries_t})
                _m_broker = st.selectbox(
                    "증권사 (매도 대상)", _m_brokers,
                    format_func=lambda x: x if x else "(미설정)",
                    key="sell_m_broker"
                )
                _m_broker_ent = [s for s in _m_entries_t if s.get("broker", "") == _m_broker]
                _m_tot_qty = sum(s["quantity"] for s in _m_broker_ent)
                _m_avg = (sum(s["purchase_price"] * s["quantity"] for s in _m_broker_ent) / _m_tot_qty) \
                    if _m_tot_qty > 0 else 0
                _m_acct_def = _m_broker_ent[0].get("account_type", "일반계좌") if _m_broker_ent else "일반계좌"

                # 현재 시장가 조회 — 매도 단가 기본값으로 사용 (실제 손익 미리보기)
                _m_cur_price = 0.0
                try:
                    _m_info = get_stock_info(_m_ticker)
                    _m_cur_price = float(_m_info.get("price", 0) or 0)
                except Exception:
                    pass
                _m_default_price = _m_cur_price if _m_cur_price > 0 else _m_avg

                # 통화별 안내 — 종목의 실제 통화에 따라 라벨/통화기호 결정
                _cur_label = {"JPY": "엔화 (JPY / ¥)", "USD": "달러 (USD / $)", "KRW": "원화 (KRW / ₩)"}.get(_m_currency, _m_currency)
                _country_hint = {"JPY": "🇯🇵 일본 주식", "USD": "🇺🇸 미국 주식", "KRW": "🇰🇷 한국 주식"}.get(_m_currency, "")
                st.caption(
                    f"현재 보유: **{_m_tot_qty:,.0f}주** | 이동평균 매수가: **{_m_sym}{_m_avg:,.2f}** "
                    f"(총 {len(_m_broker_ent)}건)  |  {_country_hint} → **{_cur_label}**로 기록"
                )
                if _m_cur_price > 0:
                    _m_unreal_pct = ((_m_cur_price - _m_avg) / _m_avg * 100) if _m_avg > 0 else 0
                    _m_unreal_cls = "color:#2e7d32" if _m_unreal_pct >= 0 else "color:#c62828"
                    st.markdown(
                        f"💹 **현재 시장가: {_m_sym}{_m_cur_price:,.2f}**  "
                        f"<span style='{_m_unreal_cls}'>({_m_unreal_pct:+.2f}% vs 평단가)</span>"
                        f"  —  아래 '매도 단가'의 기본값은 현재 시장가입니다. 실제 체결가로 수정 후 등록하세요.",
                        unsafe_allow_html=True,
                    )

                # Streamlit form 캐싱 문제 방지: 종목별 form key
                with st.form(f"sell_manual_form_{_m_ticker}_{_m_broker}", clear_on_submit=True):
                    _mf1, _mf2, _mf3 = st.columns(3)
                    with _mf1:
                        _m_qty = st.number_input(
                            "매도 수량 *", min_value=0.0001,
                            max_value=float(_m_tot_qty) if _m_tot_qty > 0 else 999999.0,
                            value=min(float(_m_tot_qty), 1.0) if _m_tot_qty > 0 else 1.0,
                            step=1.0,
                            key=f"sellm_qty_{_m_ticker}_{_m_broker}",
                        )
                        _m_price = st.number_input(
                            f"매도 단가 ({_m_sym} {_m_currency}) *", min_value=0.0,
                            value=float(_m_default_price), step=0.01,
                            help=(f"기본값 = 현재 시장가 {_m_sym}{_m_cur_price:,.2f}. "
                                  f"실제 체결된 단가로 수정하세요." if _m_cur_price > 0
                                  else f"{_country_hint}의 매도 단가를 {_cur_label}로 입력하세요."),
                            key=f"sellm_price_{_m_ticker}_{_m_broker}",
                        )
                    with _mf2:
                        _m_date = st.date_input("매도일", value=date.today(),
                                                 key=f"sellm_date_{_m_ticker}_{_m_broker}")
                        _m_acct_idx = ACCOUNT_TYPES.index(_m_acct_def) if _m_acct_def in ACCOUNT_TYPES else 0
                        _m_acct = st.selectbox("계좌 종류", ACCOUNT_TYPES, index=_m_acct_idx,
                                                key=f"sellm_acct_{_m_ticker}_{_m_broker}")
                    with _mf3:
                        _m_notes = st.text_input("메모", value="",
                                                  key=f"sellm_notes_{_m_ticker}_{_m_broker}")
                        _m_rate_ovr = st.number_input(
                            f"매도일 환율 직접 입력 (1{_m_sym}=?원, 0=자동)",
                            min_value=0.0, value=0.0, step=0.1,
                            disabled=(_m_currency == "KRW"),
                            help="원화 환산용 환율. 비워두면 매도일 환율을 자동 조회합니다.",
                            key=f"sellm_rate_{_m_ticker}_{_m_broker}",
                        )

                    if _m_qty > 0 and _m_price > 0 and _m_tot_qty > 0:
                        if _m_currency != "KRW":
                            _m_rate_prev, _ = get_historical_exchange_rate(_m_currency, str(_m_date))
                            _m_rate_use = _m_rate_ovr if _m_rate_ovr > 0 else _m_rate_prev
                        else:
                            _m_rate_use = 1.0
                        _m_calc = _calc_sell_moving_avg(
                            _m_ticker, _m_broker, _m_qty, _m_price, _m_currency, _m_rate_use
                        )
                        if _m_calc.get("error"):
                            st.error(f"⚠️ {_m_calc['error']}")
                        else:
                            # 주가 손익(원통화) + 환차손익(KRW) 분리 설명
                            _mv_p = _m_calc["moving_avg_price"]
                            _mv_r = _m_calc["moving_avg_rate"]
                            _g_orig = _m_calc["realized_gain_orig"]
                            _g_krw  = _m_calc["realized_gain_krw"]
                            _g_pct  = _m_calc["realized_gain_pct"]
                            _g_cls  = "#2e7d32" if _g_orig >= 0 else "#c62828"
                            _kcls   = "#2e7d32" if _g_krw  >= 0 else "#c62828"
                            st.markdown(
                                f"<div style='background:#f5f7fa; border:1px solid #dfe4ea; "
                                f"border-radius:8px; padding:12px 16px; margin-top:4px;'>"
                                f"📊 <b>예상 실현손익 미리보기</b><br>"
                                f"• 이동평균 매수가: <b>{_m_sym}{_mv_p:,.2f}</b>  "
                                f"→ 매도 단가: <b>{_m_sym}{_m_price:,.2f}</b> × {_m_qty:,.0f}주<br>"
                                f"• 주가 손익: <b style='color:{_g_cls}'>"
                                f"{_m_sym}{_g_orig:+,.0f} ({_g_pct:+.2f}%)</b><br>"
                                f"• 원화 환산 손익: <b style='color:{_kcls}'>₩{_g_krw:+,.0f}</b>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            # 환차손익 설명 (주가 손익 ≈ 0 이지만 KRW 손익이 있는 경우)
                            if _m_currency != "KRW":
                                _fx_only_krw = _mv_p * _m_qty * (_m_rate_use - _mv_r)
                                if abs(_g_orig) < 0.5 and abs(_g_krw) > 1:
                                    _fx_cls = "#2e7d32" if _fx_only_krw >= 0 else "#c62828"
                                    st.caption(
                                        f"💱 주가는 평단가와 같지만 환율 차이로 KRW 손익이 발생했습니다.  "
                                        f"매수 평균 환율 **₩{_mv_r:,.2f}/{_m_sym}** vs 매도일 환율 **₩{_m_rate_use:,.2f}/{_m_sym}** "
                                        f"→ 환차손익: **:color[₩{_fx_only_krw:+,.0f}]** "
                                        f"(참고: 엔화 기준 자산 관점에선 영향 없음)"
                                    )

                    if st.form_submit_button("✅ 매도 등록", type="primary"):
                        if _m_qty <= 0 or _m_price <= 0:
                            st.error("매도 수량과 단가를 입력하세요.")
                        elif _m_qty > _m_tot_qty + 0.0001:
                            st.error(f"매도 수량이 보유 수량({_m_tot_qty:,.0f}주)을 초과합니다.")
                        else:
                            if _m_currency != "KRW":
                                _m_rate_sub, _ = get_historical_exchange_rate(_m_currency, str(_m_date))
                                _m_rate_sub = _m_rate_ovr if _m_rate_ovr > 0 else _m_rate_sub
                            else:
                                _m_rate_sub = 1.0
                            _m_calc_sub = _calc_sell_moving_avg(
                                _m_ticker, _m_broker, _m_qty, _m_price, _m_currency, _m_rate_sub
                            )
                            if _m_calc_sub.get("error"):
                                st.error(_m_calc_sub["error"])
                            else:
                                _m_name = _m_stock["name"] if _m_stock else _m_ticker
                                _execute_sell(
                                    ticker=_m_ticker, name=_m_name, broker=_m_broker,
                                    account_type=_m_acct, sell_qty=_m_qty, sell_price=_m_price,
                                    sell_currency=_m_currency, sell_rate=_m_rate_sub,
                                    sell_date=str(_m_date), notes=_m_notes,
                                    calc_result=_m_calc_sub,
                                )
                                st.success(
                                    f"✅ {_m_ticker} {_m_qty:,.0f}주 매도 등록 완료!  "
                                    f"실현손익: {_m_sym}{_m_calc_sub['realized_gain_orig']:+,.0f} "
                                    f"(₩{_m_calc_sub['realized_gain_krw']:+,.0f})"
                                )
                                st.rerun()

    # ── 평단가로 등록 ────────────────────────────────────────────
    with st.expander("📊 평단가로 등록 (평균 매수가·보유 수량 입력)", expanded=False):
        st.caption(
            "날짜별 매수 내역을 모를 때 사용합니다.  \n"
            "**평균 매수가 × 보유 수량**으로 현재 수익을 계산하므로 브로커 앱 표시와 일치합니다."
        )

        # ── 종목 선택 (새 종목 추가와 동일한 패턴) ──────────────
        _ac_all_stocks = get_all_stocks()
        _ac_tickers    = sorted({s["ticker"] for s in _ac_all_stocks})

        if _ac_tickers:
            _ac_mode = st.radio(
                "티커 입력 방식",
                ["기존 종목에서 선택", "새 티커 직접 입력"],
                horizontal=True, key="ac_mode",
            )
        else:
            _ac_mode = "새 티커 직접 입력"

        # ── 조회 전 입력: 종목 / 증권사 / 통화 / 조회 버튼 ────────
        _ac1, _ac2, _ac3, _ac4 = st.columns([3, 2, 2, 1])
        with _ac1:
            if _ac_mode == "기존 종목에서 선택":
                _ac_seen, _ac_unique = set(), []
                for _s in sorted(_ac_all_stocks, key=lambda x: x["ticker"]):
                    if _s["ticker"] not in _ac_seen:
                        _ac_seen.add(_s["ticker"])
                        _ac_unique.append(_s)
                _ac_opts = [f"{_s['ticker']} — {_s['name']}" for _s in _ac_unique]
                _ac_sel  = st.selectbox("기존 종목 선택", _ac_opts, key="ac_sel")
                _ac_ticker_val = _ac_sel.split(" — ")[0] if _ac_sel else ""
                _ac_sel_stock  = next((_s for _s in _ac_unique if _s["ticker"] == _ac_ticker_val), None)
                _ac_cur_idx    = ["JPY", "USD", "KRW"].index(
                    _ac_sel_stock["purchase_currency"] if _ac_sel_stock else "JPY"
                )
            else:
                _ac_ticker_val = st.text_input(
                    "티커 심볼", placeholder="AAPL  /  7203.T  /  BTC-JPY", key="ac_ticker"
                )
                _ac_sel_stock, _ac_cur_idx = None, 0
        with _ac2:
            # 브로커를 조회 전에 선택 → 해당 브로커 항목만 요약·정리
            _ac_entries_for_ticker = (
                [_s for _s in _ac_all_stocks if _s["ticker"] == _ac_ticker_val]
                if _ac_ticker_val else []
            )
            _ac_brokers_in_ticker = sorted({_s.get("broker", "") for _s in _ac_entries_for_ticker})
            if _ac_brokers_in_ticker:
                _ac_broker_sel = st.selectbox(
                    "증권사 (정리 대상)",
                    _ac_brokers_in_ticker,
                    format_func=lambda x: x if x else "(미설정)",
                    key="ac_broker_pre",
                )
            else:
                _ac_broker_sel_idx = BROKER_LIST.index(_ac_sel_stock["broker"]) \
                    if _ac_sel_stock and _ac_sel_stock.get("broker") in BROKER_LIST else 0
                _ac_broker_sel = st.selectbox(
                    "증권사", BROKER_LIST, index=_ac_broker_sel_idx,
                    format_func=lambda x: x if x else "(미설정)",
                    key="ac_broker_pre2",
                )
        with _ac3:
            _ac_currency = st.selectbox(
                "통화", ["JPY", "USD", "KRW"],
                index=_ac_cur_idx,
                format_func=lambda x: f"{x} ({CURRENCY_LABELS[x]})",
            )
        with _ac4:
            st.write(""); st.write("")
            _ac_lookup = st.button("📡 조회", key="ac_lookup_btn", type="primary", use_container_width=True)

        # ── 선택 종목+증권사의 현재 등록 현황 ──────────────────────
        _ac_broker_entries = [
            _s for _s in _ac_entries_for_ticker
            if _s.get("broker", "") == _ac_broker_sel
        ]
        if _ac_broker_entries:
            _ac_tot_qty = sum(_s["quantity"] for _s in _ac_broker_entries)
            _ac_avg_p   = sum(_s["purchase_price"] * _s["quantity"] for _s in _ac_broker_entries) / _ac_tot_qty
            _ac_sym_tmp = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_ac_currency, "¥")
            _ac_broker_label = _ac_broker_sel if _ac_broker_sel else "미설정"
            st.caption(
                f"**{_ac_broker_label}** 등록 현황 — "
                f"{len(_ac_broker_entries)}건 | "
                f"합계 수량: {_ac_tot_qty:,.0f}주 | "
                f"등록 평균 매수가: {_ac_sym_tmp}{_ac_avg_p:,.2f}"
            )

        if _ac_lookup:
            _t = (_ac_ticker_val or "").strip().upper()
            if not _t:
                st.error("티커를 입력하세요.")
            else:
                with st.spinner("종목 정보 및 환율 조회 중..."):
                    _ac_info = get_stock_info(_t)
                    if _ac_currency != "KRW":
                        _ac_hist_rate, _ac_rate_date = get_historical_exchange_rate(
                            _ac_currency, str(date.today())
                        )
                    else:
                        _ac_hist_rate, _ac_rate_date = 1.0, str(date.today())
                _ac_acct_def = (
                    _ac_broker_entries[0].get("account_type", "일반계좌")
                    if _ac_broker_entries else
                    (_ac_sel_stock.get("account_type", "일반계좌") if _ac_sel_stock else "일반계좌")
                )
                st.session_state.avg_cost_preview = {
                    "ticker":         _t,
                    "name":           _ac_info.get("name", _t) or _t,
                    "current_price":  _ac_info.get("price", 0.0),
                    "currency":       _ac_currency,
                    "approx_date":    str(date.today()),
                    "historical_rate": _ac_hist_rate,
                    "actual_rate_date": _ac_rate_date,
                    "broker_sel":     _ac_broker_sel,
                    "acct_default":   _ac_acct_def,
                    # 정리 대상 항목 스냅샷 (submit 시점에 재조회)
                    "recon_entries":  [{"id": _s["id"], "qty": _s["quantity"],
                                        "price": _s["purchase_price"],
                                        "rate": _s["purchase_exchange_rate"],
                                        "date": _s["purchase_date"]}
                                       for _s in _ac_broker_entries],
                }

        _ac_prev = st.session_state.avg_cost_preview
        if _ac_prev:
            _sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(_ac_prev["currency"], "$")
            _rate_lbl = (
                f"**현재 환율: {_ac_prev['historical_rate']:,.2f}원**"
                if _ac_prev["currency"] != "KRW" else "원화 종목"
            )
            st.info(
                f"📌 **{_ac_prev['name']}** ({_ac_prev['ticker']})　|　"
                f"현재가: {_sym}{_ac_prev['current_price']:,.2f}　|　{_rate_lbl}"
            )

            _ac_acct_def_idx = (
                ACCOUNT_TYPES.index(_ac_prev["acct_default"])
                if _ac_prev["acct_default"] in ACCOUNT_TYPES else 0
            )
            _recon_entries = _ac_prev.get("recon_entries", [])
            _recon_old_qty = sum(e["qty"] for e in _recon_entries)

            with st.form("avg_cost_form", clear_on_submit=True):
                _f1, _f2, _f3 = st.columns(3)
                with _f1:
                    _ac_price = st.number_input(
                        f"평균 매수가 ({_ac_prev['currency']}) *",
                        min_value=0.0, value=0.0, step=0.01,
                        help="브로커 앱의 '평균 취득 단가' 또는 '평균 매수가'",
                    )
                    _ac_qty = st.number_input("현재 보유 수량 *", min_value=0.0001, value=1.0, step=1.0)
                with _f2:
                    _ac_acct = st.selectbox("계좌 종류", ACCOUNT_TYPES, index=_ac_acct_def_idx)
                    _ac_rate_ovr = st.number_input(
                        "환율 직접 입력 (선택, 0=자동)",
                        min_value=0.0, value=0.0, step=0.1,
                        disabled=(_ac_prev["currency"] == "KRW"),
                        help="자동 조회 환율이 실제와 다를 경우 직접 입력",
                    )
                with _f3:
                    _ac_name = st.text_input("종목명 수정 (선택)", value=_ac_prev["name"])
                    _ac_notes = st.text_input("메모", value="평단가 등록")

                # ── 기존 항목 정리 옵션 ──────────────────────────────
                _fin_rate = _ac_rate_ovr if _ac_rate_ovr > 0 else _ac_prev["historical_rate"]
                if _recon_entries:
                    _sold_qty = _recon_old_qty - _ac_qty
                    _recon_label = (
                        f"기존 {len(_recon_entries)}건 정리 후 1건으로 통합  "
                        f"({_ac_prev['broker_sel'] or '미설정'} | "
                        f"기존 {_recon_old_qty:,.0f}주 → 현재 {_ac_qty:,.0f}주"
                        + (f" | 매도 처리: {_sold_qty:,.0f}주" if _sold_qty > 0 else "")
                        + ")"
                    )
                    _do_recon = st.checkbox(_recon_label, value=True)
                else:
                    _do_recon = False

                # ── 예상 수익 미리보기 ───────────────────────────────
                _cur_p = _ac_prev["current_price"]
                if _ac_price > 0 and _cur_p > 0:
                    _gain     = (_cur_p - _ac_price) * _ac_qty
                    _gain_pct = _gain / (_ac_price * _ac_qty) * 100
                    _gain_krw = _gain * _fin_rate
                    if _ac_prev["currency"] == "JPY":
                        st.info(
                            f"💹 등록 후 예상 수익: **¥{_gain:+,.0f}** (₩{_gain_krw:+,.0f}) | {_gain_pct:+.2f}%  \n"
                            "브로커 앱 표시와 비교해서 맞으면 등록하세요."
                        )
                    elif _ac_prev["currency"] == "USD":
                        st.info(
                            f"💹 등록 후 예상 수익: **${_gain:+,.2f}** (₩{_gain_krw:+,.0f}) | {_gain_pct:+.2f}%  \n"
                            "브로커 앱 표시와 비교해서 맞으면 등록하세요."
                        )

                if st.form_submit_button("✅ 평단가로 등록", type="primary"):
                    if _ac_price <= 0:
                        st.error("평균 매수가를 입력하세요.")
                    else:
                        _broker_to_use  = _ac_prev["broker_sel"]
                        _name_to_use    = _ac_name or _ac_prev["name"]
                        _ticker_to_use  = _ac_prev["ticker"]
                        _currency_to_use = _ac_prev["currency"]
                        _fin_rate_sub   = _ac_rate_ovr if _ac_rate_ovr > 0 else _ac_prev["historical_rate"]

                        if _do_recon and _recon_entries:
                            # ── 기존 항목 정리 실행 ─────────────────
                            # 1) 기존 항목의 가중평균 매수가·환율 계산
                            _old_total_qty = sum(e["qty"] for e in _recon_entries)
                            _old_avg_price = sum(e["price"] * e["qty"] for e in _recon_entries) / _old_total_qty
                            _old_avg_rate  = sum(e["rate"] * e["qty"] for e in _recon_entries) / _old_total_qty
                            _old_avg_date  = _recon_entries[0]["date"]  # 가장 오래된 매수일
                            _implied_sold  = max(0.0, _old_total_qty - _ac_qty)

                            # 2) 암묵적 매도 기록 생성 (매도가 = 매수가 기준, 실제 매도가 미확인)
                            if _implied_sold > 0:
                                add_sold_record(
                                    ticker=_ticker_to_use,
                                    name=_name_to_use,
                                    broker=_broker_to_use,
                                    account_type=_ac_acct,
                                    purchase_date=_old_avg_date,
                                    purchase_price=_old_avg_price,
                                    purchase_currency=_currency_to_use,
                                    purchase_exchange_rate=_old_avg_rate,
                                    sell_date=str(date.today()),
                                    sell_price=_old_avg_price,   # 실제 매도가 미확인 → 매수가로 기록
                                    sell_currency=_currency_to_use,
                                    sell_exchange_rate=_fin_rate_sub,
                                    quantity=_implied_sold,
                                    realized_gain_krw=0.0,        # 실제 손익 미확인
                                    realized_gain_pct=0.0,
                                    notes=f"평단가 정리 — 실제 매도가 미확인 (기록 목적, {_old_total_qty:,.0f}주→{_ac_qty:,.0f}주)",
                                )

                            # 3) 기존 포트폴리오 항목 삭제
                            for _e in _recon_entries:
                                delete_stock(_e["id"])

                        # 4) 새 단일 항목 추가
                        add_stock(
                            ticker=_ticker_to_use,
                            name=_name_to_use,
                            quantity=_ac_qty,
                            purchase_price=_ac_price,
                            purchase_currency=_currency_to_use,
                            purchase_exchange_rate=_fin_rate_sub,
                            purchase_date=_ac_prev["approx_date"],
                            broker=_broker_to_use,
                            account_type=_ac_acct,
                            notes=_ac_notes,
                        )
                        st.session_state.avg_cost_preview = None
                        _msg = f"✅ {_name_to_use} 평단가 등록 완료! 평균 매수가: {_sym}{_ac_price:,.2f} × {_ac_qty:,.0f}주"
                        if _do_recon and _recon_entries and _implied_sold > 0:
                            _msg += f"  \n📋 기존 {len(_recon_entries)}건 정리 완료 (암묵적 매도 {_implied_sold:,.0f}주 기록됨)"
                        st.success(_msg)
                        st.rerun()

    # ── STEP 1: 티커 입력 (기존 종목 선택 또는 직접 입력) ────────
    with st.expander("새 종목 추가", expanded=True):
        st.caption("매수일만 입력하면 해당 날짜의 환율을 자동으로 조회합니다.")

        existing_stocks = get_all_stocks()
        existing_tickers = sorted({s["ticker"] for s in existing_stocks})

        # 기존 종목 여부에 따라 입력 방식 분기
        if existing_tickers:
            ticker_mode = st.radio(
                "티커 입력 방식",
                ["기존 종목에서 선택", "새 티커 직접 입력"],
                horizontal=True,
            )
        else:
            ticker_mode = "새 티커 직접 입력"

        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        with c1:
            if ticker_mode == "기존 종목에서 선택":
                # 기존 종목 선택 드롭다운
                existing_map = {
                    f"{s['ticker']} — {s['name']}": s["ticker"]
                    for s in sorted(existing_stocks, key=lambda x: x["ticker"])
                    if s["ticker"] in existing_tickers
                }
                # 중복 제거 (같은 티커 여러 번 나올 수 있음)
                seen = set()
                unique_existing = []
                for s in sorted(existing_stocks, key=lambda x: x["ticker"]):
                    if s["ticker"] not in seen:
                        seen.add(s["ticker"])
                        unique_existing.append(s)
                options = [f"{s['ticker']} — {s['name']}" for s in unique_existing]
                selected_opt = st.selectbox("기존 종목 선택", options)
                ticker_input = selected_opt.split(" — ")[0] if selected_opt else ""
                # 선택한 종목의 통화 자동 반영
                selected_stock = next((s for s in unique_existing if s["ticker"] == ticker_input), None)
                default_currency_idx = ["USD", "JPY", "KRW"].index(
                    selected_stock["purchase_currency"] if selected_stock else "USD"
                )
            else:
                ticker_input = st.text_input(
                    "티커 심볼 *",
                    placeholder="AAPL  /  7203.T  /  005930.KS  /  BTC-JPY",
                    help="미국: AAPL | 일본: 7203.T | 한국: 005930.KS | 암호화폐: BTC-JPY, ETH-JPY",
                )
                default_currency_idx = 0
                selected_stock = None

        with c2:
            date_input = st.date_input("매수일 *", value=date.today())
        with c3:
            currency_input = st.selectbox(
                "매수 통화 *",
                ["USD", "JPY", "KRW"],
                index=default_currency_idx,
                format_func=lambda x: f"{x} ({CURRENCY_LABELS[x]})",
            )
        with c4:
            st.write("")
            st.write("")
            do_lookup = st.button("📡 조회", type="primary", use_container_width=True)

        if do_lookup:
            t = ticker_input.strip() if ticker_input else ""
            if not t:
                st.error("티커 심볼을 입력하거나 선택하세요.")
            else:
                with st.spinner("종목 정보 및 환율 조회 중..."):
                    info = get_stock_info(t.upper())
                    if currency_input != "KRW":
                        hist_rate, actual_date = get_historical_exchange_rate(
                            currency_input, str(date_input)
                        )
                    else:
                        hist_rate, actual_date = 1.0, str(date_input)
                st.session_state.stock_preview = {
                    "ticker": t.upper(),
                    "name": info.get("name", t.upper()),
                    "current_price": info.get("price", 0.0),
                    "currency": currency_input,
                    "purchase_date": str(date_input),
                    "historical_rate": hist_rate,
                    "actual_rate_date": actual_date,
                }

        # ── STEP 2: 조회 결과 + 계좌 종류 선택 + 추가 ────────────
        preview = st.session_state.stock_preview
        if preview:
            sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(preview["currency"], "$")
            if preview["currency"] != "KRW":
                rate_label = (
                    f"**{preview['purchase_date']} 환율: {preview['historical_rate']:,.2f}원**  "
                    f"(실제 조회일: {preview['actual_rate_date']})"
                )
            else:
                rate_label = "원화 종목 — 환율 변환 없음"

            st.info(
                f"📌 **{preview['name']}** ({preview['ticker']})　|　"
                f"현재가: {sym}{preview['current_price']:,.2f}　|　{rate_label}"
            )

            with st.form("add_stock_form", clear_on_submit=True):
                col1, col2, col3 = st.columns(3)
                with col1:
                    purchase_price = st.number_input(
                        f"매수가 ({preview['currency']}) *",
                        min_value=0.0, value=0.0, step=0.01,
                    )
                    quantity = st.number_input(
                        "매수 수량 *", min_value=0.0001, value=1.0, step=1.0
                    )
                with col2:
                    broker = st.selectbox("증권사", BROKER_LIST)
                    account_type = st.selectbox(
                        "계좌 종류 *",
                        ACCOUNT_TYPES,
                        help="NISA 계좌는 매각익·배당금이 비과세입니다.",
                    )
                with col3:
                    custom_name = st.text_input("종목명 수정 (선택)", value=preview["name"])
                    override_rate = st.number_input(
                        "환율 직접 입력 (선택, 0=자동값 사용)",
                        min_value=0.0, value=0.0, step=0.1,
                        help="자동 조회 환율이 실제와 다를 경우에만 입력",
                        disabled=(preview["currency"] == "KRW"),
                    )
                    portfolio_group = st.selectbox("포트폴리오 그룹", PORTFOLIO_GROUPS)
                    notes = st.text_input("메모 (선택)")

                # ── NISA/세금 안내 ─────────────────────────────────
                if account_type in NISA_ACCOUNT_TYPES:
                    nisa_type = "성장투자" if "성장" in account_type else "적립투자"
                    limits = {"성장투자": ("연 240만엔", "총 1,200만엔"),
                              "적립투자": ("연 120만엔", "총 600만엔")}
                    yr_lim, total_lim = limits[nisa_type]
                    st.success(
                        f"✅ **NISA ({nisa_type}) — 비과세 계좌**\n\n"
                        f"매각익·배당금 세율: **0%** (비과세)\n\n"
                        f"투자 한도: {yr_lim} / {total_lim} | "
                        f"전체 비과세 한도: 총 1,800만엔 (성장+적립 합산)"
                    )
                else:
                    est_gain = max(0.0, (preview["current_price"] - purchase_price) * quantity)
                    tax_est = est_gain * TAX_RATE[account_type] * preview["historical_rate"]
                    src = "자동 원천징수" if "원천징수" in account_type else "확정신고 필요"
                    st.warning(
                        f"⚠️ **{account_type}** — 세율 20.315% (소득세 15.315% + 지방세 5%)\n\n"
                        f"납세 방법: {src}\n\n"
                        f"현재 기준 예상 세금: ₩{tax_est:,.0f} "
                        f"(매수가 기준 평가, 실제와 다를 수 있음)"
                    )

                if st.form_submit_button("✅ 추가하기", type="primary"):
                    if purchase_price <= 0:
                        st.error("매수가를 입력하세요.")
                    else:
                        final_rate = override_rate if override_rate > 0 else preview["historical_rate"]
                        add_stock(
                            ticker=preview["ticker"],
                            name=custom_name or preview["name"],
                            quantity=quantity,
                            purchase_price=purchase_price,
                            purchase_currency=preview["currency"],
                            purchase_exchange_rate=final_rate,
                            purchase_date=preview["purchase_date"],
                            broker=broker,
                            account_type=account_type,
                            notes=notes,
                            portfolio_group=portfolio_group,
                        )
                        st.session_state.stock_preview = None
                        st.success(
                            f"✅ {custom_name or preview['name']} 추가 완료!  "
                            f"증권사: {broker or '미설정'} | 계좌: {account_type} | 적용 환율: {final_rate:,.2f}원"
                        )
                        st.rerun()

    st.markdown("---")

    # ── 보유 종목 목록 + 주가 차트 ────────────────────────────────
    st.subheader("보유 종목 목록")
    stocks = get_all_stocks()

    if not stocks:
        st.info("추가된 종목이 없습니다.")
    else:
        # ══ 정렬 ════════════════════════════════════════════════
        _sort_options = {
            "매수일 (최신순)": lambda s: s.get("purchase_date", ""),
            "매수일 (오래된순)": lambda s: s.get("purchase_date", ""),
            "티커 (A→Z)": lambda s: s.get("ticker", ""),
            "종목명 (가나다순)": lambda s: s.get("name", ""),
        }
        _sort_sel = st.selectbox("정렬", list(_sort_options.keys()), key="stock_sort_sel")
        _reverse = _sort_sel in ("매수일 (최신순)",)
        stocks = sorted(stocks, key=_sort_options[_sort_sel], reverse=_reverse)

        # ══ 통합 일괄 관리 ════════════════════════════════════════
        BULK_ACTIONS = [
            "선택하세요",
            "🔍 티커 검색으로 일괄 업데이트",
            "🔤 종목명 영어로 업데이트",
            "📅 연도/날짜 일괄 변경",
            "🏦 증권사 일괄 설정",
            "📁 계좌 종류 일괄 변경",
            "📂 포트폴리오 그룹 일괄 변경",
            "✏️ 개별 필드 직접 수정",
            "🗑️ 일괄 삭제",
        ]
        bulk_col1, bulk_col2 = st.columns([3, 1])
        with bulk_col1:
            bulk_sel = st.selectbox("⚙️ 일괄 관리 작업 선택", BULK_ACTIONS, key="bulk_action_sel")
        with bulk_col2:
            st.write("")

        # ── 작업별 UI ─────────────────────────────────────────
        if bulk_sel == "🔍 티커 검색으로 일괄 업데이트":
            st.caption("종목 이름으로 검색하여 올바른 티커를 찾고, 선택한 항목의 티커/종목명을 일괄 업데이트합니다.")

            # ── STEP 1: 업데이트 대상 항목 선택 ──
            st.markdown("**① 업데이트할 항목 선택**")
            bulk_tk_ids = []
            for s in stocks:
                broker_tag = f" | {s.get('broker','')}" if s.get("broker") else ""
                label = (f"{s['name']} ({s['ticker']}) | {s['purchase_date']}"
                         f" | {s.get('account_type','일반계좌')}{broker_tag}"
                         f" | {s['purchase_currency']} {s['purchase_price']:,.2f} × {s['quantity']:.0f}주")
                if st.checkbox(label, value=False, key=f"tkchk_{s['id']}"):
                    bulk_tk_ids.append(s["id"])

            if bulk_tk_ids:
                st.success(f"✅ {len(bulk_tk_ids)}건 선택됨")

                # ── STEP 2: 티커 검색 ──
                st.markdown("**② 종목 검색**")
                tk_search_q = st.text_input(
                    "🔍 종목 이름 검색 (키워드 입력)",
                    placeholder="예: KODEX S&P, TIGER 나스닥100, 삼성전자, SPDR",
                    key="bulk_tk_search_q",
                )

                if tk_search_q and len(tk_search_q) >= 2:
                    try:
                        import yfinance as _yf_bulk
                        _bulk_sr = _yf_bulk.Search(tk_search_q, max_results=10)
                        if _bulk_sr.quotes:
                            _bulk_opts = [
                                f"{q['symbol']} — {q.get('shortname','') or q.get('longname','')}"
                                for q in _bulk_sr.quotes
                            ]
                            _bulk_sel = st.selectbox(
                                "검색 결과에서 선택", _bulk_opts,
                                key="bulk_tk_search_sel",
                            )
                            if _bulk_sel:
                                _found_ticker = _bulk_sel.split(" — ")[0]
                                _found_name = _bulk_sel.split(" — ")[1] if " — " in _bulk_sel else _found_ticker

                                st.info(f"📌 선택된 티커: **{_found_ticker}** | 종목명: **{_found_name}**")

                                # ── STEP 3: 적용 ──
                                st.markdown("**③ 일괄 적용**")
                                target_items = [s for s in stocks if s["id"] in bulk_tk_ids]
                                st.markdown("변경 미리보기:")
                                for s in target_items:
                                    old_info = f"{s['ticker']} / {s['name']}"
                                    st.markdown(f"- {old_info} → **{_found_ticker}** / **{_found_name}**")

                                if st.button(
                                    f"✅ {len(bulk_tk_ids)}건 티커/종목명 일괄 업데이트",
                                    type="primary",
                                    key="confirm_bulk_tk_update",
                                ):
                                    with st.spinner("업데이트 중..."):
                                        for sid in bulk_tk_ids:
                                            update_stock_ticker(sid, _found_ticker)
                                            update_stock_name(sid, _found_name)
                                    st.success(
                                        f"✅ {len(bulk_tk_ids)}건 업데이트 완료: "
                                        f"티커 → {_found_ticker} | 종목명 → {_found_name}"
                                    )
                                    st.rerun()
                        else:
                            st.warning("검색 결과가 없습니다. 다른 키워드로 시도해보세요.")
                    except Exception as e:
                        st.error(f"검색 오류: {e} — 다른 키워드로 시도해보세요.")

        elif bulk_sel == "🔤 종목명 영어로 업데이트":
            if st.button("실행: 종목명 영어로 일괄 업데이트", type="primary"):
                updated = []
                with st.spinner("Yahoo Finance에서 종목명 조회 중..."):
                    for s in stocks:
                        try:
                            info = get_stock_info(s["ticker"])
                            en_name = info.get("name", "")
                            if en_name and en_name != s["ticker"] and en_name != s.get("name", ""):
                                update_stock_name(s["id"], en_name)
                                updated.append(f"{s['ticker']}: {s.get('name','')} → {en_name}")
                        except Exception:
                            pass
                if updated:
                    st.success("업데이트 완료:\n" + "\n".join(updated))
                    st.rerun()
                else:
                    st.info("모든 종목명이 이미 최신입니다.")

        elif bulk_sel == "📅 연도/날짜 일괄 변경":
            st.caption("날짜를 바꾸면 해당 날짜의 실제 환율도 자동으로 함께 업데이트됩니다.")
            existing_years = sorted({s["purchase_date"][:4] for s in stocks if s.get("purchase_date")}, reverse=True)
            yc1, yc2 = st.columns(2)
            with yc1:
                from_year = st.selectbox("변경 전 연도", existing_years, key="year_from")
            with yc2:
                to_year = st.number_input("변경 후 연도", min_value=2000, max_value=datetime.now().year,
                                          value=datetime.now().year, step=1, key="year_to")
            target_stocks = [s for s in stocks if s.get("purchase_date", "").startswith(str(from_year))]
            if not target_stocks:
                st.info(f"{from_year}년 항목이 없습니다.")
            else:
                st.markdown(f"**{from_year}년 항목 {len(target_stocks)}건** — 변경할 항목 선택:")
                selected_ids = []
                for s in target_stocks:
                    broker_tag = f" | {s.get('broker','')}" if s.get("broker") else ""
                    label = (f"{s['name']} ({s['ticker']}) | {s['purchase_date']}"
                             f" | {s.get('account_type','일반계좌')}{broker_tag}"
                             f" | {s['purchase_currency']} {s['purchase_price']:,.2f} × {s['quantity']:.0f}주")
                    if st.checkbox(label, value=False, key=f"yrchk_{s['id']}"):
                        selected_ids.append(s["id"])
                if selected_ids:
                    if st.button(f"✅ {len(selected_ids)}건 → {to_year}년으로 변경 (날짜+환율)", type="primary", key="confirm_year_change"):
                        rate_log = []
                        with st.spinner("날짜 및 환율 업데이트 중..."):
                            for s in target_stocks:
                                if s["id"] not in selected_ids:
                                    continue
                                new_date = str(to_year) + s["purchase_date"][4:]
                                update_stock_date(s["id"], new_date)
                                currency = s["purchase_currency"]
                                if currency != "KRW":
                                    new_rate, actual_d = get_historical_exchange_rate(currency, new_date)
                                    update_stock_exchange_rate(s["id"], new_rate)
                                    rate_log.append(
                                        f"• {s['name']}: {new_date} → "
                                        f"{CURRENCY_LABELS.get(currency, currency)} 환율 **{new_rate:,.2f}원** ({actual_d} 기준)"
                                    )
                        st.success(f"✅ {len(selected_ids)}건 날짜+환율 변경 완료 ({from_year} → {to_year})")
                        if rate_log:
                            st.markdown("\n".join(rate_log))
                        st.rerun()

        elif bulk_sel == "🏦 증권사 일괄 설정":
            st.caption("종목별로 증권사를 선택하고 저장합니다.")
            broker_changes = {}
            for s in stocks:
                cur_broker = s.get("broker", "") or ""
                cur_idx = BROKER_LIST.index(cur_broker) if cur_broker in BROKER_LIST else 0
                col_a, col_b = st.columns([3, 2])
                with col_a:
                    st.write(f"**{s['name']}** ({s['ticker']}) | {s['purchase_date']} | {s.get('account_type','')}")
                with col_b:
                    new_broker = st.selectbox("증권사", BROKER_LIST, index=cur_idx, key=f"bkchg_{s['id']}")
                    if new_broker != cur_broker:
                        broker_changes[s["id"]] = new_broker
            if broker_changes:
                if st.button(f"✅ {len(broker_changes)}건 증권사 변경 저장", type="primary"):
                    for sid, brk in broker_changes.items():
                        update_stock_broker(sid, brk)
                    st.success(f"{len(broker_changes)}건 증권사 업데이트 완료")
                    st.rerun()

        elif bulk_sel == "📁 계좌 종류 일괄 변경":
            st.caption("종목별로 계좌 종류를 변경합니다.")
            acct_changes = {}
            for s in stocks:
                cur_acct = s.get("account_type", "일반계좌")
                cur_idx = ACCOUNT_TYPES.index(cur_acct) if cur_acct in ACCOUNT_TYPES else 0
                col_a, col_b = st.columns([3, 2])
                with col_a:
                    broker_tag = f" [{s.get('broker','')}]" if s.get("broker") else ""
                    st.write(f"**{s['name']}** ({s['ticker']}) | {s['purchase_date']}{broker_tag}")
                with col_b:
                    new_acct = st.selectbox("계좌 종류", ACCOUNT_TYPES, index=cur_idx, key=f"acctchg_{s['id']}")
                    if new_acct != cur_acct:
                        acct_changes[s["id"]] = new_acct
            if acct_changes:
                if st.button(f"✅ {len(acct_changes)}건 계좌 종류 변경 저장", type="primary"):
                    for sid, acct in acct_changes.items():
                        update_stock_account_type(sid, acct)
                    st.success(f"{len(acct_changes)}건 계좌 종류 업데이트 완료")
                    st.rerun()

        elif bulk_sel == "📂 포트폴리오 그룹 일괄 변경":
            st.caption("종목별로 포트폴리오 그룹(개별주식 / 積立NISA)을 변경합니다.")
            grp_changes = {}
            for s in stocks:
                cur_grp = s.get("portfolio_group", "개별주식")
                cur_idx = PORTFOLIO_GROUPS.index(cur_grp) if cur_grp in PORTFOLIO_GROUPS else 0
                col_a, col_b = st.columns([3, 2])
                with col_a:
                    broker_tag = f" [{s.get('broker','')}]" if s.get("broker") else ""
                    st.write(f"**{s['name']}** ({s['ticker']}) | {s['purchase_date']}{broker_tag}")
                with col_b:
                    new_grp = st.selectbox("그룹", PORTFOLIO_GROUPS, index=cur_idx, key=f"grpchg_{s['id']}")
                    if new_grp != cur_grp:
                        grp_changes[s["id"]] = new_grp
            if grp_changes:
                if st.button(f"✅ {len(grp_changes)}건 그룹 변경 저장", type="primary"):
                    for sid, grp in grp_changes.items():
                        update_stock_group(sid, grp)
                    st.success(f"{len(grp_changes)}건 그룹 변경 완료")
                    st.rerun()

        elif bulk_sel == "✏️ 개별 필드 직접 수정":
            st.caption("각 항목의 모든 필드를 직접 수정하고 저장합니다.")
            for s in stocks:
                with st.expander(f"{s['name']} ({s['ticker']}) | {s['purchase_date']} | {s.get('broker','')} | {s.get('account_type','')}"):
                    ec1, ec2, ec3, ec4 = st.columns(4)
                    with ec1:
                        e_qty = st.number_input("수량", value=float(s["quantity"]), min_value=0.0001, key=f"ef_qty_{s['id']}")
                        e_price = st.number_input("평단가", value=float(s["purchase_price"]), min_value=0.0, key=f"ef_price_{s['id']}")
                    with ec2:
                        e_rate = st.number_input("적용환율(원)", value=float(s["purchase_exchange_rate"]), min_value=0.0,
                                                  key=f"ef_rate_{s['id']}", disabled=(s["purchase_currency"]=="KRW"))
                        cur_broker_idx = BROKER_LIST.index(s.get("broker","") or "") if (s.get("broker","") or "") in BROKER_LIST else 0
                        e_broker = st.selectbox("증권사", BROKER_LIST, index=cur_broker_idx, key=f"ef_broker_{s['id']}")
                    with ec3:
                        cur_acct = s.get("account_type","일반계좌")
                        e_acct = st.selectbox("계좌", ACCOUNT_TYPES,
                                               index=ACCOUNT_TYPES.index(cur_acct) if cur_acct in ACCOUNT_TYPES else 0,
                                               key=f"ef_acct_{s['id']}")
                        e_notes = st.text_input("메모", value=s.get("notes",""), key=f"ef_notes_{s['id']}")
                    with ec4:
                        st.write(f"통화: **{s['purchase_currency']}**")
                        st.write(f"기준일: {s['purchase_date']}")
                        if s["purchase_currency"] != "KRW":
                            if st.button("🔄 환율 재조회", key=f"ef_refetch_{s['id']}"):
                                with st.spinner("조회 중..."):
                                    new_r, actual_d = get_historical_exchange_rate(s["purchase_currency"], s["purchase_date"])
                                update_stock(s["id"], e_qty, e_price, new_r, e_acct, e_notes, broker=e_broker)
                                st.success(f"{new_r:,.2f}원 ({actual_d})")
                                st.rerun()
                        if st.button("💾 저장", key=f"ef_save_{s['id']}", type="primary"):
                            update_stock(s["id"], e_qty, e_price, e_rate, e_acct, e_notes, broker=e_broker)
                            st.success("저장됨")
                            st.rerun()
                        if st.button("🗑️ 삭제", key=f"ef_del_{s['id']}"):
                            delete_stock(s["id"])
                            st.warning(f"{s['name']} 삭제됨")
                            st.rerun()

        elif bulk_sel == "🗑️ 일괄 삭제":
            del_tab1, del_tab2 = st.tabs(["종목 전체 삭제", "항목 선택 삭제"])
            with del_tab1:
                st.caption("선택한 티커의 매수 내역을 모두 삭제합니다.")
                all_tickers = sorted({s["ticker"] for s in stocks})
                del_tickers = st.multiselect("티커 선택", all_tickers, key="del_by_ticker")
                if del_tickers:
                    target_ids = [s["id"] for s in stocks if s["ticker"] in del_tickers]
                    target_rows = [s for s in stocks if s["ticker"] in del_tickers]
                    st.warning("⚠️ 총 {}건 삭제됩니다:\n".format(len(target_ids))
                               + "\n".join(f"- {s['name']} ({s['ticker']}) | {s['purchase_date']}" for s in target_rows))
                    if st.button("선택 종목 전체 삭제", type="primary", key="confirm_del_ticker"):
                        for sid in target_ids:
                            delete_stock(sid)
                        st.success(f"{len(target_ids)}건 삭제 완료")
                        st.rerun()
            with del_tab2:
                st.caption("개별 항목을 체크해서 선택 삭제합니다.")
                del_selected_ids = []
                for s in stocks:
                    label = (f"{s['ticker']} — {s['name']} | {s['purchase_date']}"
                             f" | {s['purchase_currency']} {s['purchase_price']:,.2f} × {s['quantity']:.0f}주"
                             f" | {s.get('account_type','')} | {s.get('broker','')}")
                    if st.checkbox(label, key=f"delchk_{s['id']}"):
                        del_selected_ids.append(s["id"])
                if del_selected_ids:
                    if st.button(f"선택 {len(del_selected_ids)}건 삭제", type="primary", key="confirm_del_items"):
                        for sid in del_selected_ids:
                            delete_stock(sid)
                        st.success(f"{len(del_selected_ids)}건 삭제 완료")
                        st.rerun()

        st.markdown("---")

        # ── 종목 필터 + 차트 기간 ─────────────────────────────
        _filt_col1, _filt_col2 = st.columns([3, 2])
        with _filt_col1:
            _unique_tickers = []
            _seen_tk = set()
            for _s in stocks:
                if _s["ticker"] not in _seen_tk:
                    _seen_tk.add(_s["ticker"])
                    _unique_tickers.append(f"{_s['ticker']} — {_s['name']}")
            _filter_opts = ["전체 보기"] + _unique_tickers
            _filter_sel = st.selectbox(
                "🔍 종목 필터",
                _filter_opts,
                key="stock_list_filter",
            )
        with _filt_col2:
            chart_days = st.select_slider(
                "차트 조회 기간",
                options=[30, 60, 90, 180, 365],
                value=90,
                format_func=lambda x: f"{x}일" if x < 365 else "1년",
            )

        # 필터 적용
        if _filter_sel == "전체 보기":
            _filtered_stocks = stocks
        else:
            _filter_ticker = _filter_sel.split(" — ")[0]
            _filtered_stocks = [s for s in stocks if s["ticker"] == _filter_ticker]

        st.caption(f"총 {len(stocks)}건 중 {len(_filtered_stocks)}건 표시")
        st.markdown("")

        @st.cache_data(ttl=1800, show_spinner=False)
        def _price_history_cached(ticker: str, days: int) -> pd.DataFrame:
            return get_price_history(ticker, days)

        # 매도 이력 요약 (티커별 실현손익)
        sold_summary = {r["ticker"]: r for r in get_sold_summary_by_ticker()}

        for stock in _filtered_stocks:
            sym = {"USD": "$", "JPY": "¥", "KRW": "₩"}.get(stock["purchase_currency"], "$")
            currency = stock["purchase_currency"]
            acct = stock.get("account_type", "일반계좌")
            broker_tag = f" | {stock.get('broker','')}" if stock.get("broker") else ""
            nisa_tag = " 🟢NISA" if acct in NISA_ACCOUNT_TYPES else ""
            _jpy_r_now = _jpy_rate_cached()
            if currency == "USD" or _get_stock_native_currency(stock["ticker"]) == "USD":
                _p_rate = stock["purchase_exchange_rate"]
                if _p_rate > 1 and _jpy_r_now > 0:
                    _usd_jpy_at_buy = _p_rate / _jpy_r_now
                else:
                    # 환율 미저장 → 현재 환율 참고 표시
                    _usd_krw_now = get_exchange_rate("USD")
                    _usd_jpy_at_buy = _usd_krw_now / _jpy_r_now if _jpy_r_now > 0 else 0
                rate_info = f" | 적용환율: ¥{_usd_jpy_at_buy:.0f}/USD" if _usd_jpy_at_buy > 0 else ""
            else:
                rate_info = ""

            with st.expander(
                f"**{stock['name']}** ({stock['ticker']}) — "
                f"{sym}{stock['purchase_price']:,.2f} × {stock['quantity']:.0f}주 | "
                f"기준일: {stock['purchase_date']}{broker_tag}{rate_info}{nisa_tag}"
            ):
                tab_main, tab_history, tab_tax = st.tabs(["📊 현황 & 차트", "📋 매매 이력", "💴 세금 정보"])

                with st.spinner("차트 로딩 중..."):
                    df_price = _price_history_cached(stock["ticker"], chart_days)

                # ══ 탭1: 현황 & 차트 ══════════════════════════════
                with tab_main:
                    if not df_price.empty:
                        fig_price = go.Figure()
                        fig_price.add_trace(go.Scatter(
                            x=df_price.index, y=df_price["price"],
                            mode="lines", name="주가",
                            line=dict(color="#89b4fa", width=2),
                            hovertemplate="%{x|%Y-%m-%d}<br>" + f"{sym}%{{y:,.2f}}<extra></extra>",
                        ))
                        fig_price.add_hline(
                            y=stock["purchase_price"], line_dash="dash", line_color="#f38ba8",
                            annotation_text=f"평단가 {sym}{stock['purchase_price']:,.2f}",
                            annotation_position="bottom right",
                        )
                        last_price = float(df_price["price"].iloc[-1])
                        last_date = df_price.index[-1]
                        color_marker = "#a6e3a1" if last_price >= stock["purchase_price"] else "#f38ba8"
                        fig_price.add_trace(go.Scatter(
                            x=[last_date], y=[last_price], mode="markers+text",
                            marker=dict(size=10, color=color_marker),
                            text=[f"{sym}{last_price:,.2f}"], textposition="top center",
                            name="현재가",
                            hovertemplate=f"현재가: {sym}{last_price:,.2f}<extra></extra>",
                        ))
                        fig_price.update_layout(
                            height=280, margin=dict(t=10, b=10, l=0, r=0),
                            showlegend=False, hovermode="x unified",
                            xaxis_title="", yaxis_title=f"주가 ({currency})",
                        )
                        st.plotly_chart(fig_price, use_container_width=True, key=f"chart_{stock['id']}")

                        with st.expander("📅 특정 날짜 금액 확인"):
                            min_d = df_price.index[0].date()
                            max_d = df_price.index[-1].date()
                            sel_date = st.date_input("날짜 선택", value=max_d,
                                min_value=min_d, max_value=max_d, key=f"datecheck_{stock['id']}")
                            df_idx = df_price.index.normalize()
                            sel_ts = pd.Timestamp(sel_date)
                            if df_idx.tz is not None:
                                sel_ts = sel_ts.tz_localize(df_idx.tz)
                            before = df_price[df_idx <= sel_ts]
                            if before.empty:
                                st.caption("선택 날짜 이전 데이터가 없습니다.")
                            else:
                                actual_date_str = before.index[-1].strftime("%Y-%m-%d")
                                price_at = float(before.iloc[-1]["price"])
                                curr_rate_now = get_exchange_rate(currency)
                                val_jpy = price_at * curr_rate_now * stock["quantity"] / _jpy_rate_cached()
                                change_pct = ((price_at - stock["purchase_price"]) / stock["purchase_price"] * 100) if stock["purchase_price"] > 0 else 0
                                arrow = "▲" if change_pct >= 0 else "▼"
                                dc1, dc2 = st.columns(2)
                                with dc1:
                                    st.metric(f"주가 ({actual_date_str})", f"{sym}{price_at:,.2f}", f"{arrow}{abs(change_pct):.2f}%")
                                with dc2:
                                    st.metric("평가액 (¥)", f"¥{val_jpy:,.0f}")
                    else:
                        st.caption("주가 데이터를 불러올 수 없습니다.")

                    st.markdown("---")

                    # ── 편집 + 매도 ────────────────────────────────
                    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
                    with col1:
                        new_qty = st.number_input("수량", value=float(stock["quantity"]),
                            min_value=0.0001, key=f"qty_{stock['id']}",
                            help="매도로 수량이 줄어들 수 있습니다.")
                        new_price = st.number_input("평단가", value=float(stock["purchase_price"]),
                            min_value=0.0, key=f"price_{stock['id']}",
                            help="이 로트의 단가. 이동평균법 적용 시 매도 후에도 유지됩니다.")
                    with col2:
                        _rate_lbl = {
                            "JPY": "적용환율 (1엔=?원)",
                            "USD": "적용환율 (1달러=?원)",
                            "KRW": "환율 불필요 (원화)",
                        }.get(currency, "적용환율 (원)")
                        new_rate = st.number_input(_rate_lbl,
                            value=float(stock["purchase_exchange_rate"]), min_value=0.0,
                            key=f"rate_{stock['id']}", disabled=(currency == "KRW"),
                            help="기준일 기준 자동 조회된 값. 원화 계산에 사용됩니다. 필요시 수정 가능.")
                        new_acct = st.selectbox("계좌 종류", ACCOUNT_TYPES,
                            index=ACCOUNT_TYPES.index(acct) if acct in ACCOUNT_TYPES else 0,
                            key=f"acct_{stock['id']}")
                    with col3:
                        cur_broker_idx = BROKER_LIST.index(stock.get("broker","") or "") if (stock.get("broker","") or "") in BROKER_LIST else 0
                        new_broker = st.selectbox("증권사", BROKER_LIST, index=cur_broker_idx, key=f"broker_{stock['id']}")
                        new_notes = st.text_input("메모", value=stock.get("notes",""), key=f"notes_{stock['id']}")
                        if currency != "KRW":
                            if st.button("🔄 환율 재조회", key=f"refetch_{stock['id']}"):
                                with st.spinner("환율 조회 중..."):
                                    new_r, actual_d = get_historical_exchange_rate(currency, stock["purchase_date"])
                                update_stock(stock["id"], new_qty, new_price, new_r, new_acct, new_notes, broker=new_broker)
                                _jpy_r2 = _jpy_rate_cached()
                                if currency == "USD":
                                    _usdjpy2 = new_r / _jpy_r2 if _jpy_r2 > 0 else 0
                                    st.success(f"재조회 완료: ¥{_usdjpy2:.0f}/USD  (₩{new_r:,.2f}, {actual_d})")
                                elif currency == "JPY":
                                    st.success(f"재조회 완료: 1엔=₩{new_r:,.2f} ({actual_d})")
                                else:
                                    st.success(f"재조회 완료: {new_r:,.2f}원 ({actual_d})")
                                st.rerun()
                    with col4:
                        if st.button("💾 저장", key=f"save_{stock['id']}", type="primary"):
                            update_stock(stock["id"], new_qty, new_price, new_rate, new_acct, new_notes, broker=new_broker)
                            st.success("저장됨")
                            st.rerun()
                        if st.button("🗑️ 삭제", key=f"del_{stock['id']}", type="secondary"):
                            delete_stock(stock["id"])
                            st.warning(f"{stock['name']} 삭제됨")
                            st.rerun()

                    # ── 매도 기록 추가 ──────────────────────────────
                    st.markdown("---")
                    with st.expander("📤 매도 기록 추가"):
                        st.caption("일부 수량만 매도하는 경우도 지원합니다. 매도일의 환율이 자동으로 조회됩니다.")
                        sell_c1, sell_c2, sell_c3 = st.columns(3)
                        with sell_c1:
                            sell_qty = st.number_input("매도 수량",
                                min_value=0.0001, max_value=float(stock["quantity"]),
                                value=float(stock["quantity"]), step=1.0,
                                key=f"sell_qty_{stock['id']}")
                            sell_price = st.number_input(f"매도가 ({currency})",
                                min_value=0.0, value=float(stock["purchase_price"]), step=0.01,
                                key=f"sell_price_{stock['id']}")
                        with sell_c2:
                            sell_date_val = st.date_input("매도일", value=date.today(), key=f"sell_date_{stock['id']}")
                            sell_notes = st.text_input("매도 메모", key=f"sell_notes_{stock['id']}")
                        with sell_c3:
                            # 예상 실현손익 미리보기
                            if currency != "KRW":
                                st.info("💡 확인 버튼을 누르면 매도일 환율을 자동 조회해 실현손익을 계산합니다.")
                            else:
                                sell_fx = 1.0
                                buy_krw = stock["purchase_price"] * stock["purchase_exchange_rate"] * sell_qty
                                sell_krw = sell_price * sell_qty
                                preview_gain = sell_krw - buy_krw
                                preview_pct = (preview_gain / buy_krw * 100) if buy_krw > 0 else 0
                                gain_color = "🟢" if preview_gain >= 0 else "🔴"
                                st.metric("예상 실현손익", format_krw(preview_gain), f"{preview_pct:+.2f}%")

                        if st.button("✅ 매도 확인 및 저장", type="primary", key=f"sell_confirm_{stock['id']}"):
                            with st.spinner("환율 조회 및 매도 처리 중..."):
                                sell_date_str = str(sell_date_val)
                                if currency != "KRW":
                                    sell_fx, sell_fx_date = get_historical_exchange_rate(currency, sell_date_str)
                                else:
                                    sell_fx, sell_fx_date = 1.0, sell_date_str
                                buy_krw_total = stock["purchase_price"] * stock["purchase_exchange_rate"] * sell_qty
                                sell_krw_total = sell_price * sell_fx * sell_qty
                                realized_gain = sell_krw_total - buy_krw_total
                                realized_pct = (realized_gain / buy_krw_total * 100) if buy_krw_total > 0 else 0
                                # 매도 이력 저장
                                add_sold_record(
                                    ticker=stock["ticker"], name=stock["name"],
                                    broker=stock.get("broker",""), account_type=acct,
                                    purchase_date=stock["purchase_date"],
                                    purchase_price=stock["purchase_price"],
                                    purchase_currency=currency,
                                    purchase_exchange_rate=stock["purchase_exchange_rate"],
                                    sell_date=sell_date_str, sell_price=sell_price,
                                    sell_currency=currency, sell_exchange_rate=sell_fx,
                                    quantity=sell_qty,
                                    realized_gain_krw=realized_gain, realized_gain_pct=realized_pct,
                                    notes=sell_notes,
                                )
                                # 잔여 수량 업데이트 또는 삭제
                                remaining = stock["quantity"] - sell_qty
                                _jpy_r_sell = _jpy_rate_cached()
                                _gain_jpy = realized_gain / _jpy_r_sell if _jpy_r_sell > 0 else realized_gain
                                if remaining < 0.0001:
                                    delete_stock(stock["id"])
                                    st.success(f"✅ {stock['name']} 전량 매도 완료! 실현손익: ¥{_gain_jpy:,.0f} ({realized_pct:+.2f}%)")
                                else:
                                    update_stock(stock["id"], remaining, stock["purchase_price"],
                                                 stock["purchase_exchange_rate"], acct, stock.get("notes",""),
                                                 broker=stock.get("broker",""))
                                    _sell_rate_note = ""
                                    if currency == "USD":
                                        _sell_usdjpy = sell_fx / _jpy_r_sell if _jpy_r_sell > 0 else 0
                                        _sell_rate_note = f" | 매도환율: ¥{_sell_usdjpy:.0f}/USD"
                                    elif currency == "JPY":
                                        _sell_rate_note = f" | 1엔=₩{sell_fx:,.2f}"
                                    st.success(
                                        f"✅ {sell_qty:.0f}주 매도 완료! 잔여: {remaining:.0f}주\n"
                                        f"실현손익: ¥{_gain_jpy:,.0f} ({realized_pct:+.2f}%){_sell_rate_note}"
                                    )
                            st.rerun()

                # ══ 탭2: 매매 이력 ════════════════════════════════
                with tab_history:
                    ticker_sold = get_sold_history(ticker=stock["ticker"])
                    # 매수 이력 (이 티커의 모든 portfolio 항목)
                    buy_entries = [s for s in stocks if s["ticker"] == stock["ticker"]]

                    # 누적 실현손익
                    s_summary = sold_summary.get(stock["ticker"])
                    if s_summary:
                        rc1, rc2, rc3 = st.columns(3)
                        with rc1:
                            st.metric("총 매도 횟수", f"{s_summary['sell_count']}회")
                        with rc2:
                            st.metric("총 매도 수량", f"{s_summary['total_sold_qty']:.0f}주")
                        with rc3:
                            rg = s_summary["total_realized_gain_krw"] or 0
                            _jpy_r_hist = _jpy_rate_cached()
                            rg_jpy = rg / _jpy_r_hist if _jpy_r_hist > 0 else rg
                            st.metric("누적 실현손익 (¥)",
                                      f"¥{rg_jpy:,.0f}",
                                      delta=f"{'▲' if rg>=0 else '▼'} 실현")
                        st.markdown("---")

                    # 타임라인 테이블
                    _jpy_r_tl = _jpy_rate_cached()

                    def _rate_disp(rate_val, curr):
                        if curr == "JPY":
                            return f"₩{rate_val:,.2f}/¥"
                        elif curr == "USD":
                            usdjpy = rate_val / _jpy_r_tl if _jpy_r_tl > 0 else 0
                            return f"¥{usdjpy:.0f}/$"
                        return "-"

                    timeline_rows = []
                    for b in buy_entries:
                        timeline_rows.append({
                            "날짜": b["purchase_date"],
                            "구분": "📈 매수",
                            "수량": f"{b['quantity']:.0f}주",
                            "단가": f"{sym}{b['purchase_price']:,.2f}",
                            "환율(참고)": _rate_disp(b["purchase_exchange_rate"], currency),
                            "증권사": b.get("broker","") or "-",
                            "계좌": b.get("account_type",""),
                            "손익": "-",
                            "메모": b.get("notes",""),
                        })
                    for s_rec in ticker_sold:
                        rg = s_rec.get("realized_gain_krw") or 0
                        rp = s_rec.get("realized_gain_pct") or 0
                        rg_jpy = rg / _jpy_r_tl if _jpy_r_tl > 0 else rg
                        timeline_rows.append({
                            "날짜": s_rec["sell_date"],
                            "구분": "📉 매도",
                            "수량": f"{s_rec['quantity']:.0f}주",
                            "단가": f"{sym}{s_rec['sell_price']:,.2f}",
                            "환율(참고)": _rate_disp(s_rec["sell_exchange_rate"], currency),
                            "증권사": s_rec.get("broker","") or "-",
                            "계좌": s_rec.get("account_type",""),
                            "손익": f"¥{rg_jpy:,.0f} ({rp:+.2f}%)",
                            "메모": s_rec.get("notes",""),
                        })
                    if timeline_rows:
                        df_timeline = pd.DataFrame(sorted(timeline_rows, key=lambda x: x["날짜"], reverse=True))
                        st.dataframe(df_timeline, use_container_width=True, hide_index=True)

                        # 매도 이력 삭제
                        if ticker_sold:
                            with st.expander("매도 이력 삭제"):
                                for sr in ticker_sold:
                                    rg = sr.get("realized_gain_krw") or 0
                                    rg_jpy_del = rg / _jpy_r_tl if _jpy_r_tl > 0 else rg
                                    label = f"{sr['sell_date']} | {sr['quantity']:.0f}주 | {sym}{sr['sell_price']:,.2f} | ¥{rg_jpy_del:,.0f}"
                                    if st.checkbox(label, key=f"del_sold_{stock['id']}_{sr['id']}"):
                                        if st.button("이 항목 삭제", key=f"do_del_sold_{stock['id']}_{sr['id']}"):
                                            delete_sold_record(sr["id"])
                                            st.success("삭제됨")
                                            st.rerun()
                    else:
                        st.info("매매 이력이 없습니다.")

                # ══ 탭3: 세금 정보 ════════════════════════════════
                with tab_tax:
                    tax_rate_val = TAX_RATE.get(acct, 0.20315)
                    curr_price_now = float(df_price["price"].iloc[-1]) if not df_price.empty else stock["purchase_price"]
                    jpy_r = _jpy_rate_cached()

                    # 미실현 손익
                    unrealized_krw = (curr_price_now - stock["purchase_price"]) * stock["purchase_exchange_rate"] * stock["quantity"]
                    unrealized_pct = (unrealized_krw / (stock["purchase_price"] * stock["purchase_exchange_rate"] * stock["quantity"]) * 100) if stock["purchase_price"] > 0 else 0
                    # JPY 직접 계산 (JPY 종목은 환율 변환 불필요)
                    if currency == "JPY":
                        unrealized_jpy = (curr_price_now - stock["purchase_price"]) * stock["quantity"]
                    else:
                        unrealized_jpy = unrealized_krw / jpy_r if jpy_r > 0 else unrealized_krw

                    # 실현 손익
                    s_sum = sold_summary.get(stock["ticker"])
                    realized_total = s_sum["total_realized_gain_krw"] if s_sum else 0
                    realized_jpy = (realized_total or 0) / jpy_r if jpy_r > 0 else (realized_total or 0)

                    # 누적 손익 = 실현 + 미실현
                    total_combined_jpy = realized_jpy + unrealized_jpy

                    tc1, tc2, tc3 = st.columns(3)
                    with tc1:
                        st.metric("미실현 손익 (보유중)", f"¥{unrealized_jpy:,.0f}", f"{unrealized_pct:+.2f}%")
                    with tc2:
                        st.metric("실현 손익 (매도 합계)", f"¥{realized_jpy:,.0f}")
                    with tc3:
                        st.metric("누적 손익 (실현+미실현)", f"¥{total_combined_jpy:,.0f}")

                    st.markdown("---")
                    if tax_rate_val == 0.0:
                        st.success("🟢 NISA 계좌 — 비과세 (세율 0%)\n\n매각익·배당금 모두 비과세입니다.")
                    else:
                        est_gain_native = max(0.0, (curr_price_now - stock["purchase_price"]) * stock["quantity"])
                        if currency == "JPY":
                            est_tax_jpy = est_gain_native * tax_rate_val  # JPY 직접 계산
                            est_tax_krw = est_tax_jpy * jpy_r
                        else:
                            est_tax_krw = est_gain_native * stock["purchase_exchange_rate"] * tax_rate_val
                            est_tax_jpy = est_tax_krw / jpy_r if jpy_r > 0 else 0
                        src = "자동 원천징수" if "원천징수" in acct else "확정신고 필요"
                        st.warning(
                            f"⚠️ {acct} — 세율 20.315% (소득세 15.315% + 지방세 5%)\n\n"
                            f"납세 방법: {src}\n\n"
                            f"현재 보유분 예상 세금: ¥{est_tax_jpy:,.0f}  (₩{est_tax_krw:,.0f})\n\n"
                            f"(현재 평가 기준, 실제와 다를 수 있음)"
                        )
                        st.caption(f"증권사: {stock.get('broker','미설정')} | 통화: {currency} | 기준일: {stock['purchase_date']}")




# ══════════════════════════════════════════════════════════════
# 페이지: 투자 추천
# ══════════════════════════════════════════════════════════════
elif page == "투자 추천":
    st.title("💡 투자 추천")

    _api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not _api_key:
        st.warning("⚙️ 설정 메뉴에서 **Anthropic API 키**를 먼저 입력하세요.")
        st.stop()

    # ── 관심 키워드 등록 & 관련 종목 자동 표시 ──────────────────
    _saved_kw_str = get_setting("invest_watchlist_keywords", "")
    _saved_keywords = [k.strip() for k in _saved_kw_str.split(",") if k.strip()] if _saved_kw_str else []

    with st.expander("⭐ 관심 키워드 등록 (등록하면 관련 종목 3개 자동 표시)", expanded=not _saved_keywords):
        # 등록된 키워드 버튼 표시
        if _saved_keywords:
            _ikw_cols = st.columns(min(len(_saved_keywords), 6))
            _ikw_to_remove = None
            for _ki, _kw in enumerate(_saved_keywords):
                with _ikw_cols[_ki % min(len(_saved_keywords), 6)]:
                    if st.button(f"❌ {_kw}", key=f"ikw_del_{_ki}", use_container_width=True):
                        _ikw_to_remove = _kw
            if _ikw_to_remove:
                _saved_keywords.remove(_ikw_to_remove)
                save_setting("invest_watchlist_keywords", ", ".join(_saved_keywords))
                st.rerun()

        # 새 키워드 추가
        _ikw_c1, _ikw_c2 = st.columns([3, 1])
        with _ikw_c1:
            _ikw_new = st.text_input(
                "키워드 추가",
                placeholder="예: HBM, AI 데이터센터, 방산, 원전",
                key="ikw_new_input",
                label_visibility="collapsed",
            )
        with _ikw_c2:
            if st.button("➕ 추가", key="ikw_add", use_container_width=True):
                if _ikw_new and _ikw_new.strip():
                    _new_kw = _ikw_new.strip()
                    if _new_kw not in _saved_keywords:
                        _saved_keywords.append(_new_kw)
                        save_setting("invest_watchlist_keywords", ", ".join(_saved_keywords))
                        st.rerun()
                    else:
                        st.warning(f"'{_new_kw}'는 이미 등록되어 있습니다.")

    if _saved_keywords:
        _my_stocks_kw = get_all_stocks()
        _my_list_kw = ", ".join(f"{s['ticker']}({s['name']})" for s in _my_stocks_kw) if _my_stocks_kw else "없음"
        _my_tickers_kw = {s["ticker"].upper() for s in _my_stocks_kw}

        # 키워드별 결과를 미리 조회 (캐시 키 = "kw_rec_{keyword}")
        _kw_results = {}
        with st.spinner("관심 키워드 관련 종목 조회 중..."):
            for _kw in _saved_keywords:
                _cache_key = f"kw_rec_{_kw}"
                if _cache_key not in st.session_state:
                    try:
                        import anthropic as _anth_kw_rec
                        _client = _anth_kw_rec.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
                        _msg = _client.messages.create(
                            model="claude-haiku-4-5-20251001",
                            max_tokens=600,
                            messages=[{"role": "user", "content": (
                                f"'{_kw}' 키워드와 관련된 주식 종목 3개를 추천해주세요.\n\n"
                                f"사용자 보유 종목: {_my_list_kw}\n\n"
                                f"각 종목마다 아래 형식으로 답변:\n"
                                f"1. **종목명 (티커)** — 한 줄 추천 이유\n"
                                f"2. **종목명 (티커)** — 한 줄 추천 이유\n"
                                f"3. **종목명 (티커)** — 한 줄 추천 이유\n\n"
                                f"규칙:\n"
                                f"- 티커는 Yahoo Finance 형식 (미국: AAPL, 일본: 7203.T, 한국: 005930.KS)\n"
                                f"- 보유 종목이 포함되면 🟢 표시\n"
                                f"- 한국어로, 번호 리스트만 답변 (추가 설명 없이)"
                            )}],
                        )
                        st.session_state[_cache_key] = _msg.content[0].text.strip()
                    except Exception as _e:
                        st.session_state[_cache_key] = f"조회 실패: {_e}"
                _kw_results[_kw] = st.session_state[_cache_key]

        # 3개씩 한 줄에 배치
        for _row_start in range(0, len(_saved_keywords), 3):
            _row_kws = _saved_keywords[_row_start:_row_start + 3]
            _kw_cols = st.columns(len(_row_kws))
            for _ki, _kw in enumerate(_row_kws):
                with _kw_cols[_ki]:
                    st.markdown(f"#### 🏷️ {_kw} 관련 추천 종목")
                    _kw_result = _kw_results.get(_kw, "")
                    # 보유 종목 하이라이트
                    for _tk in _my_tickers_kw:
                        if _tk in _kw_result:
                            _kw_result = _kw_result.replace(
                                _tk,
                                f'<span style="background:#e8f5e9;padding:1px 4px;border-radius:3px;">{_tk}</span>'
                            )
                    st.markdown(_kw_result, unsafe_allow_html=True)
                    if st.button(f"🔍 상세 분석", key=f"kw_detail_{_row_start + _ki}"):
                        st.session_state.invest_keyword = _kw
                        st.session_state.invest_result = None
                        st.session_state.inv_mode = "🔍 키워드·트렌드 → 종목 추천"
                        st.rerun()

        # 새로고침 버튼
        if st.button("🔄 추천 종목 새로고침"):
            for _kw in _saved_keywords:
                _ck = f"kw_rec_{_kw}"
                if _ck in st.session_state:
                    del st.session_state[_ck]
            st.rerun()

        st.markdown("---")

    # ── 분석 모드 선택 ─────────────────────────────────────────
    if "invest_result" not in st.session_state:
        st.session_state.invest_result = None
    if "invest_keyword" not in st.session_state:
        st.session_state.invest_keyword = ""

    _inv_mode = st.radio(
        "분석 모드",
        ["🔍 키워드·트렌드 → 종목 추천", "📊 개별 종목 전망 분석"],
        horizontal=True, key="inv_mode",
    )

    _inv_col1, _inv_col2 = st.columns([5, 1])
    with _inv_col1:
        if _inv_mode == "📊 개별 종목 전망 분석":
            _inv_keyword = st.text_input(
                "티커 또는 종목명 입력",
                placeholder="예: NVDA, TSLA, AVGO, 7203.T, 삼성전자 등",
                value=st.session_state.invest_keyword,
            )
        else:
            _inv_keyword = st.text_input(
                "키워드 · 기술 · 트렌드 입력",
                placeholder="예: HBM, AI 데이터센터, 방산, 이차전지, 원전 등",
                value=st.session_state.invest_keyword,
            )
    with _inv_col2:
        st.write("")
        st.write("")
        _inv_go = st.button("🔍 분석", type="primary", use_container_width=True)

    if _inv_go and _inv_keyword.strip():
        st.session_state.invest_keyword = _inv_keyword.strip()

        _my_stocks = get_all_stocks()
        _my_tickers = {s["ticker"].upper() for s in _my_stocks}
        _my_list_str = ", ".join(f"{s['ticker']}({s['name']})" for s in _my_stocks) if _my_stocks else "없음"

        if _inv_mode == "📊 개별 종목 전망 분석":
            # ── 개별 종목 전망 분석 프롬프트 ──────────────────
            _is_held = _inv_keyword.strip().upper() in _my_tickers
            _held_info = ""
            if _is_held:
                _held_stocks = [s for s in _my_stocks if s["ticker"].upper() == _inv_keyword.strip().upper()]
                _held_qty = sum(s["quantity"] for s in _held_stocks)
                _held_avg = sum(s["purchase_price"] * s["quantity"] for s in _held_stocks) / _held_qty if _held_qty > 0 else 0
                _held_info = f"\n사용자 보유 현황: {_held_qty:,.2f}주, 평단가 {_held_avg:,.2f}"

            _inv_prompt = f"""당신은 글로벌 주식 투자 애널리스트입니다.
사용자가 분석을 요청한 종목: **{_inv_keyword.strip()}**
사용자의 전체 보유 종목: {_my_list_str}{_held_info}

아래 형식으로 한국어로 상세하게 답변해주세요:

## 📊 {_inv_keyword.strip()} 종목 전망 분석

### 🏢 기업 개요
- 정식 명칭, 티커, 섹터/산업
- 핵심 사업 내용 (2~3줄)
- 시가총액 규모, 글로벌 포지션

### 📈 투자 추천 근거 (Bull Case)
- 왜 지금 사야 하는지 3~5가지 이유
- 성장 동력, 경쟁 우위, 시장 기회
- 관련 **상승** 요인은 **굵은 글씨**로 표시

### 📉 투자 비추천 근거 (Bear Case)
- 왜 지금 사면 안 되는지 3~5가지 이유
- 리스크, 경쟁 위협, 밸류에이션 우려
- 관련 **하락** 요인은 **굵은 글씨**로 표시

### 📊 핵심 투자 지표
- PER(주가수익비율), PBR, PSR 등 밸류에이션
- 매출 성장률, 영업이익률
- 배당 유무 및 배당수익률
- 부채비율, 현금흐름

### 🌐 현재 주요 이슈
- 이 종목에 영향을 미치는 최근 뉴스/이벤트 3~5개
- 거시경제, 업계 동향, 규제 변화 등

### 📅 향후 일정 & 촉매
- 실적 발표 예정일
- 주요 컨퍼런스, 제품 출시, 규제 결정 등
- 주가에 영향을 줄 수 있는 이벤트 2~3개

### 💡 종합 의견
- 현재 시점에서의 투자 매력도를 한 줄로 요약
- 단기(1~3개월) vs 장기(1년+) 관점 분리

{"### 💼 보유 현황 분석" if _is_held else ""}
{"보유 중인 종목이므로: 현재 포지션에서 추가 매수/보유/매도 중 어떤 전략이 적합한지 의견 제시" if _is_held else ""}

규칙:
- 전체 2000자 이내
- 상승/하락/급등/급락/고점/저점/돌파/지지/저항 등 시장 방향성 단어는 반드시 **굵은 글씨**
- 티커는 Yahoo Finance 형식
- 투자 권유가 아닌 정보 제공 목적임을 명시
- 숫자와 데이터를 최대한 포함 (구체적으로)
"""
        else:
            # ── 키워드/트렌드 → 종목 추천 프롬프트 ───────────
            _inv_prompt = f"""당신은 글로벌 주식 투자 애널리스트입니다.
사용자가 관심 있는 키워드/기술/트렌드: **{_inv_keyword.strip()}**

사용자의 현재 보유 종목: {_my_list_str}

아래 형식으로 한국어로 답변해주세요:

## 📌 "{_inv_keyword.strip()}" 관련 투자 분석

### 🏆 업계 1위 기업
- 기업명 (티커)
- 왜 1위인지 3줄 이내로 설명

### 📋 추천 종목 3~5개
각 종목마다:
- **종목명 (티커)** — 한 줄 소개
- 추천 이유 (2~3줄)
- 최근 주가 동향: **상승** 또는 **하락** 관련 내용은 **굵은 글씨**로 표시
- 리스크 요인 1줄

### 💼 내 포트폴리오 연결
보유 종목 중 이 키워드와 관련된 종목이 있으면:
- 🟢 **[보유 중] 종목명 (티커)** — 키워드와의 연결점 설명
보유하지 않은 종목이면 언급하지 마세요.

### 📅 주목할 일정/이슈
향후 1~3개월 내 이 키워드와 관련된 주요 발표/이벤트가 있으면 2~3개 나열.

### ⚠️ 투자 유의사항
이 섹터/기술의 주요 리스크를 2~3줄로 정리.

규칙:
- 전체 1200자 이내
- 상승/하락/급등/급락/고점/저점 등 시장 방향성 단어는 반드시 **굵은 글씨**
- 티커는 Yahoo Finance 형식 (미국: AAPL, 일본: 7203.T, 한국: 005930.KS)
- 투자 권유가 아닌 정보 제공 목적임을 명시
"""

        with st.spinner(f"🔍 '{_inv_keyword.strip()}' 관련 종목 분석 중..."):
            try:
                import anthropic as _anth_inv
                _client = _anth_inv.Anthropic(api_key=_api_key)
                _msg = _client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": _inv_prompt}],
                )
                st.session_state.invest_result = _msg.content[0].text
            except Exception as _e:
                st.error(f"AI 분석 오류: {_e}")
                st.session_state.invest_result = None

    # ── 결과 표시 ──────────────────────────────────────────────
    _inv_result = st.session_state.invest_result
    if _inv_result:
        # 보유 종목 하이라이트 — [보유 중] 태그가 있으면 배경색 적용
        _my_stocks_hl = get_all_stocks()
        _my_tickers_hl = {s["ticker"].upper() for s in _my_stocks_hl}

        # 결과 텍스트에서 보유 종목 티커를 하이라이트 (HTML 배경색)
        _display_text = _inv_result
        for _tk in _my_tickers_hl:
            if _tk in _display_text:
                _display_text = _display_text.replace(
                    _tk,
                    f'<span style="background:#e8f5e9;padding:2px 6px;border-radius:4px;font-weight:bold;">'
                    f'🟢 {_tk}</span>'
                )

        st.markdown(_display_text, unsafe_allow_html=True)

        st.markdown("---")

        # ── 텔레그램 알림 전송 ─────────────────────────────────
        _inv_kw = st.session_state.invest_keyword
        if st.button(f"📱 이 분석을 텔레그램으로 전송", key="inv_tg"):
            _tg_msg = f"💡 투자 추천: {_inv_kw}\n\n{_inv_result[:4000]}"
            _tg_result = send_telegram_message(_tg_msg)
            if _tg_result.get("success"):
                st.success("✅ 텔레그램으로 전송 완료!")
            else:
                st.error(f"전송 실패: {_tg_result.get('error')}")

        st.caption("⚠️ 이 분석은 정보 제공 목적이며, 투자 권유가 아닙니다. 투자 결정은 본인의 판단으로 이루어져야 합니다.")

    elif not _inv_go:
        # 초기 안내
        if _inv_mode == "📊 개별 종목 전망 분석":
            st.info(
                "티커 또는 종목명을 입력하고 **🔍 분석**을 누르면 종목 전망을 분석합니다.\n\n"
                "**분석 내용:**\n"
                "- 기업 개요 · Bull Case (매수 근거) · Bear Case (매도 근거)\n"
                "- 핵심 투자 지표 (PER, 매출 성장률 등)\n"
                "- 현재 주요 이슈 · 향후 일정 & 촉매\n"
                "- 보유 중이면 추가 매수/보유/매도 전략 의견\n\n"
                "**예시:** `NVDA`, `TSLA`, `AVGO`, `7203.T`, `005930.KS`"
            )
        else:
            st.info(
                "키워드를 입력하고 **🔍 분석**을 누르면 관련 종목을 추천합니다.\n\n"
                "**예시 키워드:**\n"
                "- `HBM` — 고대역폭 메모리, 반도체\n"
                "- `AI 데이터센터` — 클라우드 인프라, GPU\n"
                "- `방산` — 방위 산업, 방위 관련 기업\n"
                "- `원전` — 원자력 발전, SMR\n"
                "- `이차전지` — 배터리, 전기차\n"
                "- `엔저` — 엔화 약세 수혜주"
            )

    # ── 뉴스 받아보기 ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("📰 뉴스 받아보기")
    st.caption("티커/키워드를 등록하면 스케줄러 알림 시간에 관련 뉴스 3개를 한국어로 번역해서 텔레그램으로 보내드립니다.")

    _saved_nw = get_setting("news_watchlist", "")
    _nw_keywords = [k.strip() for k in _saved_nw.split(",") if k.strip()] if _saved_nw else []
    _inv_kw_str2 = get_setting("invest_watchlist_keywords", "")
    if _inv_kw_str2:
        st.caption(f"위에서 등록한 관심 키워드도 자동 포함: **{_inv_kw_str2}**")

    # 등록된 키워드 버튼으로 표시 (클릭하면 삭제)
    if _nw_keywords:
        st.markdown("**등록된 키워드** (클릭하면 삭제)")
        _nw_cols = st.columns(min(len(_nw_keywords), 6))
        _nw_to_remove = None
        for _ki, _kw in enumerate(_nw_keywords):
            with _nw_cols[_ki % min(len(_nw_keywords), 6)]:
                if st.button(f"❌ {_kw}", key=f"nw_del_{_ki}", use_container_width=True):
                    _nw_to_remove = _kw
        if _nw_to_remove:
            _nw_keywords.remove(_nw_to_remove)
            save_setting("news_watchlist", ", ".join(_nw_keywords))
            st.rerun()

    # 새 키워드 추가
    _add_c1, _add_c2 = st.columns([3, 1])
    with _add_c1:
        _nw_new = st.text_input(
            "키워드 추가",
            placeholder="예: NVDA, TSLA, SPY, 8035.T",
            key="nw_new_input",
            label_visibility="collapsed",
        )
    with _add_c2:
        if st.button("➕ 추가", key="nw_add", use_container_width=True):
            if _nw_new and _nw_new.strip():
                _new_kw = _nw_new.strip()
                if _new_kw not in _nw_keywords:
                    _nw_keywords.append(_new_kw)
                    save_setting("news_watchlist", ", ".join(_nw_keywords))
                    st.rerun()
                else:
                    st.warning(f"'{_new_kw}'는 이미 등록되어 있습니다.")

    # 테스트 전송
    _inv_kws2 = [k.strip() for k in _inv_kw_str2.split(",") if k.strip()] if _inv_kw_str2 else []
    _all_wl = list(dict.fromkeys(_nw_keywords + _inv_kws2))
    if _all_wl:
        if st.button("📨 지금 뉴스 보내기 (테스트)", key="nw_test"):
            with st.spinner("뉴스 수집 및 번역 중..."):
                _news_msg = fetch_watchlist_news(_all_wl, max_per_item=3)
            if _news_msg:
                _result = send_telegram_message(_news_msg)
                if _result.get("success"):
                    st.success("✅ 뉴스를 텔레그램으로 전송했습니다!")
                else:
                    st.error(f"전송 실패: {_result.get('error')}")
            else:
                st.info("해당 키워드의 뉴스가 없습니다.")


# ══════════════════════════════════════════════════════════════
# 페이지: 경제적 자유 계획 계산기
# ══════════════════════════════════════════════════════════════
elif page == "경제적 자유 계획":
    st.title("🏖️ 경제적 자유 계획 계산기")
    st.caption("본인의 개인 자산만으로 경제적 자유를 달성하기 위한 계획을 세웁니다.")

    _api_key_fi = os.getenv("ANTHROPIC_API_KEY", "")
    _jpy_r_fi = _jpy_rate_cached()

    # ── 저장된 설정 로드 ──────────────────────────────────────
    _fi_saved = get_settings_bulk("fi_")
    if "fi_ai_result" not in st.session_state:
        st.session_state.fi_ai_result = _fi_saved.get("fi_ai_result_cache", None)

    def _fi_default(key, default, as_type=int):
        """저장된 값이 있으면 사용, 없으면 기본값"""
        v = _fi_saved.get(key, "")
        if v == "":
            return default
        try:
            return as_type(v)
        except (ValueError, TypeError):
            return default

    # ── 1. 기본 정보 입력 ─────────────────────────────────────
    st.subheader("1️⃣ 기본 정보")
    _fi_c1, _fi_c2, _fi_c3 = st.columns(3)
    with _fi_c1:
        _saved_birth = _fi_saved.get("fi_birth", "")
        _default_birth = date.fromisoformat(_saved_birth) if _saved_birth else date(1993, 1, 1)
        _fi_birth = st.date_input("생년월일", value=_default_birth,
                                   min_value=date(1960, 1, 1), max_value=date(2005, 12, 31),
                                   key="fi_birth")
        _fi_age = (date.today() - _fi_birth).days // 365
        st.caption(f"현재 나이: **{_fi_age}세**")

        _fi_target_age = st.number_input("목표 경제적 자유 나이", min_value=_fi_age + 1,
                                          max_value=80,
                                          value=_fi_default("fi_target_age", min(45, _fi_age + 15)),
                                          step=1, key="fi_target_age")
        _fi_years_left = _fi_target_age - _fi_age
        st.caption(f"남은 기간: **{_fi_years_left}년**")

    with _fi_c2:
        _fi_salary_annual = st.number_input("연봉 (세전, ¥)", min_value=0,
                                             value=_fi_default("fi_salary_annual", 0),
                                             step=100000, key="fi_salary_annual",
                                             help="세전 연봉. 입력하면 세금을 추정 계산합니다.")
        _fi_monthly_net = st.number_input("월 실수령액 (세후, ¥)", min_value=0,
                                           value=_fi_default("fi_monthly_net", 0),
                                           step=10000, key="fi_monthly_net",
                                           help="실제 통장에 들어오는 금액. 연봉과 함께 입력하면 실효세율을 계산합니다.")
        # 세금 계산
        if _fi_salary_annual > 0 and _fi_monthly_net > 0:
            _fi_annual_net = _fi_monthly_net * 12
            _fi_tax_total = _fi_salary_annual - _fi_annual_net
            _fi_tax_rate = (_fi_tax_total / _fi_salary_annual * 100) if _fi_salary_annual > 0 else 0
            st.info(f"💴 연간 세금+사회보험: **¥{_fi_tax_total:,.0f}** (실효세율 **{_fi_tax_rate:.1f}%**)")
        elif _fi_salary_annual > 0:
            # 일본 세금 추정 (소득세+주민세+사회보험 약 25-30%)
            _fi_est_rate = 0.25 if _fi_salary_annual < 5000000 else 0.28 if _fi_salary_annual < 8000000 else 0.32
            _fi_monthly_net = int(_fi_salary_annual * (1 - _fi_est_rate) / 12)
            st.caption(f"추정 월 실수령: **¥{_fi_monthly_net:,.0f}** (세율 약 {_fi_est_rate*100:.0f}% 추정)")

    with _fi_c3:
        _fi_industries = [
            "IT/소프트웨어", "금융/보험", "컨설팅", "제조업", "의료/제약",
            "교육", "미디어/광고", "부동산", "유통/소매", "공공/공무원", "기타"
        ]
        _saved_industry_idx = _fi_industries.index(_fi_saved.get("fi_industry", "IT/소프트웨어")) \
            if _fi_saved.get("fi_industry", "") in _fi_industries else 0
        _fi_industry = st.selectbox("업계/직종", _fi_industries, index=_saved_industry_idx, key="fi_industry")
        _fi_savings_now = st.number_input("현재 저축 잔액 (주식 외, ¥)",
                                           min_value=0,
                                           value=_fi_default("fi_savings_now", 0),
                                           step=100000, key="fi_savings_now")

    st.markdown("---")

    # ── 2. 월 지출 ────────────────────────────────────────────
    st.subheader("2️⃣ 월 지출")
    _fi_e1, _fi_e2, _fi_e3 = st.columns(3)
    with _fi_e1:
        _fi_living = st.number_input("생활비 (주거+식비+공과금+교통+반려견 등) ¥",
                                      min_value=0,
                                      value=_fi_default("fi_living", 150000),
                                      step=10000, key="fi_living")
    with _fi_e2:
        _fi_extra = st.number_input("기타/꾸밈비 (쇼핑+미용+취미+여행 등) ¥",
                                     min_value=0,
                                     value=_fi_default("fi_extra", 50000),
                                     step=10000, key="fi_extra")
    with _fi_e3:
        _fi_total_expense = _fi_living + _fi_extra
        st.metric("월 총 지출", f"¥{_fi_total_expense:,.0f}")
        _fi_annual_expense = _fi_total_expense * 12
        st.caption(f"연간: ¥{_fi_annual_expense:,.0f}")

    # ── 💾 입력 정보 저장 ─────────────────────────────────────
    if st.button("💾 입력 정보 저장", type="primary", key="fi_save_btn"):
        save_settings_bulk({
            "fi_birth": str(_fi_birth),
            "fi_target_age": str(_fi_target_age),
            "fi_salary_annual": str(_fi_salary_annual),
            "fi_monthly_net": str(_fi_monthly_net),
            "fi_industry": _fi_industry,
            "fi_savings_now": str(_fi_savings_now),
            "fi_living": str(_fi_living),
            "fi_extra": str(_fi_extra),
        })
        st.success("✅ 입력 정보가 저장되었습니다. 다음에 접속해도 유지됩니다.")

    st.markdown("---")

    # ── 3. 경제 가정치 ────────────────────────────────────────
    st.subheader("3️⃣ 경제 가정치")
    _fi_s1, _fi_s2, _fi_s3, _fi_s4 = st.columns(4)
    with _fi_s1:
        _fi_inflation = st.slider("물가 상승률 (%/년)", 0.0, 5.0, 2.5, 0.1, key="fi_inflation")
    with _fi_s2:
        _fi_return = st.slider("기대 주식 수익률 (%/년)", 0.0, 15.0, 7.0, 0.5, key="fi_return")
    with _fi_s3:
        _fi_savings_rate = st.slider("저축 이자율 (%/년)", 0.0, 3.0, 0.5, 0.1, key="fi_savings_rate")
    with _fi_s4:
        _fi_withdrawal = st.slider("안전 인출률 (%)", 2.0, 6.0, 4.0, 0.5, key="fi_withdrawal",
                                    help="은퇴 후 연간 자산의 몇 %를 인출할지 (4% 룰 기본)")

    st.markdown("---")

    # ── 4. 계산 & AI 분석 ─────────────────────────────────────
    if _fi_monthly_net > 0 and _fi_total_expense > 0:
        st.subheader("4️⃣ 경제적 자유 분석 결과")

        # 현재 포트폴리오 가치 (앱 데이터에서 자동)
        _fi_stocks = get_all_stocks()
        _fi_portfolio_krw = 0
        if _fi_stocks:
            try:
                _fi_agg = aggregate_stocks_by_ticker(_fi_stocks)
                _fi_summ = get_portfolio_summary(_fi_agg)
                _fi_portfolio_krw = _fi_summ.get("total_current_krw", 0)
            except Exception:
                pass
        _fi_portfolio_jpy = _fi_portfolio_krw / _jpy_r_fi if _jpy_r_fi > 0 else _fi_portfolio_krw

        # 확정 수익 (매도 + 배당)
        _fi_realized_krw = sum((r.get("realized_gain_krw") or 0) for r in get_sold_history(limit=10000))
        _fi_div_krw = (get_dividend_summary().get("total_krw") or 0)
        _fi_confirmed_jpy = (_fi_realized_krw + _fi_div_krw) / _jpy_r_fi if _jpy_r_fi > 0 else 0

        # 현재 총 자산
        _fi_total_assets = _fi_portfolio_jpy + _fi_savings_now + _fi_confirmed_jpy

        # 물가 반영 월 지출 (목표 나이 시점)
        _fi_future_monthly = _fi_total_expense * ((1 + _fi_inflation / 100) ** _fi_years_left)
        _fi_future_annual = _fi_future_monthly * 12

        # 경제적 자유 목표액 (4% 룰)
        _fi_target_amount = _fi_future_annual / (_fi_withdrawal / 100)

        # 진행률
        _fi_progress = min(100, (_fi_total_assets / _fi_target_amount * 100)) if _fi_target_amount > 0 else 0

        # 상단 카드
        _fic1, _fic2, _fic3, _fic4 = st.columns(4)
        with _fic1:
            st.metric("현재 총 자산", f"¥{_fi_total_assets:,.0f}",
                       help="주식 포트폴리오 + 저축 + 확정수익")
        with _fic2:
            st.metric("목표 자산", f"¥{_fi_target_amount:,.0f}",
                       help=f"물가 반영 후 연간 지출 ¥{_fi_future_annual:,.0f} ÷ {_fi_withdrawal}%")
        with _fic3:
            st.metric("진행률", f"{_fi_progress:.1f}%")
        with _fic4:
            _fi_investable = _fi_monthly_net - _fi_total_expense
            st.metric("월 투자 가능액", f"¥{max(0, _fi_investable):,.0f}")

        # 진행 바
        st.progress(min(1.0, _fi_progress / 100))

        st.markdown("---")

        # ── 시나리오별 달성 시점 계산 ──────────────────────────
        st.subheader("📊 시나리오별 달성 시점")

        def _calc_years_to_fi(current, target, monthly_invest, annual_return_pct):
            """단리+복리로 목표 도달까지 몇 년 걸리는지 계산"""
            r = annual_return_pct / 100
            assets = current
            for yr in range(1, 60):
                assets = assets * (1 + r) + monthly_invest * 12
                if assets >= target:
                    return yr
            return 60  # 60년 이상

        _fi_monthly_invest = max(0, _fi_investable)
        _scenarios = [
            ("🐢 보수적", 5.0),
            ("⚖️ 중립적", _fi_return),
            ("🚀 적극적", 10.0),
        ]

        _scenario_data = []
        _chart_data = {}
        for _sc_name, _sc_rate in _scenarios:
            _yrs = _calc_years_to_fi(_fi_total_assets, _fi_target_amount, _fi_monthly_invest, _sc_rate)
            _target_year = date.today().year + _yrs
            _scenario_data.append({
                "시나리오": _sc_name,
                "수익률": f"{_sc_rate:.1f}%",
                "달성 시기": f"{_target_year}년" if _yrs < 60 else "60년+",
                "남은 기간": f"{_yrs}년" if _yrs < 60 else "60년+",
                "달성 나이": f"{_fi_age + _yrs}세" if _yrs < 60 else "-",
            })

            # 차트 데이터
            _assets_by_year = []
            _a = _fi_total_assets
            for yr in range(0, min(_yrs + 5, 40)):
                _assets_by_year.append({"연도": date.today().year + yr, "자산": _a})
                _a = _a * (1 + _sc_rate / 100) + _fi_monthly_invest * 12
            _chart_data[_sc_name] = _assets_by_year

        st.dataframe(pd.DataFrame(_scenario_data), use_container_width=True, hide_index=True)

        # 자산 성장 차트
        fig_fi = go.Figure()
        _colors = {"🐢 보수적": "#89b4fa", "⚖️ 중립적": "#a6e3a1", "🚀 적극적": "#f9e2af"}
        for _sc_name, _data in _chart_data.items():
            if _data:
                _df_sc = pd.DataFrame(_data)
                fig_fi.add_trace(go.Scatter(
                    x=_df_sc["연도"], y=_df_sc["자산"],
                    mode="lines", name=_sc_name,
                    line=dict(color=_colors.get(_sc_name, "#cdd6f4"), width=2),
                ))
        fig_fi.add_hline(y=_fi_target_amount, line_dash="dash", line_color="#f38ba8",
                          annotation_text=f"목표: ¥{_fi_target_amount:,.0f}")
        fig_fi.update_layout(
            height=350, margin=dict(t=30, b=30),
            yaxis_title="자산 (¥)", xaxis_title="연도",
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_fi, use_container_width=True)

        st.markdown("---")

        # ── AI 종합 분석 (투자 추천 + 동년배 비교) ────────────
        st.subheader("5️⃣ AI 종합 분석 & 추천")
        if _api_key_fi:
            if st.button("🤖 AI 분석 시작", type="primary", key="fi_ai_btn"):
                _fi_prompt = f"""당신은 일본에 거주하는 외국인 여성을 위한 재무설계사입니다.

아래 정보를 바탕으로 한국어로 분석해주세요:

**개인 정보:**
- 나이: {_fi_age}세 여성, 기혼 (DINK, 자녀 없음), 반려견 1마리
- 업계: {_fi_industry}
- 연봉(세전): ¥{_fi_salary_annual:,.0f} / 월 실수령: ¥{_fi_monthly_net:,.0f}
- 목표: {_fi_target_age}세에 경제적 자유 ({_fi_years_left}년 후)

**자산 현황:**
- 주식 포트폴리오: ¥{_fi_portfolio_jpy:,.0f}
- 저축: ¥{_fi_savings_now:,.0f}
- 확정 수익 (매도+배당): ¥{_fi_confirmed_jpy:,.0f}
- 총 자산: ¥{_fi_total_assets:,.0f}

**지출:**
- 생활비: ¥{_fi_living:,.0f}/월
- 기타/꾸밈비: ¥{_fi_extra:,.0f}/월
- 총 지출: ¥{_fi_total_expense:,.0f}/월
- 월 투자 가능액: ¥{max(0, _fi_investable):,.0f}

**목표:**
- 경제적 자유 목표액: ¥{_fi_target_amount:,.0f} (물가 {_fi_inflation}%/년 반영, {_fi_withdrawal}% 룰)
- 진행률: {_fi_progress:.1f}%

아래 형식으로 답변:

## 📊 동년배 비교
- {_fi_age}세 일본 거주 여성 평균 연봉 (전체 + {_fi_industry} 업계)
- 당신의 소득 위치 (상위 몇 %)
- {_fi_industry} 업계 평균 연봉 상승률
- 동년배 평균 저축률 vs 당신의 저축률

## 💴 일본 세금 분석
- 현재 연봉 기준 예상 세금 내역 (소득세, 주민세, 사회보험료 각각)
- 절세 팁 (NISA 활용, iDeCo, 후르사토 납세 등)

## 💡 추천 월 자금 배분
- 수입 ¥{_fi_monthly_net:,.0f}에서 최적 배분 추천
- 주식 투자 추천 금액 (NISA 우선)
- 저축 추천 금액 (비상금 6개월분 기준)
- 여유자금

## 🎯 경제적 자유 달성 전략
- 현재 속도로 달성 가능한지 평가
- 가속 방안 (월 투자 증액 효과, 수익률 개선 등)
- "월 투자를 ¥X 늘리면 Y년 빨라짐" 구체 시뮬레이션

## 📈 배당 자립도
- 현재 연간 배당 수입 추정 vs 연간 지출
- 배당으로 생활비 커버까지 필요한 추가 투자

## ⚠️ 리스크 & 주의사항
- 환율 리스크 (엔화 기반 생활, 달러 투자)
- 인플레이션 시나리오
- 비상금 충분성

규칙: 전체 2000자 이내, 숫자를 최대한 구체적으로, **중요 수치는 굵은 글씨**
"""
                with st.spinner("🤖 AI가 분석 중입니다..."):
                    try:
                        import anthropic as _anth_fi
                        _client_fi = _anth_fi.Anthropic(api_key=_api_key_fi)
                        _msg_fi = _client_fi.messages.create(
                            model="claude-sonnet-4-6",
                            max_tokens=3000,
                            messages=[{"role": "user", "content": _fi_prompt}],
                        )
                        st.session_state.fi_ai_result = _msg_fi.content[0].text
                    except Exception as _e_fi:
                        st.error(f"AI 분석 오류: {_e_fi}")

            if st.session_state.fi_ai_result:
                st.markdown(st.session_state.fi_ai_result)

                st.markdown("---")
                # Google Sheets 저장 버튼
                if st.button("📊 이 계획을 Google Sheets에 저장", key="fi_save_sheets"):
                    try:
                        from modules.gsheets import is_available as gs_avail, _get_client, _ensure_worksheet
                        _gs_env = _load_env()
                        _gs_id = _gs_env.get("GOOGLE_SHEETS_ID", "")
                        _gs_creds = _gs_env.get("GOOGLE_SHEETS_CREDENTIALS", "")
                        if not _gs_id or not _gs_creds or not gs_avail():
                            st.warning("Google Sheets 설정이 필요합니다. 설정 페이지에서 먼저 연결하세요.")
                        else:
                            gc = _get_client(_gs_creds)
                            ss = gc.open_by_key(_gs_id)
                            ws = _ensure_worksheet(ss, "경제적자유계획")
                            _now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                            _plan_data = [
                                ["항목", "값"],
                                ["생성일시", _now_str],
                                ["생년월일", str(_fi_birth)],
                                ["현재 나이", f"{_fi_age}세"],
                                ["목표 나이", f"{_fi_target_age}세"],
                                ["업계", _fi_industry],
                                ["연봉(세전)", f"¥{_fi_salary_annual:,.0f}"],
                                ["월 실수령", f"¥{_fi_monthly_net:,.0f}"],
                                ["생활비/월", f"¥{_fi_living:,.0f}"],
                                ["기타비/월", f"¥{_fi_extra:,.0f}"],
                                ["총지출/월", f"¥{_fi_total_expense:,.0f}"],
                                ["현재 저축", f"¥{_fi_savings_now:,.0f}"],
                                ["주식 포트폴리오", f"¥{_fi_portfolio_jpy:,.0f}"],
                                ["확정수익(매도+배당)", f"¥{_fi_confirmed_jpy:,.0f}"],
                                ["현재 총 자산", f"¥{_fi_total_assets:,.0f}"],
                                ["목표 자산", f"¥{_fi_target_amount:,.0f}"],
                                ["진행률", f"{_fi_progress:.1f}%"],
                                ["월 투자 가능액", f"¥{max(0, _fi_investable):,.0f}"],
                                ["물가상승률", f"{_fi_inflation}%"],
                                ["기대수익률", f"{_fi_return}%"],
                                ["안전인출률", f"{_fi_withdrawal}%"],
                                ["", ""],
                                ["=== AI 분석 ===", ""],
                                ["분석 내용", st.session_state.fi_ai_result[:5000]],
                            ]
                            ws.clear()
                            ws.update(_plan_data, value_input_option="USER_ENTERED")
                            st.success(f"✅ Google Sheets '경제적자유계획' 시트에 저장 완료!")
                    except Exception as _e_gs:
                        st.error(f"저장 오류: {_e_gs}")

                # 텔레그램 전송 버튼
                if st.button("📱 이 계획을 텔레그램으로 전송", key="fi_tg_btn"):
                    _fi_tg_msg = (
                        f"🏖️ 경제적 자유 계획 ({datetime.now().strftime('%Y-%m-%d')})\n\n"
                        f"👤 {_fi_age}세 | {_fi_industry} | 목표 {_fi_target_age}세\n"
                        f"💰 현재 총 자산: ¥{_fi_total_assets:,.0f}\n"
                        f"🎯 목표 자산: ¥{_fi_target_amount:,.0f}\n"
                        f"📊 진행률: {_fi_progress:.1f}%\n\n"
                        f"{st.session_state.fi_ai_result[:3500]}"
                    )
                    _fi_tg_result = send_telegram_message(_fi_tg_msg)
                    if _fi_tg_result.get("success"):
                        st.success("✅ 텔레그램으로 전송 완료!")
                    else:
                        st.error(f"전송 실패: {_fi_tg_result.get('error')}")

                st.caption("⚠️ 이 분석은 참고용이며, 전문 재무설계사의 조언을 대체하지 않습니다.")

        else:
            st.warning("AI 분석을 위해 설정에서 Anthropic API 키를 입력하세요.")

    else:
        st.info("위의 기본 정보(월 실수령액)와 월 지출을 입력하면 분석이 시작됩니다.")


# ══════════════════════════════════════════════════════════════
# 페이지 3: 환율 분석
# ══════════════════════════════════════════════════════════════
elif page == "환율 분석":
    st.title("💱 환율 분석")

    # ── 현재 환율 (항상 최상단 표시) ──────────────────────────
    with st.spinner("현재 환율 로딩 중..."):
        usd_rate = get_exchange_rate("USD")
        jpy_rate = get_exchange_rate("JPY")

    usd_jpy_rate = usd_rate / jpy_rate if jpy_rate > 0 else 0.0

    _h1, _h2, _h3, _h4 = st.columns([1, 1, 1, 1])
    with _h1:
        st.metric("💴 JPY/KRW (1엔)", f"₩{jpy_rate:,.2f}")
    with _h2:
        st.metric("💵 USD/JPY (1달러)", f"¥{usd_jpy_rate:,.1f}")
    with _h3:
        st.metric("💵 USD/KRW (참고)", f"₩{usd_rate:,.1f}")
    with _h4:
        st.caption(f"🕐 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        st.caption("차트를 드래그하거나 하단 슬라이더로 기간을 조정하세요.")

    st.markdown("---")

    # ── 이벤트 & 색상 정의 ─────────────────────────────────────
    _FX_TYPE_LABELS = {
        "policy":       "통화정책",
        "crisis":       "위기·충격",
        "geopolitical": "지정학",
        "trade":        "무역",
        "economy":      "경제지표",
    }
    _FX_EVENT_COLORS = {
        "policy":       "#4a9eff",
        "crisis":       "#ff6b6b",
        "geopolitical": "#ffa94d",
        "trade":        "#a9e34b",
        "economy":      "#cc5de8",
    }
    _FX_EVENT_ICONS = {
        "policy":       "🏦",
        "crisis":       "⚠️",
        "geopolitical": "🌍",
        "trade":        "📦",
        "economy":      "📈",
    }

    _EXCHANGE_EVENTS = {
        "USD": [
            {
                "date": "2015-12-16", "type": "policy",
                "label": "미연준 첫 금리 인상",
                "desc": "2008년 금융위기 이후 7년 만에 처음으로 기준금리를 인상(0% → 0.25%). 달러 강세 사이클이 시작되며 신흥국 통화(원화) 약세 압력이 본격화됐습니다.",
            },
            {
                "date": "2016-06-23", "type": "geopolitical",
                "label": "브렉시트 국민투표",
                "desc": "영국이 EU 탈퇴를 결정하면서 글로벌 불확실성이 급증했습니다. 안전자산인 달러 수요가 늘어 달러 강세 / 원화 약세로 이어졌습니다.",
            },
            {
                "date": "2018-03-22", "type": "trade",
                "label": "미중 무역전쟁 시작",
                "desc": "트럼프 행정부가 중국산 제품 500억 달러에 관세를 부과하며 무역전쟁이 시작됐습니다. 글로벌 교역 불안으로 신흥국 통화가 동반 약세를 보였습니다.",
            },
            {
                "date": "2019-08-05", "type": "trade",
                "label": "위안화 7위안 돌파",
                "desc": "미중 무역갈등 격화로 위안화가 심리적 마지노선인 7위안을 돌파했습니다. 아시아 통화 동반 약세로 원달러도 상승했습니다.",
            },
            {
                "date": "2020-03-19", "type": "crisis",
                "label": "코로나19 팬데믹 충격",
                "desc": "WHO 팬데믹 선언 후 전 세계 시장이 패닉 매도에 빠졌습니다. 안전자산 달러 수요가 폭발하며 원달러가 1,280원을 돌파했습니다. 주식·채권·신흥국 통화가 동시에 폭락했습니다.",
            },
            {
                "date": "2020-03-23", "type": "policy",
                "label": "연준 무제한 양적완화",
                "desc": "미 연준이 무제한 자산매입을 선언하고 제로 금리를 유지했습니다. 달러 유동성을 대규모로 공급하면서 달러 강세가 진정되고 원화가 반등하기 시작했습니다.",
            },
            {
                "date": "2020-11-09", "type": "economy",
                "label": "화이자 백신 성공 발표",
                "desc": "화이자가 코로나19 백신 90% 효과를 발표했습니다. 경기 회복 기대로 위험자산 선호가 급회복하며 원화가 강세로 전환됐습니다.",
            },
            {
                "date": "2021-11-03", "type": "policy",
                "label": "연준 테이퍼링 시작",
                "desc": "미 연준이 월 150억 달러씩 자산매입을 축소(테이퍼링)하기 시작했습니다. 금리 인상 기대가 고조되면서 달러 강세 압력이 다시 증가했습니다.",
            },
            {
                "date": "2022-02-24", "type": "geopolitical",
                "label": "러시아 우크라이나 침공",
                "desc": "러시아가 우크라이나를 전면 침공하면서 에너지·식량 위기가 발생했습니다. 글로벌 인플레이션이 악화되며 연준의 공격적 금리 인상을 촉발했습니다.",
            },
            {
                "date": "2022-03-16", "type": "policy",
                "label": "연준 금리 인상 시작",
                "desc": "치솟는 인플레이션에 대응해 기준금리 인상을 시작했습니다(0% → 0.25%). 이후 역대 가장 빠른 속도로 금리를 올리며 원달러가 급등했습니다.",
            },
            {
                "date": "2022-09-22", "type": "crisis",
                "label": "원달러 1,430원 돌파",
                "desc": "미국 기준금리가 3.0~3.25%까지 공격적으로 인상되면서 원달러가 13년 만에 최고치인 1,430원을 돌파했습니다. 원화 가치가 역사적으로 급락한 시점입니다.",
            },
            {
                "date": "2023-03-10", "type": "crisis",
                "label": "SVB 파산 사태",
                "desc": "실리콘밸리은행(SVB)이 파산하면서 미국 지역은행 불안이 확산됐습니다. 연준의 금리 인상 속도 조절 기대가 커지며 달러가 약세로 전환됐습니다.",
            },
            {
                "date": "2023-07-26", "type": "policy",
                "label": "연준 금리 최고점 도달",
                "desc": "기준금리가 5.25~5.50%로 최고점에 도달했습니다. 추가 인상 중단을 시사하면서 달러 강세 사이클의 정점을 찍었습니다.",
            },
            {
                "date": "2024-09-18", "type": "policy",
                "label": "연준 금리 인하 시작",
                "desc": "4년 만에 처음으로 기준금리를 인하했습니다(-0.5%p). 완화 사이클 시작으로 달러 약세 기대가 형성됐으나, 한국 정치 불안으로 원화는 약세를 유지했습니다.",
            },
            {
                "date": "2024-12-03", "type": "geopolitical",
                "label": "한국 비상계엄 선포",
                "desc": "윤석열 대통령이 비상계엄을 선포(6시간 후 해제)했습니다. 정치적 불확실성으로 원화가 급락하고 달러가 급등하며 원달러가 1,400원 이상으로 뛰었습니다.",
            },
            {
                "date": "2025-01-20", "type": "trade",
                "label": "트럼프 2기 취임·관세전쟁",
                "desc": "트럼프 대통령이 취임 직후 전 세계에 고율 관세 부과를 선언했습니다. 무역전쟁 우려로 신흥국 통화(원화) 약세가 지속되고 있습니다.",
            },
        ],
        "JPY": [
            {
                "date": "2016-01-29", "type": "policy",
                "label": "BOJ 마이너스 금리 도입",
                "desc": "일본은행(BOJ)이 사상 처음으로 마이너스 금리(-0.1%)를 도입했습니다. 엔화 약세를 유도하는 정책으로 이후 수년간 엔화 약세 기조가 굳어졌습니다.",
            },
            {
                "date": "2018-03-22", "type": "trade",
                "label": "미중 무역전쟁",
                "desc": "미중 무역전쟁으로 글로벌 불안이 고조됐습니다. 전통적 안전자산인 엔화 수요가 증가하면서 엔화가 일시적으로 강세를 보였습니다.",
            },
            {
                "date": "2020-03-19", "type": "crisis",
                "label": "코로나19 충격",
                "desc": "글로벌 패닉 속 안전자산 엔화 수요가 급증했습니다. 엔화가 단기 강세를 보였으나, BOJ가 추가 완화에 나서면서 다시 약세로 전환됐습니다.",
            },
            {
                "date": "2022-03-16", "type": "policy",
                "label": "미일 금리 격차 급등",
                "desc": "미국이 금리 인상을 시작한 반면, 일본은 마이너스 금리와 양적완화를 고집했습니다. 미일 금리 격차가 급격히 확대되면서 엔화가 역사적 약세 국면에 진입했습니다.",
            },
            {
                "date": "2022-09-22", "type": "policy",
                "label": "일본 외환시장 개입",
                "desc": "엔화가 달러당 145엔을 돌파하자 일본 정부가 24년 만에 외환시장에 개입(달러 매도·엔 매수)했습니다. 일시적으로 엔화가 강세를 보였으나 효과는 단기에 그쳤습니다.",
            },
            {
                "date": "2022-10-21", "type": "crisis",
                "label": "엔화 32년 최저점",
                "desc": "달러당 151엔을 돌파하며 32년 만에 최저점을 기록했습니다. 엔원 환율도 900원대까지 하락했으며, 일본 경제의 체력 우려가 극에 달했습니다.",
            },
            {
                "date": "2023-12-19", "type": "policy",
                "label": "BOJ 정책 전환 시사",
                "desc": "우에다 BOJ 총재가 마이너스 금리 종료 가능성을 시사했습니다. 엔 캐리 트레이드 청산 우려가 커지며 엔화가 강세로 전환되기 시작했습니다.",
            },
            {
                "date": "2024-03-19", "type": "policy",
                "label": "BOJ 마이너스 금리 종료",
                "desc": "일본은행이 17년 만에 기준금리를 인상(-0.1% → 0%)했습니다. 엔 캐리 트레이드 청산이 본격화되면서 엔화가 강세로 전환됐습니다.",
            },
            {
                "date": "2024-07-31", "type": "policy",
                "label": "BOJ 0.25% 추가 인상",
                "desc": "기준금리를 0.25%로 추가 인상했습니다. 대규모 엔 캐리 트레이드가 급격히 청산되면서 엔화가 폭등하고 글로벌 주식시장이 동반 급락했습니다.",
            },
            {
                "date": "2025-01-24", "type": "policy",
                "label": "BOJ 0.5% 추가 인상",
                "desc": "17년 만에 최고 수준인 0.5%로 추가 인상했습니다. 일본의 금리 정상화 기조가 지속되면서 엔화 강세 흐름이 유지되고 있습니다.",
            },
        ],
        "USDJPY": [
            {
                "date": "2015-12-16", "type": "policy",
                "label": "미연준 첫 금리 인상",
                "desc": "연준이 7년 만에 금리를 인상(0% → 0.25%)했습니다. 미일 금리 격차가 확대되기 시작하며 달러 강세·엔 약세 압력이 커졌고, USD/JPY가 상승했습니다.",
            },
            {
                "date": "2016-01-29", "type": "policy",
                "label": "BOJ 마이너스 금리 도입",
                "desc": "일본은행이 마이너스 금리(-0.1%)를 도입했습니다. 엔화 약세 정책으로 USD/JPY가 일시 상승했으나, 이후 안전자산 엔 수요로 되돌림이 발생했습니다.",
            },
            {
                "date": "2020-03-19", "type": "crisis",
                "label": "코로나19 팬데믹 충격",
                "desc": "전 세계 패닉 매도 속 전통적 안전자산인 엔화 수요가 급증했습니다. USD/JPY가 101엔 수준까지 급락(엔화 강세)하며 달러-엔 환율이 크게 떨어졌습니다.",
            },
            {
                "date": "2022-03-16", "type": "policy",
                "label": "미일 금리 격차 최대화",
                "desc": "연준이 공격적 금리 인상을 시작한 반면 일본은 마이너스 금리를 유지했습니다. 사상 최대의 미일 금리 격차로 USD/JPY가 150엔을 돌파하며 32년 만에 최저 엔화를 기록했습니다.",
            },
            {
                "date": "2022-09-22", "type": "policy",
                "label": "일본 외환시장 개입",
                "desc": "달러당 145엔 돌파 후 일본 정부가 24년 만에 외환시장 개입(달러 매도·엔 매수)을 단행했습니다. USD/JPY가 일시 급락했으나 효과는 단기에 그쳤습니다.",
            },
            {
                "date": "2022-10-21", "type": "crisis",
                "label": "USD/JPY 32년 최고점",
                "desc": "USD/JPY가 달러당 151엔을 돌파하며 1990년 이후 최고치를 기록했습니다. 엔화 가치가 역사적으로 폭락한 시점으로, 미국 자산 보유자에게 엔 환산 평가액이 크게 떨어졌습니다.",
            },
            {
                "date": "2023-12-19", "type": "policy",
                "label": "BOJ 정책 전환 시사",
                "desc": "우에다 BOJ 총재가 마이너스 금리 종료 가능성을 시사했습니다. 미일 금리 격차 축소 기대로 엔 캐리 청산이 시작되며 USD/JPY가 하락(엔 강세) 반전했습니다.",
            },
            {
                "date": "2024-03-19", "type": "policy",
                "label": "BOJ 마이너스 금리 종료",
                "desc": "BOJ가 17년 만에 기준금리를 인상(-0.1% → 0%)했습니다. 엔 캐리 트레이드 청산이 본격화되며 USD/JPY가 하락하고 일본 주식의 엔화 환산 가치가 상승했습니다.",
            },
            {
                "date": "2024-07-31", "type": "policy",
                "label": "BOJ 0.25% 추가 인상·대규모 엔 캐리 청산",
                "desc": "BOJ가 0.25%로 추가 인상하자 대규모 엔 캐리 트레이드가 급격히 청산됐습니다. USD/JPY가 급락(엔화 폭등)하며 일본 주식시장도 동반 급락했습니다.",
            },
            {
                "date": "2024-09-18", "type": "policy",
                "label": "연준 금리 인하 시작",
                "desc": "연준이 4년 만에 기준금리를 인하(-0.5%p)했습니다. 미일 금리 격차 축소로 USD/JPY가 추가 하락하며 엔화 강세 흐름이 강화됐습니다.",
            },
            {
                "date": "2025-01-24", "type": "policy",
                "label": "BOJ 0.5% 추가 인상",
                "desc": "BOJ가 17년 만에 최고 수준인 0.5%로 추가 인상했습니다. 일본 금리 정상화 기조가 이어지면서 USD/JPY 하락(엔 강세) 흐름이 지속되고 있습니다.",
            },
        ],
    }

    # ── 통화 탭 ────────────────────────────────────────────────
    _tab_jpy, _tab_usdjpy = st.tabs(["🇯🇵 JPY/KRW (엔-원)", "💱 USD/JPY (달러-엔)"])

    _tab_configs = [
        (_tab_jpy,    "JPY",    jpy_rate,     "JPY/KRW",  1, "₩"),
        (_tab_usdjpy, "USDJPY", usd_jpy_rate, "USD/JPY",  1, "¥"),
    ]

    for _curr_tab, _currency, _current_rate, _rate_label, _rate_mult, _sym in _tab_configs:
        with _curr_tab:

            # 기간 선택
            _period_map = {"최근 1년": 1, "3년": 3, "5년": 5, "10년": 10}
            _sel_label = st.radio(
                "조회 기간",
                list(_period_map.keys()),
                horizontal=True,
                key=f"fx_period_{_currency}",
                label_visibility="collapsed",
            )
            _sel_years = _period_map[_sel_label]
            _period_display = "1년" if _sel_label == "최근 1년" else _sel_label

            # 데이터 로드
            with st.spinner(f"{_sel_label} 환율 데이터 로딩 중..."):
                if _currency == "USDJPY":
                    _df = _fx_usdjpy_history_cached(_sel_years)
                else:
                    _df = _fx_long_history_cached(_currency, _sel_years)

            if _df.empty:
                st.warning("환율 데이터를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.")
                continue

            # ── 기간 통계 메트릭 ─────────────────────────────
            _start_rate   = float(_df["rate"].iloc[0])
            _high_rate    = float(_df["rate"].max()) * _rate_mult
            _low_rate     = float(_df["rate"].min()) * _rate_mult
            _disp_current = _current_rate * _rate_mult
            _disp_start   = _start_rate   * _rate_mult
            _chg_pct      = ((_current_rate - _start_rate) / _start_rate * 100) if _start_rate > 0 else 0.0

            _mc1, _mc2, _mc3 = st.columns(3)
            with _mc1:
                st.metric(
                    f"현재 {_rate_label}",
                    f"{_sym}{_disp_current:,.1f}",
                    delta=f"{_chg_pct:+.1f}%  ({_period_display} 전 대비)",
                    delta_color="off",
                )
            with _mc2:
                st.metric(f"{_period_display} 전 환율", f"{_sym}{_disp_start:,.1f}")
            with _mc3:
                st.metric(f"기간 최고 / 최저", f"{_sym}{_high_rate:,.0f}  /  {_sym}{_low_rate:,.0f}")

            # ── 이벤트 필터링 ─────────────────────────────────
            _all_events = _EXCHANGE_EVENTS.get(_currency, [])
            _t_start    = _df.index.min()
            _t_end      = _df.index.max()
            _events = [
                e for e in _all_events
                if _t_start <= pd.Timestamp(e["date"]) <= _t_end
            ]

            # ── Plotly 차트 ───────────────────────────────────
            _plot_df = _df.copy()
            _plot_df["rate_disp"] = _plot_df["rate"] * _rate_mult

            _fig = go.Figure()

            # 환율 라인
            _fig.add_trace(go.Scatter(
                x=_plot_df.index,
                y=_plot_df["rate_disp"],
                mode="lines",
                name=_rate_label,
                line=dict(color="#4a9eff", width=2),
                hovertemplate="%{x|%Y-%m-%d}<br>" + _sym + "%{y:,.1f}<extra></extra>",
            ))

            # 현재 환율 수평 기준선
            _fig.add_hline(
                y=_disp_current,
                line_dash="dot",
                line_color="#f38ba8",
                annotation_text=f"현재 {_sym}{_disp_current:,.0f}",
                annotation_position="bottom right",
                annotation_font_color="#f38ba8",
                opacity=0.8,
            )

            # 이벤트: 수직선 + 별 마커
            _ev_x, _ev_y, _ev_hover, _ev_colors = [], [], [], []
            for _ev in _events:
                _ev_ts  = pd.Timestamp(_ev["date"])
                _color  = _FX_EVENT_COLORS.get(_ev["type"], "#888888")
                _icon   = _FX_EVENT_ICONS.get(_ev["type"], "📌")
                _nearby = _plot_df[_plot_df.index >= _ev_ts]
                if _nearby.empty:
                    continue
                _y_val = float(_nearby["rate_disp"].iloc[0])
                _ev_x.append(_ev_ts)
                _ev_y.append(_y_val)
                _ev_colors.append(_color)
                _ev_hover.append(
                    f"<b>{_icon} {_ev['label']}</b><br>"
                    f"📅 {_ev['date']}  |  {_sym}{_y_val:,.0f}<br><br>"
                    f"{_ev['desc']}"
                )
                _fig.add_vline(
                    x=_ev_ts.to_pydatetime(),
                    line_width=1.2,
                    line_dash="dot",
                    line_color=_color,
                    opacity=0.55,
                )

            if _ev_x:
                _fig.add_trace(go.Scatter(
                    x=_ev_x,
                    y=_ev_y,
                    mode="markers",
                    name="주요 이벤트",
                    marker=dict(
                        size=12,
                        color=_ev_colors,
                        symbol="star",
                        line=dict(color="#13131f", width=1),
                    ),
                    hovertext=_ev_hover,
                    hoverinfo="text",
                ))

            _fig.update_layout(
                height=500,
                margin=dict(t=20, b=10, l=70, r=20),
                xaxis=dict(
                    title="",
                    rangeslider=dict(visible=True, thickness=0.05),
                    type="date",
                ),
                yaxis=dict(title="엔화 (¥)" if _currency == "USDJPY" else "원화 (₩)", tickformat=",.0f"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                plot_bgcolor="#13131f",
                paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"),
                hovermode="x unified",
                hoverlabel=dict(bgcolor="#2a2a3e", font_size=12, namelength=-1),
            )

            st.plotly_chart(_fig, use_container_width=True)

            # ── 이벤트 타임라인 카드 ──────────────────────────
            if _events:
                st.subheader(f"📅 주요 이벤트 타임라인 ({len(_events)}건)")

                # 이벤트 유형 범례
                _legend_parts = []
                for _k, _lbl in _FX_TYPE_LABELS.items():
                    _c  = _FX_EVENT_COLORS[_k]
                    _ic = _FX_EVENT_ICONS[_k]
                    _legend_parts.append(
                        f"<span style='background:{_c}22;color:{_c};"
                        f"padding:3px 10px;border-radius:12px;margin:2px;"
                        f"display:inline-block;font-size:0.82em;'>{_ic} {_lbl}</span>"
                    )
                st.markdown(
                    "<div style='margin-bottom:14px;'>" + " ".join(_legend_parts) + "</div>",
                    unsafe_allow_html=True,
                )

                # 카드 (역순 — 최신 먼저)
                for _ev in reversed(_events):
                    _ev_ts    = pd.Timestamp(_ev["date"])
                    _ev_date  = _ev["date"]
                    _ev_label = _ev["label"]
                    _ev_desc  = _ev["desc"]
                    _ev_type  = _ev["type"]
                    _color    = _FX_EVENT_COLORS.get(_ev_type, "#888888")
                    _icon     = _FX_EVENT_ICONS.get(_ev_type, "📌")
                    _tlabel   = _FX_TYPE_LABELS.get(_ev_type, _ev_type)

                    _nearby2 = _plot_df[_plot_df.index >= _ev_ts]
                    if not _nearby2.empty:
                        _rate_at = float(_nearby2["rate_disp"].iloc[0])
                        _vs_pct  = ((_disp_current - _rate_at) / _rate_at * 100) if _rate_at > 0 else 0.0
                        _vs_arrow = "▲" if _vs_pct > 0 else "▼"
                        _rate_info = (
                            f"<br><span style='color:#cdd6f4;font-weight:bold;'>"
                            f"{_sym}{_rate_at:,.0f}</span>"
                            f" <span style='color:#a6adc8;font-size:0.8em;'>"
                            f"(현재 대비 {_vs_arrow}{abs(_vs_pct):.1f}%)</span>"
                        )
                    else:
                        _rate_info = ""

                    _card = (
                        f"<div style='border-left:4px solid {_color};"
                        f"background:#1e1e2e;padding:12px 16px;"
                        f"margin-bottom:8px;border-radius:0 8px 8px 0;'>"
                        f"<div style='display:flex;justify-content:space-between;"
                        f"align-items:flex-start;flex-wrap:wrap;gap:4px;'>"
                        f"<div>"
                        f"<span style='font-size:1.05em;font-weight:bold;color:{_color};'>"
                        f"{_icon} {_ev_label}</span>"
                        f"<span style='background:{_color}22;color:{_color};"
                        f"border-radius:4px;padding:2px 8px;"
                        f"font-size:0.75em;margin-left:8px;'>{_tlabel}</span>"
                        f"</div>"
                        f"<div style='text-align:right;'>"
                        f"<span style='color:#a6adc8;font-size:0.85em;'>{_ev_date}</span>"
                        f"{_rate_info}"
                        f"</div>"
                        f"</div>"
                        f"<div style='color:#cdd6f4;margin-top:8px;"
                        f"font-size:0.9em;line-height:1.65;'>{_ev_desc}</div>"
                        f"</div>"
                    )
                    st.markdown(_card, unsafe_allow_html=True)

    st.markdown("---")

    # ── 환율 영향 시뮬레이터 (JPY 기준) ──────────────────────────
    st.subheader("🧮 환율 변동 영향 계산기 (엔화 기준)")
    stocks = get_all_stocks()
    all_stocks_sim = stocks  # JPY 포함 전 종목

    if not all_stocks_sim:
        st.info("보유 종목이 없습니다.")
    else:
        st.caption("엔화 기준 평가액에 대한 환율 변동 영향을 계산합니다. JPY 종목은 환율 영향 없음.")
        _has_usd = any(s["purchase_currency"] == "USD" for s in all_stocks_sim)
        _has_krw = any(s["purchase_currency"] == "KRW" for s in all_stocks_sim)

        sim_usdjpy = st.slider(
            "시뮬레이션 USD/JPY (1달러 = ?엔)",
            100, 200,
            int(usd_jpy_rate) if usd_jpy_rate > 0 else 148,
            1,
            disabled=not _has_usd,
        ) if _has_usd else usd_jpy_rate
        sim_jpykrw = st.slider(
            "시뮬레이션 JPY/KRW (1엔 = ?원, KRW 종목 환산용)",
            5.0, 15.0,
            float(jpy_rate),
            0.1,
            disabled=not _has_krw,
        ) if _has_krw else jpy_rate

        from modules.stock_data import get_current_price as _gcp
        sim_rows = []
        for s in all_stocks_sim:
            curr      = s["purchase_currency"]
            curr_price = _gcp(s["ticker"])
            if curr_price <= 0:
                continue

            if curr == "JPY":
                # JPY 종목: 환율 영향 없음, 현재가 그대로 엔화
                curr_val_jpy  = curr_price * s["quantity"]
                sim_val_jpy   = curr_val_jpy
                sim_rate_disp = "-"
            elif curr == "USD":
                # USD 종목: USD/JPY 환율 적용
                curr_val_jpy = curr_price * usd_jpy_rate * s["quantity"]
                sim_val_jpy  = curr_price * sim_usdjpy * s["quantity"]
                sim_rate_disp = f"¥{sim_usdjpy:,.0f}/USD"
            elif curr == "KRW":
                # KRW 종목: JPY/KRW 로 원화→엔화 환산
                curr_val_jpy = curr_price * s["quantity"] / jpy_rate if jpy_rate > 0 else 0
                sim_val_jpy  = curr_price * s["quantity"] / sim_jpykrw if sim_jpykrw > 0 else 0
                sim_rate_disp = f"₩{sim_jpykrw:,.1f}/JPY"
            else:
                continue

            diff_jpy = sim_val_jpy - curr_val_jpy
            sim_rows.append({
                "종목": s["name"],
                "통화": curr,
                "시뮬 환율": sim_rate_disp,
                "현재 평가액(¥)": f"¥{curr_val_jpy:,.0f}",
                "시뮬 평가액(¥)": f"¥{sim_val_jpy:,.0f}",
                "환율 영향(¥)": f"{'+'if diff_jpy>=0 else ''}¥{diff_jpy:,.0f}",
            })
        if sim_rows:
            st.dataframe(pd.DataFrame(sim_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════
# 페이지 4: 일일 리포트
# ══════════════════════════════════════════════════════════════
elif page == "일일 리포트":
    st.title("📅 일일 리포트")

    stocks = get_all_stocks()
    aggregated = aggregate_stocks_by_ticker(stocks) if stocks else []
    unique_tickers = [a["ticker"] for a in aggregated]

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("📊 지금 리포트 생성", type="primary"):
            if aggregated:
                with st.spinner("데이터 수집 및 AI 분석 중..."):
                    summary = get_portfolio_summary(aggregated)
                    report = run_daily_analysis(aggregated, summary["items"])
                st.success("리포트 생성 완료!")
                st.rerun()
            else:
                st.warning("보유 종목을 먼저 추가하세요.")

    st.markdown("---")

    # ── 글로벌 + 한국 경제 뉴스 (한국어 번역) ───────────────────
    st.subheader("🌐 글로벌 & 한국 경제 뉴스")

    @st.cache_data(ttl=1800, show_spinner=False)
    def _market_news_cached():
        return get_market_news(max_per_source=4)

    with st.spinner("경제 뉴스 불러오는 중..."):
        market_news = _market_news_cached()

    if market_news:
        has_api_key_news = bool(os.getenv("ANTHROPIC_API_KEY", ""))
        # 한국어 번역 (한국 뉴스 소스는 이미 한국어이므로 제외)
        KOREAN_SOURCES = {"한국경제", "매일경제", "연합뉴스"}
        foreign_news = [n for n in market_news if n["source"] not in KOREAN_SOURCES]
        korean_news  = [n for n in market_news if n["source"] in KOREAN_SOURCES]

        if foreign_news and has_api_key_news:
            with st.spinner("해외 뉴스 한국어 번역 중..."):
                trans_raw = _translate_news_cached(
                    _json.dumps([{"title": n["title"], "summary": n.get("summary","")} for n in foreign_news],
                                ensure_ascii=False)
                )
            trans_list = _json.loads(trans_raw)
            for i, n in enumerate(foreign_news):
                n["title_ko"]   = trans_list[i].get("title_ko", "")
                n["summary_ko"] = trans_list[i].get("summary_ko", "")

        all_news_display = foreign_news + korean_news
        source_groups = {}
        for n in all_news_display:
            source_groups.setdefault(n["source"], []).append(n)

        NCOLS = 3
        source_list = list(source_groups.items())
        for row_start in range(0, len(source_list), NCOLS):
            cols = st.columns(NCOLS)
            for col_i, (src, items) in enumerate(source_list[row_start:row_start+NCOLS]):
                with cols[col_i]:
                    is_kr = src in KOREAN_SOURCES
                    st.markdown(f"**{src}** {'🇰🇷' if is_kr else '🌐'}")
                    for n in items:
                        url   = n.get("url","")
                        title = n.get("title_ko","") or n.get("title","")
                        summ  = (n.get("summary_ko","") or n.get("summary",""))[:80]
                        if url:
                            st.markdown(f"- [{title}]({url})")
                        else:
                            st.markdown(f"- {title}")
                        if summ:
                            st.caption(f"💡 {summ}")
    else:
        st.caption("뉴스를 불러올 수 없습니다.")

    st.markdown("---")

    # ── 뉴스 & 키워드 섹션 ────────────────────────────────────
    if unique_tickers:
        st.subheader("📰 티커별 주요 뉴스 (최신 3건)")

        with st.spinner("뉴스 불러오는 중..."):
            news_by_ticker = {}
            for ticker in unique_tickers:
                news_by_ticker[ticker] = get_stock_news(ticker, max_items=3)

        # ── 전체 뉴스 일괄 번역 ───────────────────────────────
        # flat list로 만들어 한 번의 API 호출로 처리
        flat_news = []
        flat_index = {}   # ticker → (start_idx, count)
        for ticker in unique_tickers:
            nl = news_by_ticker.get(ticker, [])
            flat_index[ticker] = (len(flat_news), len(nl))
            flat_news.extend({"title": n.get("title",""), "summary": n.get("summary","")} for n in nl)

        has_api_key = bool(os.getenv("ANTHROPIC_API_KEY", ""))
        if flat_news and has_api_key:
            with st.spinner("뉴스 한국어 번역 중..."):
                translated_json = _translate_news_cached(_json.dumps(flat_news, ensure_ascii=False))
            translated_flat = _json.loads(translated_json)
        else:
            translated_flat = flat_news

        # 번역 결과를 news_by_ticker 원본에도 반영 (키워드 검색용)
        for ticker in unique_tickers:
            _start_i, _count_i = flat_index.get(ticker, (0, 0))
            _orig_nl = news_by_ticker.get(ticker, [])
            _trans_nl = translated_flat[_start_i: _start_i + _count_i]
            for _oi, _ti in zip(_orig_nl, _trans_nl):
                _oi["title_ko"]   = _ti.get("title_ko", "")
                _oi["summary_ko"] = _ti.get("summary_ko", "")

        # ── 번역 누락된 영어 제목 재번역 ────────────────────────
        def _is_untranslated(text: str) -> bool:
            if not text:
                return False
            ascii_chars = sum(1 for c in text if ord(c) < 128)
            return ascii_chars / max(len(text), 1) > 0.7

        if has_api_key:
            _retry_items = []
            _retry_map = []  # (ticker_idx_in_flat, orig_item)
            for idx, (orig, trans) in enumerate(zip(flat_news, translated_flat)):
                _title_ko = trans.get("title_ko", "")
                _orig_title = orig.get("title", "")
                if (not _title_ko and _is_untranslated(_orig_title)) or \
                   (_title_ko and _is_untranslated(_title_ko)):
                    _retry_items.append({"title": _orig_title, "summary": orig.get("summary", "")})
                    _retry_map.append(idx)

            if _retry_items:
                try:
                    _retrans = translate_news_batch(_retry_items)
                    for _ri, _flat_idx in enumerate(_retry_map):
                        _rt = _retrans[_ri]
                        if _rt.get("title_ko"):
                            translated_flat[_flat_idx]["title_ko"] = _rt["title_ko"]
                        if _rt.get("summary_ko"):
                            translated_flat[_flat_idx]["summary_ko"] = _rt["summary_ko"]
                    # news_by_ticker에도 재반영
                    for ticker in unique_tickers:
                        _start_i, _count_i = flat_index.get(ticker, (0, 0))
                        _orig_nl = news_by_ticker.get(ticker, [])
                        _trans_nl = translated_flat[_start_i: _start_i + _count_i]
                        for _oi, _ti in zip(_orig_nl, _trans_nl):
                            if _ti.get("title_ko"):
                                _oi["title_ko"] = _ti["title_ko"]
                            if _ti.get("summary_ko"):
                                _oi["summary_ko"] = _ti["summary_ko"]
                except Exception:
                    pass

        # ── 티커별 뉴스 표시 ─────────────────────────────────
        for agg in aggregated:
            ticker = agg["ticker"]
            name = agg["name"]
            orig_news = news_by_ticker.get(ticker, [])
            start, count = flat_index.get(ticker, (0, 0))
            trans_news = translated_flat[start: start + count]

            with st.expander(f"**{name}** ({ticker})", expanded=True):
                if not orig_news:
                    st.caption("뉴스를 불러올 수 없습니다.")
                    continue

                for i, (orig, trans) in enumerate(zip(orig_news, trans_news), 1):
                    title = trans.get("title_ko","") or orig.get("title","")
                    summ  = (trans.get("summary_ko","") or orig.get("summary",""))[:100]
                    url   = orig.get("url","")
                    pub   = orig.get("published","")[:10]

                    if title:
                        _ncol1, _ncol2 = st.columns([5, 1])
                        with _ncol1:
                            if url:
                                st.markdown(f"**{i}.** [{title}]({url})")
                            else:
                                st.markdown(f"**{i}.** {title}")
                            if summ:
                                st.caption(f"💡 {summ}")
                            if pub:
                                st.caption(f"🕐 {pub}")
                        with _ncol2:
                            if url:
                                _art_key = f"art_{ticker}_{i}"
                                if st.button("📖 번역", key=_art_key):
                                    st.session_state[f"show_{_art_key}"] = True
                        # 번역된 기사 본문 표시
                        if url and st.session_state.get(f"show_art_{ticker}_{i}"):
                            with st.spinner("기사 본문 번역 중..."):
                                _article_ko = _fetch_and_translate_article(url)
                            st.markdown(_article_ko)
                            st.markdown("---")

        st.markdown("---")

        # ── 통합 키워드 클릭 → 관련 뉴스 즉시 표시 ──────────────
        st.subheader("🔑 키워드 클릭 → 관련 뉴스")
        st.caption("🔴 여러 소스 공통 등장 (중요)  🟠 다수 등장  🟡 2개 소스  이모지 없음 = 1개 소스")

        # 티커 뉴스 + 글로벌 뉴스 통합 키워드 (번역 제목 포함)
        unified_kw = build_unified_keywords(news_by_ticker, all_news_display if market_news else [])
        pill_labels = [label for _, _, label in unified_kw]
        label_to_word = {label: word for word, _, label in unified_kw}

        if pill_labels:
            sel_label = st.pills(
                "키워드 선택",
                pill_labels,
                selection_mode="single",
                default=None,
                label_visibility="collapsed",
                key="kw_pills",
            )

            if sel_label:
                sel_kw = label_to_word.get(sel_label, sel_label.split()[-1])
                st.markdown(f"### 🔍 '{sel_kw}' 관련 뉴스")

                # 문맥 기반 검색: 키워드의 각 단어 중 하나라도 매칭되면 표시
                def _kw_match(news_item, keyword):
                    """키워드의 각 단어를 개별 검색 (OR 방식), 1글자 한국어도 허용"""
                    _combined = (
                        (news_item.get("title_ko","") or "") + " " +
                        (news_item.get("title","") or "") + " " +
                        (news_item.get("summary_ko","") or "") + " " +
                        (news_item.get("summary","") or "")
                    ).lower()
                    # 한국어 1글자도 허용, 영어는 2글자 이상
                    _words = []
                    for w in keyword.lower().split():
                        w = w.strip()
                        if not w:
                            continue
                        _has_ko = any('\uac00' <= c <= '\ud7a3' for c in w)
                        if _has_ko or len(w) >= 2:
                            _words.append(w)
                    return any(w in _combined for w in _words) if _words else False

                _all_matched = []  # (source_label, news_item) 쌍
                for ticker, nl in news_by_ticker.items():
                    matching = [n for n in nl if _kw_match(n, sel_kw)]
                    if matching:
                        agg_item = next((a for a in aggregated if a["ticker"] == ticker), {})
                        _src = f"{agg_item.get('name', ticker)} ({ticker})"
                        for n in matching:
                            _all_matched.append((_src, n))

                if market_news:
                    mkt_matching = [n for n in all_news_display if _kw_match(n, sel_kw)]
                    for n in mkt_matching[:5]:
                        _all_matched.append(("글로벌/한국 뉴스", n))

                # 매칭 없으면 전체 뉴스에서 Claude로 관련 뉴스 검색
                if not _all_matched:
                    _all_news_pool = []
                    for ticker, nl in news_by_ticker.items():
                        agg_item = next((a for a in aggregated if a["ticker"] == ticker), {})
                        _src = f"{agg_item.get('name', ticker)} ({ticker})"
                        for n in nl:
                            _all_news_pool.append((_src, n))
                    if market_news:
                        for n in all_news_display[:10]:
                            _all_news_pool.append(("글로벌/한국 뉴스", n))
                    # 키워드 매칭 실패 시 전체 뉴스를 대상으로 표시
                    _all_matched = _all_news_pool

                if _all_matched:
                    # ── Claude 종합 요약 ──
                    _summary_lines = []
                    for _src, n in _all_matched:
                        _t = n.get("title_ko","") or n.get("title","")
                        _s = n.get("summary_ko","") or n.get("summary","")
                        if _t:
                            _summary_lines.append(f"[{_src}] {_t}: {_s[:150]}")

                    _has_api = bool(os.getenv("ANTHROPIC_API_KEY", ""))
                    if _summary_lines and _has_api:
                        @st.cache_data(ttl=3600, show_spinner=False)
                        def _summarize_keyword_news(_keyword: str, _news_text: str) -> str:
                            import anthropic as _anth_sum
                            _client = _anth_sum.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
                            _msg = _client.messages.create(
                                model="claude-haiku-4-5-20251001",
                                max_tokens=1024,
                                messages=[{"role": "user", "content": (
                                    f"아래는 '{_keyword}' 키워드와 관련된 주식/경제 뉴스 목록입니다.\n\n"
                                    f"이 뉴스들을 종합 분석하여 한국어로 요약해주세요.\n\n"
                                    f"규칙:\n"
                                    f"- 핵심 내용을 3~5개 bullet point로 정리\n"
                                    f"- 투자자 관점에서 의미 있는 시사점 포함\n"
                                    f"- 회사명/티커/금융 용어는 영어 유지\n"
                                    f"- 마지막에 '💡 투자 시사점' 한 줄 추가\n\n"
                                    f"뉴스 목록:\n" + "\n".join(_summary_lines[:20])
                                )}],
                            )
                            return _msg.content[0].text.strip()

                        with st.spinner(f"'{sel_kw}' 관련 뉴스 종합 분석 중..."):
                            _news_text = "\n".join(_summary_lines[:20])
                            _kw_summary = _summarize_keyword_news(sel_kw, _news_text)
                        st.info(_kw_summary)

                    # ── 개별 뉴스 목록 ──
                    st.markdown("**📰 관련 뉴스 목록**")
                    _prev_src = ""
                    for _src, n in _all_matched:
                        if _src != _prev_src:
                            st.markdown(f"**{'📈' if '글로벌' not in _src else '🌐'} {_src}**")
                            _prev_src = _src
                        url   = n.get("url","")
                        title = n.get("title_ko","") or n.get("title","")
                        summ  = (n.get("summary_ko","") or n.get("summary",""))[:90]
                        if title:
                            st.markdown(f"  ▸ [{title}]({url})" if url else f"  ▸ {title}")
                            if summ:
                                st.caption(f"    💡 {summ}")
                else:
                    st.caption("관련 뉴스를 찾을 수 없습니다.")

        st.markdown("---")

    # ── AI 분석 리포트 ────────────────────────────────────────
    reports = get_reports(limit=30)
    if not reports:
        st.info("아직 생성된 리포트가 없습니다. '지금 리포트 생성' 버튼을 눌러보세요.")
    else:
        st.subheader("📋 AI 분석 리포트 목록")
        for r in reports:
            with st.expander(f"📋 {r['date']} 리포트"):
                st.markdown(r["report_text"])


# ══════════════════════════════════════════════════════════════
# 페이지 5: 설정
# ══════════════════════════════════════════════════════════════
elif page == "설정":
    st.title("⚙️ 설정")

    env = _load_env()

    # ── API 키 설정 ────────────────────────────────────────────
    st.subheader("API 키 설정")

    with st.form("api_keys_form"):
        anthropic_key = st.text_input(
            "Anthropic API 키 (Claude AI 분석용)",
            value=env.get("ANTHROPIC_API_KEY", ""),
            type="password",
            help="https://console.anthropic.com 에서 발급",
        )
        notif_time = st.text_input(
            "뉴스 알림 시간 (쉼표로 여러 개 가능)",
            value=env.get("NOTIFICATION_TIME", "08:20, 12:20, 21:00"),
            help="24시간 형식, 쉼표 구분. 이 시간에 관심 키워드 뉴스가 전송됩니다.",
        )
        report_time = st.text_input(
            "일일 리포트 시간 (하루 1회)",
            value=env.get("DAILY_REPORT_TIME", "19:00"),
            help="24시간 형식. 이 시간에 포트폴리오 분석 리포트가 전송됩니다.",
        )

        if st.form_submit_button("저장", type="primary"):
            if anthropic_key:
                _save_env_value("ANTHROPIC_API_KEY", anthropic_key)
            _save_env_value("NOTIFICATION_TIME", notif_time)
            _save_env_value("DAILY_REPORT_TIME", report_time)
            st.success("✅ 설정이 저장되었습니다.")

    st.markdown("---")

    # ══════════════════════════════════════════════════════════
    # 오프라인 스냅샷 (비행기 모드용)
    # ══════════════════════════════════════════════════════════
    st.subheader("📱 오프라인 스냅샷 (비행기 모드용)")
    st.caption(
        "현재 포트폴리오 상태를 **단일 HTML 파일**로 저장합니다. "
        "비행기·지하 등 인터넷이 없을 때 핸드폰 브라우저로 열면 "
        "요약 카드·종목 테이블·주가 차트가 그대로 보입니다.  \n"
        "사용법: 아래 버튼으로 다운로드 → 핸드폰에 AirDrop/이메일/Drive로 전송 → 브라우저로 열기"
    )

    if "offline_snapshot_html" not in st.session_state:
        st.session_state.offline_snapshot_html = None

    _col_sg, _col_sd = st.columns([1, 1])
    with _col_sg:
        if st.button("🔄 스냅샷 새로 생성", type="primary", use_container_width=True):
            with st.spinner("스냅샷 생성 중 (주가·차트 로딩)..."):
                _snap_html = generate_offline_html_snapshot()
            if _snap_html:
                st.session_state.offline_snapshot_html = _snap_html
                st.success(f"✅ 스냅샷 생성 완료! ({len(_snap_html)//1024:,}KB)")
            else:
                st.warning("⚠️ 포트폴리오가 비어 있습니다. 먼저 종목을 추가하세요.")
    with _col_sd:
        if st.session_state.offline_snapshot_html:
            st.download_button(
                "⬇️ HTML 파일 다운로드",
                data=st.session_state.offline_snapshot_html,
                file_name=f"portfolio_snapshot_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html",
                type="primary",
                use_container_width=True,
            )
        else:
            st.button("⬇️ HTML 파일 다운로드", disabled=True, use_container_width=True,
                      help="먼저 '스냅샷 새로 생성' 버튼을 누르세요.")

    st.markdown(
        "#### 📖 사용 순서 (비행기 모드용)\n"
        "1. **[비행 전]** 위의 `🔄 스냅샷 새로 생성` → `⬇️ HTML 파일 다운로드` 클릭\n"
        "2. **[핸드폰 전송]** 다운로드한 `portfolio_snapshot_YYYYMMDD_HHMM.html`을 핸드폰으로:\n"
        "   - iPhone: AirDrop / 이메일 / Google Drive / 카카오톡 '나에게 보내기'\n"
        "   - Android: Google Drive / 이메일 / Telegram '나에게 보내기' / USB\n"
        "3. **[핸드폰에서 열기]** 전송된 파일을 탭 → 브라우저(Safari/Chrome)가 자동으로 열림\n"
        "4. **[앱 아이콘처럼 쓰기]** (선택) 브라우저에서:\n"
        "   - **iPhone Safari**: 하단 공유 버튼 📤 → `홈 화면에 추가` → 아이콘 생김\n"
        "   - **Android Chrome**: 우측 상단 ⋮ 메뉴 → `홈 화면에 추가`\n"
        "5. **[비행기 안]** 홈 화면 아이콘 탭 → 오프라인에서 전체 포트폴리오·차트 확인 가능\n\n"
        "⚠️ **정적 스냅샷**입니다 — 생성 당시의 가격이 고정됩니다. 최신 가격으로 보려면 비행 전에 반드시 다시 생성하세요.  \n"
        "📈 차트는 JavaScript가 파일에 내장되어 있어 **오프라인에서도 호버·줌이 작동**합니다."
    )

    st.markdown("---")

    # ── 텔레그램 봇 설정 ──────────────────────────────────────
    st.subheader("텔레그램 봇 설정")

    _tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    _tg_chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if _tg_token and _tg_chat_id:
        st.success(f"✅ 텔레그램 연동 완료 (Chat ID: {_tg_chat_id})")
        if st.button("설정 초기화"):
            _save_env_value("TELEGRAM_BOT_TOKEN", "")
            _save_env_value("TELEGRAM_CHAT_ID", "")
            st.rerun()
    else:
        st.markdown(
            "#### 텔레그램 봇 만들기 (3분)\n\n"
            "**Step 1.** 텔레그램에서 **@BotFather** 검색 → 대화 시작\n\n"
            "**Step 2.** `/newbot` 입력 → 봇 이름 입력 (예: `내 주식알림`) → 유저네임 입력 (예: `my_stock_alert_bot`)\n\n"
            "**Step 3.** BotFather가 보내주는 **토큰** 복사 (예: `7123456789:AAF...`)\n\n"
            "**Step 4.** 아래에 토큰 붙여넣기 → 저장\n\n"
            "**Step 5.** 만든 봇에 아무 메시지 보내기 (예: `hello`) → **Chat ID 자동 감지** 버튼 클릭\n"
        )

        with st.form("telegram_setup_form"):
            _new_tg_token = st.text_input(
                "봇 토큰 (BotFather에서 받은 값)",
                value=_tg_token,
                type="password",
                placeholder="7123456789:AAF...",
            )
            _new_tg_chat_id = st.text_input(
                "Chat ID (아래 자동 감지 사용 또는 직접 입력)",
                value=_tg_chat_id,
                placeholder="자동 감지 버튼을 먼저 사용하세요",
            )
            if st.form_submit_button("💾 저장", type="primary"):
                if _new_tg_token.strip():
                    _save_env_value("TELEGRAM_BOT_TOKEN", _new_tg_token.strip())
                if _new_tg_chat_id.strip():
                    _save_env_value("TELEGRAM_CHAT_ID", _new_tg_chat_id.strip())
                if _new_tg_token.strip() and _new_tg_chat_id.strip():
                    st.success("✅ 텔레그램 설정 저장 완료!")
                    st.rerun()
                elif _new_tg_token.strip():
                    st.success("토큰 저장됨. 봇에 메시지를 보낸 뒤 Chat ID 자동 감지를 눌러주세요.")
                    st.rerun()
                else:
                    st.error("봇 토큰을 입력하세요.")

        # Chat ID 자동 감지 버튼
        _saved_token = env.get("TELEGRAM_BOT_TOKEN", "")
        if _saved_token:
            st.caption("봇에 아무 메시지(예: hello)를 보낸 후 아래 버튼을 눌러주세요.")
            if st.button("🔍 Chat ID 자동 감지", type="primary"):
                with st.spinner("봇 메시지 확인 중..."):
                    _detected = detect_chat_id(_saved_token)
                if _detected:
                    _save_env_value("TELEGRAM_CHAT_ID", _detected)
                    st.success(f"✅ Chat ID 감지 완료: **{_detected}**")
                    st.rerun()
                else:
                    st.warning("메시지를 찾을 수 없습니다. 봇에 먼저 아무 메시지를 보내주세요.")

    st.markdown("---")

    # ── 테스트 알림 ────────────────────────────────────────────
    st.subheader("테스트 알림 전송")
    if st.button("텔레그램 테스트 메시지 전송"):
        result = send_telegram_message(
            f"📊 주식 포트폴리오 앱 테스트 메시지\n"
            f"전송 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            "알림 설정이 완료되었습니다!"
        )
        if result.get("success"):
            st.success("✅ 텔레그램으로 메시지를 전송했습니다!")
        else:
            st.error(f"전송 실패: {result.get('error')}")

    st.markdown("---")

    # ── 수동 일일 알림 ─────────────────────────────────────────
    st.subheader("수동 일일 리포트 전송")
    if st.button("지금 일일 리포트 전송"):
        stocks = get_all_stocks()
        if not stocks:
            st.warning("보유 종목을 먼저 추가하세요.")
        else:
            with st.spinner("포트폴리오 분석 및 전송 중..."):
                summary = get_portfolio_summary(stocks)
                report = run_daily_analysis(stocks, summary["items"])
                result = send_daily_notification(summary, report)
            if result.get("success"):
                st.success("✅ 일일 리포트를 텔레그램으로 전송했습니다!")
            else:
                st.error(f"전송 실패: {result.get('error')}")

    st.markdown("---")

    # ── 자동 리포트 스케줄러 ──────────────────────────────────
    st.subheader("자동 리포트 스케줄러")
    _notif_times = env.get("NOTIFICATION_TIME", "08:20, 12:20")
    st.caption(f"설정된 알림 시간: **{_notif_times}** (위 API 키 설정에서 변경 가능)")

    # 스케줄러 상태 관리
    if "_scheduler_obj" not in st.session_state:
        st.session_state._scheduler_obj = None
    if "_scheduler_thread" not in st.session_state:
        st.session_state._scheduler_thread = None

    _is_running = (
        st.session_state._scheduler_obj is not None
        and st.session_state._scheduler_obj.running
    )

    if _is_running:
        st.success("✅ 스케줄러 실행 중 — 설정 시간에 자동으로 리포트가 전송됩니다.")
        if st.button("스케줄러 중지", type="primary"):
            try:
                st.session_state._scheduler_obj.shutdown(wait=False)
            except Exception:
                pass
            st.session_state._scheduler_obj = None
            st.session_state._scheduler_thread = None
            st.success("스케줄러를 중지했습니다.")
            st.rerun()
    else:
        st.info("스케줄러가 꺼져 있습니다. 아래 버튼으로 시작하세요.")
        if st.button("스케줄러 시작", type="primary"):
            import threading
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            def _news_job():
                """뉴스만 전송 (설정된 모든 시간)"""
                try:
                    _nw_str = get_setting("news_watchlist", "")
                    _nw_list = [k.strip() for k in _nw_str.split(",") if k.strip()] if _nw_str else []
                    _inv_kw_str = get_setting("invest_watchlist_keywords", "")
                    _inv_kws = [k.strip() for k in _inv_kw_str.split(",") if k.strip()] if _inv_kw_str else []
                    _all_watchlist = list(dict.fromkeys(_nw_list + _inv_kws))
                    if _all_watchlist:
                        _news_msg = fetch_watchlist_news(_all_watchlist, max_per_item=3)
                        if _news_msg:
                            send_telegram_message(_news_msg)
                except Exception:
                    pass

            def _daily_report_job():
                """일일 리포트 + 뉴스 전송 (19:00에만)"""
                try:
                    _stocks = get_all_stocks()
                    if _stocks:
                        _summary = get_portfolio_summary(_stocks)
                        _report = run_daily_analysis(_stocks, _summary["items"])
                        send_daily_notification(_summary, _report)
                    _news_job()
                except Exception:
                    pass

            def _parse_times(s):
                times = []
                for part in s.split(","):
                    part = part.strip()
                    if ":" in part:
                        try:
                            h, m = part.split(":")
                            times.append((int(h), int(m)))
                        except Exception:
                            continue
                return times if times else [(8, 20)]

            _report_time_str = env.get("DAILY_REPORT_TIME", "19:00")
            _rpt_h, _rpt_m = _parse_times(_report_time_str)[0]

            _times = _parse_times(_notif_times)
            _bg_scheduler = BackgroundScheduler(timezone="Asia/Seoul")

            # 일일 리포트: 하루 1회 (19:00)
            _bg_scheduler.add_job(
                _daily_report_job,
                trigger=CronTrigger(hour=_rpt_h, minute=_rpt_m, timezone="Asia/Seoul"),
                id="daily_report",
                replace_existing=True,
            )

            # 뉴스: 설정된 모든 시간 (리포트 시간 제외 — 리포트에 뉴스 포함)
            for _i, (_h, _m) in enumerate(_times):
                if (_h, _m) == (_rpt_h, _rpt_m):
                    continue  # 리포트 시간에는 리포트 job에서 뉴스도 보냄
                _bg_scheduler.add_job(
                    _news_job,
                    trigger=CronTrigger(hour=_h, minute=_m, timezone="Asia/Seoul"),
                    id=f"news_{_i}",
                    replace_existing=True,
                )

            _bg_scheduler.start()
            st.session_state._scheduler_obj = _bg_scheduler

            _time_labels = ", ".join(f"{h:02d}:{m:02d}" for h, m in _times)
            st.success(
                f"✅ 스케줄러 시작!\n\n"
                f"- 뉴스: 매일 **{_time_labels}** (KST)\n"
                f"- 일일 리포트: 매일 **{_rpt_h:02d}:{_rpt_m:02d}** (KST)"
            )
            st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════
    # Google Sheets 연동
    # ══════════════════════════════════════════════════════════
    st.subheader("📊 Google Sheets 연동")
    st.caption(
        "포트폴리오·매도기록을 Google Sheets에 백업하거나 불러올 수 있습니다. "
        "Sheets에서 직접 수정 후 '불러오기'하면 앱에 반영됩니다."
    )

    from modules.gsheets import (
        is_available as gs_available,
        sync_to_sheets, sync_from_sheets, apply_import,
        get_spreadsheet_url,
    )

    # 설정값 불러오기 및 저장 폼
    _gs_id    = env.get("GOOGLE_SHEETS_ID", "")
    _gs_creds = env.get("GOOGLE_SHEETS_CREDENTIALS", "")

    with st.form("gsheets_config_form"):
        _gs_id_input = st.text_input(
            "스프레드시트 ID",
            value=_gs_id,
            placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            help="Google Sheets URL의 /d/ 와 /edit 사이 긴 문자열",
        )
        _gs_creds_input = st.text_input(
            "서비스 계정 JSON 파일 경로",
            value=_gs_creds,
            placeholder="/Users/.../stock_app/google_credentials.json",
            help="Google Cloud에서 발급한 서비스 계정 JSON 키 파일의 전체 경로",
        )
        if st.form_submit_button("설정 저장"):
            _save_env_value("GOOGLE_SHEETS_ID", _gs_id_input.strip())
            _save_env_value("GOOGLE_SHEETS_CREDENTIALS", _gs_creds_input.strip())
            st.success("✅ Google Sheets 설정이 저장되었습니다.")
            st.rerun()

    # 라이브러리 미설치 경고
    if not gs_available():
        st.warning(
            "⚠️ `gspread` 패키지가 설치되지 않았습니다. "
            "터미널에서 아래 명령어를 실행하세요:\n\n"
            "```\npip install gspread google-auth\n```"
        )
    elif not _gs_id or not _gs_creds:
        st.info("위에서 스프레드시트 ID와 서비스 계정 JSON 경로를 저장하면 동기화 버튼이 활성화됩니다.")
    else:
        # Sheets 링크 표시
        _gs_url = get_spreadsheet_url(_gs_id)
        st.markdown(f"🔗 **[Google Sheets 열기]({_gs_url})**")
        st.markdown("")

        _gc1, _gc2 = st.columns(2)

        # ── 업로드 (앱 → Sheets) ───────────────────────────
        with _gc1:
            st.markdown("#### ☁️ Sheets에 백업")
            st.caption("현재 앱의 DB → Google Sheets 덮어쓰기")
            if st.button("Sheets에 백업", use_container_width=True, key="gs_upload"):
                with st.spinner("Google Sheets에 업로드 중..."):
                    _port  = get_all_stocks()
                    _sold  = get_sold_history()
                    _result = sync_to_sheets(_gs_id, _gs_creds, _port, _sold)
                if _result["success"]:
                    st.success(_result["message"])
                else:
                    st.error(_result["message"])

        # ── 다운로드 (Sheets → 앱) ─────────────────────────
        with _gc2:
            st.markdown("#### 📥 Sheets에서 불러오기")
            st.caption("Google Sheets → 앱 DB 완전 교체 (되돌릴 수 없음)")
            if st.button("Sheets에서 불러오기", use_container_width=True,
                         key="gs_download", type="primary"):
                st.session_state["gs_confirm"] = True

        # 불러오기 확인 단계
        if st.session_state.get("gs_confirm"):
            st.warning(
                "⚠️ **주의:** 현재 앱의 포트폴리오·매도기록이 "
                "Google Sheets 내용으로 **완전히 교체**됩니다. "
                "백업이 필요하다면 먼저 '☁️ Sheets에 백업'을 실행하세요."
            )
            _conf1, _conf2 = st.columns(2)
            with _conf1:
                if st.button("✅ 확인, 불러오기 실행", type="primary",
                             use_container_width=True, key="gs_confirm_yes"):
                    with st.spinner("Google Sheets에서 불러오는 중..."):
                        _dl = sync_from_sheets(_gs_id, _gs_creds)
                    if _dl["success"]:
                        with st.spinner("DB에 적용 중..."):
                            _apply = apply_import(_dl["portfolio"], _dl["sold"])
                        if _apply["success"]:
                            st.success(_apply["message"])
                        else:
                            st.error(_apply["message"])
                    else:
                        st.error(_dl["message"])
                    st.session_state["gs_confirm"] = False
                    st.rerun()
            with _conf2:
                if st.button("❌ 취소", use_container_width=True, key="gs_confirm_no"):
                    st.session_state["gs_confirm"] = False
                    st.rerun()
