"""
notifications.py - 텔레그램 봇 알림 전송
"""

from __future__ import annotations

import os
import json
import requests
from pathlib import Path
from datetime import datetime

ENV_PATH = Path(__file__).parent.parent / ".env"

# Streamlit Cloud에서 Secrets → os.environ 으로 주입되는 설정 키 목록
_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "NOTIFICATION_TIME",
    "DAILY_REPORT_TIME",
    "GOOGLE_SHEETS_ID",
    "GOOGLE_SHEETS_CREDENTIALS",
    "GOOGLE_OAUTH_TOKEN",
    "APP_PASSWORD",
)


def _load_env():
    """
    설정값 로드. 우선순위:
      1. .env 파일 (로컬 Mac 사용자 최신 저장값)
      2. os.environ (Streamlit Cloud → Secrets 자동 주입)
    Cloud에서는 .env 파일이 없어도 Secrets 값을 바로 읽어옵니다.
    """
    data = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
    for key in _ENV_KEYS:
        if not data.get(key):
            val = os.environ.get(key, "")
            if val:
                data[key] = val
    return data


def is_cloud_env() -> bool:
    """Streamlit Cloud 환경인지 판별 (Secrets에는 값이 있지만 로컬 .env가 없는 상태)"""
    try:
        import streamlit as _st
        if _st.secrets and len(_st.secrets) > 0:
            return True
    except Exception:
        pass
    return False


def _save_env_value(key: str, value: str):
    """
    .env 파일의 특정 키 값 업데이트.
    Cloud 환경에서는 파일 저장이 컨테이너 재시작 시 소실되므로, 세션 동안에만 유효.
    영구 보존이 필요하면 Streamlit Cloud → Settings → Secrets에서 수정해야 함.
    """
    # Cloud에서도 일단 저장은 시도 (세션 내에서는 유효). 재시작 시 사라짐.
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{key}={value}\n", encoding="utf-8")
    else:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}")
        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    # 같은 세션 내에서 즉시 반영되도록 os.environ도 업데이트
    os.environ[key] = value


# ── 텔레그램 봇 ──────────────────────────────────────────────

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(message: str) -> dict:
    """
    텔레그램 봇으로 메시지 전송.
    성공: {"success": True}
    실패: {"success": False, "error": "..."}
    """
    env = _load_env()
    bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if not bot_token:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.\n설정 페이지에서 텔레그램 봇 토큰을 입력하세요."}
    if not chat_id:
        return {"success": False, "error": "TELEGRAM_CHAT_ID가 설정되지 않았습니다.\n설정 페이지에서 Chat ID를 입력하세요."}

    # 텔레그램 메시지 최대 4096자
    text = message[:4096]

    try:
        url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        data = resp.json()

        if data.get("ok"):
            return {"success": True}

        # HTML 파싱 실패 시 plain text로 재시도
        if "can't parse entities" in str(data.get("description", "")):
            resp2 = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }, timeout=15)
            data2 = resp2.json()
            if data2.get("ok"):
                return {"success": True}
            return {"success": False, "error": str(data2.get("description", data2))}

        return {"success": False, "error": str(data.get("description", data))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_telegram_updates(bot_token: str) -> list:
    """봇에 수신된 메시지 목록 조회 (chat_id 확인용)."""
    try:
        url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
        return []
    except Exception:
        return []


def detect_chat_id(bot_token: str) -> str | None:
    """봇에 메시지를 보낸 사용자의 chat_id 자동 감지."""
    updates = get_telegram_updates(bot_token)
    for upd in reversed(updates):
        msg = upd.get("message", {})
        chat = msg.get("chat", {})
        if chat.get("id"):
            return str(chat["id"])
    return None


# ── 메시지 포맷 ───────────────────────────────────────────────

def format_daily_message(summary: dict, report_text: str, date: str | None = None) -> str:
    """일일 알림용 메시지 포맷 구성"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    total_current = summary.get("total_current_krw", 0)
    total_gain = summary.get("total_gain_krw", 0)
    total_gain_pct = summary.get("total_gain_pct", 0)
    items = summary.get("items", [])

    arrow = "▲" if total_gain >= 0 else "▼"
    emoji = "📈" if total_gain >= 0 else "📉"

    now_time = datetime.now().strftime("%H:%M")

    lines = [
        f"🕐 {date} {now_time} (KST)",
        "",
        f"📊 [일일 포트폴리오 리포트]",
        "",
        f"💼 총 평가액: ₩{total_current:,.0f}",
        f"{emoji} 총 손익: {arrow}₩{abs(total_gain):,.0f} ({total_gain_pct:+.2f}%)",
        "",
        "─────────────────",
        "📌 종목별 현황:",
    ]

    symbol_map = {"USD": "$", "JPY": "¥", "KRW": "₩"}
    for item in items:
        sym = symbol_map.get(item.get("currency", "USD"), "$")
        name = item.get("name", item["ticker"])
        ticker = item["ticker"]
        qty = item.get("quantity", 0)
        curr_price = item.get("current_price", 0)
        daily_pct = item.get("daily_change_pct", 0)
        total_pct = item.get("total_gain_pct", 0)
        curr_val = item.get("current_value_krw", 0)
        fx_pct = item.get("fx_gain_pct", 0)
        currency = item.get("currency", "USD")

        d_arrow = "▲" if daily_pct >= 0 else "▼"
        lines.append(
            f"• {name} ({ticker}) {qty:.0f}주\n"
            f"  현재가: {sym}{curr_price:,.2f} ({d_arrow}{abs(daily_pct):.2f}%)\n"
            f"  평가액: ₩{curr_val:,.0f} | 총수익: {total_pct:+.2f}%"
        )
        if currency != "KRW":
            lines.append(f"  환율영향: {fx_pct:+.2f}%")

    lines += [
        "",
        "─────────────────",
        "📰 변동 이유 분석:",
        report_text,
    ]

    return "\n".join(lines)


def send_daily_notification(summary: dict, report_text: str) -> dict:
    """포트폴리오 요약 + AI 분석을 텔레그램으로 전송"""
    message = format_daily_message(summary, report_text)
    return send_telegram_message(message)


def fetch_watchlist_news(watchlist: list[str], max_per_item: int = 3) -> str:
    """
    관심 키워드/티커 목록의 뉴스를 가져와 한국어로 번역한 텔레그램 메시지 생성.
    watchlist: ["NVDA", "HBM", "AI 데이터센터", ...]
    """
    import yfinance as yf
    import anthropic

    if not watchlist:
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    all_news = []

    for keyword in watchlist:
        keyword = keyword.strip()
        if not keyword:
            continue

        # 티커로 시도
        try:
            t = yf.Ticker(keyword)
            news = t.news or []
            for item in news[:max_per_item]:
                title = item.get("content", {}).get("title", "")
                summary = item.get("content", {}).get("summary", "")
                url = item.get("content", {}).get("canonicalUrl", {}).get("url", "")
                if title:
                    all_news.append({
                        "keyword": keyword,
                        "title": title,
                        "summary": summary[:150],
                        "url": url,
                    })
        except Exception:
            pass

    if not all_news:
        return ""

    _header = f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} (KST)\n\n"

    # Claude로 한국어 번역
    if api_key:
        news_text = "\n".join(
            f"[{n['keyword']}] {n['title']}: {n['summary']}" for n in all_news
        )
        try:
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                messages=[{"role": "user", "content": (
                    "아래 주식/경제 뉴스를 한국어로 번역해서 텔레그램 메시지로 정리해주세요.\n\n"
                    "규칙:\n"
                    "- 키워드별로 그룹화 (🏷️ 키워드 형식)\n"
                    "- 각 키워드별로 뉴스 3개씩, 빠짐없이 모두 포함\n"
                    "- 각 뉴스는 번호 + 한 줄 제목 + 한 줄 요약\n"
                    "- 회사명/티커/금융 용어는 영어 유지\n"
                    "- 간결하게, 이모지 적절히 사용\n\n"
                    f"뉴스:\n{news_text}"
                )}],
            )
            return _header + msg.content[0].text.strip()
        except Exception:
            pass

    # 번역 실패 시 원본으로 포맷
    lines = [_header.strip(), "", "📰 관심 키워드 뉴스"]
    current_kw = ""
    for n in all_news:
        if n["keyword"] != current_kw:
            current_kw = n["keyword"]
            lines.append(f"\n🏷️ {current_kw}")
        lines.append(f"  • {n['title'][:80]}")
        if n["url"]:
            lines.append(f"    {n['url']}")
    return "\n".join(lines)
