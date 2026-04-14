"""
gsheets.py - Google Sheets 양방향 동기화 모듈

OAuth2 방식 (내 구글 계정으로 직접 인증):
  1. Google Cloud Console → Google Sheets API + Google Drive API 활성화
  2. OAuth 동의 화면 설정 (외부 / 테스트 모드)
  3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
  4. 다운로드한 JSON → google_credentials.json 으로 저장
  5. 앱에서 처음 연결 시 브라우저에서 구글 로그인 → 자동 저장
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

try:
    import gspread
    _GSPREAD_OK = True
except ImportError:
    _GSPREAD_OK = False

# 토큰 저장 경로 (최초 인증 후 자동 저장, 이후 자동 로그인)
_TOKEN_PATH = str(Path(__file__).parent.parent / "google_token.json")

# 시트 이름
SHEET_PORTFOLIO = "포트폴리오"
SHEET_SOLD      = "매도기록"
SHEET_META      = "메타정보"

# 컬럼 순서
PORTFOLIO_COLS = [
    "id", "ticker", "name", "quantity", "purchase_price",
    "purchase_currency", "purchase_exchange_rate", "purchase_date",
    "broker", "account_type", "notes",
]

SOLD_COLS = [
    "id", "ticker", "name", "broker", "account_type",
    "purchase_date", "purchase_price", "purchase_currency", "purchase_exchange_rate",
    "sell_date", "sell_price", "sell_currency", "sell_exchange_rate",
    "quantity", "realized_gain_krw", "realized_gain_pct", "notes",
]


# ── 내부 헬퍼 ─────────────────────────────────────────────────

def is_available() -> bool:
    return _GSPREAD_OK


def _get_client(creds_path: str = ""):
    """
    인증 방식 자동 선택:
    1. Streamlit Secrets에 GOOGLE_OAUTH_TOKEN이 있으면 → OAuth refresh token 방식 (Cloud)
    2. Streamlit Secrets에 GOOGLE_CREDENTIALS_JSON이 있으면 → 서비스 계정 방식 (Cloud)
    3. 로컬 파일이 있으면 → OAuth2 방식 (Mac)
    """
    # 방식 1: Streamlit Secrets - OAuth refresh token (Cloud 환경)
    try:
        import streamlit as _st_gs
        import json as _json_gs
        from google.oauth2.credentials import Credentials as _OAuthCredentials

        _oauth_json = _st_gs.secrets.get("GOOGLE_OAUTH_TOKEN", "")
        if _oauth_json:
            if isinstance(_oauth_json, str):
                _info = _json_gs.loads(_oauth_json)
            else:
                _info = dict(_oauth_json)
            _creds = _OAuthCredentials(
                token=None,
                refresh_token=_info["refresh_token"],
                token_uri=_info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=_info["client_id"],
                client_secret=_info["client_secret"],
                scopes=_info.get("scopes", [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]),
            )
            return gspread.authorize(_creds)
    except Exception:
        pass

    # 방식 2: Streamlit Secrets - 서비스 계정 (Cloud 환경)
    try:
        import streamlit as _st_gs2
        import json as _json_gs2
        from google.oauth2.service_account import Credentials as _SACredentials

        _gs_json = _st_gs2.secrets.get("GOOGLE_CREDENTIALS_JSON", "")
        if _gs_json:
            if isinstance(_gs_json, str):
                _info = _json_gs2.loads(_gs_json)
            else:
                _info = dict(_gs_json)
            _scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            _creds = _SACredentials.from_service_account_info(_info, scopes=_scopes)
            return gspread.authorize(_creds)
    except Exception:
        pass

    # 방식 3: 로컬 OAuth2 (Mac 환경)
    if creds_path:
        return gspread.oauth(
            credentials_filename=creds_path,
            authorized_user_filename=_TOKEN_PATH,
        )

    raise RuntimeError("Google 인증 정보를 찾을 수 없습니다.")


def _ensure_worksheet(ss, name: str, rows: int = 2000, cols: int = 30):
    try:
        return ss.worksheet(name)
    except gspread.WorksheetNotFound:
        return ss.add_worksheet(title=name, rows=rows, cols=cols)


def _safe(val, default: str = "") -> str:
    if val is None:
        return default
    return str(val)


def _float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _str(val, default: str = "") -> str:
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()


def get_spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


# ── 공개 API ──────────────────────────────────────────────────

def sync_to_sheets(
    spreadsheet_id: str,
    creds_path: str,
    portfolio: list[dict],
    sold: list[dict],
) -> dict:
    """앱 DB → Google Sheets 업로드"""
    if not _GSPREAD_OK:
        return {"success": False, "message": "gspread 패키지가 없습니다. pip install gspread 를 실행하세요."}
    try:
        gc = _get_client(creds_path)
        ss = gc.open_by_key(spreadsheet_id)

        # 포트폴리오
        ws_p = _ensure_worksheet(ss, SHEET_PORTFOLIO)
        rows_p = [PORTFOLIO_COLS] + [[_safe(s.get(c)) for c in PORTFOLIO_COLS] for s in portfolio]
        ws_p.clear()
        ws_p.update(rows_p, value_input_option="USER_ENTERED")

        # 매도기록
        ws_s = _ensure_worksheet(ss, SHEET_SOLD)
        rows_s = [SOLD_COLS] + [[_safe(s.get(c)) for c in SOLD_COLS] for s in sold]
        ws_s.clear()
        ws_s.update(rows_s, value_input_option="USER_ENTERED")

        # 메타정보
        ws_m = _ensure_worksheet(ss, SHEET_META)
        ws_m.clear()
        ws_m.update([
            ["항목",           "값"],
            ["마지막 업로드",   datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["포트폴리오 건수", str(len(portfolio))],
            ["매도기록 건수",   str(len(sold))],
        ])

        return {
            "success": True,
            "message": f"업로드 완료 — 포트폴리오 {len(portfolio)}건 / 매도기록 {len(sold)}건",
        }
    except Exception as e:
        return {"success": False, "message": f"오류: {e}"}


def sync_from_sheets(spreadsheet_id: str, creds_path: str) -> dict:
    """Google Sheets → 앱 데이터 읽기"""
    if not _GSPREAD_OK:
        return {"success": False, "portfolio": [], "sold": [],
                "message": "gspread 패키지가 없습니다."}
    try:
        gc = _get_client(creds_path)
        ss = gc.open_by_key(spreadsheet_id)

        portfolio = ss.worksheet(SHEET_PORTFOLIO).get_all_records()

        try:
            sold = ss.worksheet(SHEET_SOLD).get_all_records()
        except gspread.WorksheetNotFound:
            sold = []

        return {
            "success": True,
            "portfolio": portfolio,
            "sold": sold,
            "message": f"불러오기 완료 — 포트폴리오 {len(portfolio)}건 / 매도기록 {len(sold)}건",
        }
    except Exception as e:
        return {"success": False, "portfolio": [], "sold": [], "message": f"오류: {e}"}


def apply_import(portfolio_records: list[dict], sold_records: list[dict]) -> dict:
    """Sheets 데이터를 DB에 적용 (기존 데이터 완전 교체)"""
    from modules.database import (
        clear_portfolio, clear_sold_history,
        add_stock, add_sold_record,
    )
    try:
        clear_portfolio()
        clear_sold_history()

        p_ok = 0
        for rec in portfolio_records:
            try:
                ticker = _str(rec.get("ticker"))
                if not ticker:
                    continue
                add_stock(
                    ticker=ticker.upper(),
                    name=_str(rec.get("name"), ticker),
                    quantity=_float(rec.get("quantity")),
                    purchase_price=_float(rec.get("purchase_price")),
                    purchase_currency=_str(rec.get("purchase_currency"), "USD"),
                    purchase_exchange_rate=_float(rec.get("purchase_exchange_rate"), 1.0),
                    purchase_date=_str(rec.get("purchase_date")),
                    broker=_str(rec.get("broker")),
                    account_type=_str(rec.get("account_type"), "일반계좌"),
                    notes=_str(rec.get("notes")),
                )
                p_ok += 1
            except Exception:
                continue

        s_ok = 0
        for rec in sold_records:
            try:
                ticker = _str(rec.get("ticker"))
                if not ticker:
                    continue
                add_sold_record(
                    ticker=ticker.upper(),
                    name=_str(rec.get("name"), ticker),
                    broker=_str(rec.get("broker")),
                    account_type=_str(rec.get("account_type"), "일반계좌"),
                    purchase_date=_str(rec.get("purchase_date")),
                    purchase_price=_float(rec.get("purchase_price")),
                    purchase_currency=_str(rec.get("purchase_currency"), "USD"),
                    purchase_exchange_rate=_float(rec.get("purchase_exchange_rate"), 1.0),
                    sell_date=_str(rec.get("sell_date")),
                    sell_price=_float(rec.get("sell_price")),
                    sell_currency=_str(rec.get("sell_currency"), "USD"),
                    sell_exchange_rate=_float(rec.get("sell_exchange_rate"), 1.0),
                    quantity=_float(rec.get("quantity")),
                    realized_gain_krw=_float(rec.get("realized_gain_krw")),
                    realized_gain_pct=_float(rec.get("realized_gain_pct")),
                    notes=_str(rec.get("notes")),
                )
                s_ok += 1
            except Exception:
                continue

        return {"success": True, "message": f"포트폴리오 {p_ok}건 / 매도기록 {s_ok}건 적용 완료"}

    except Exception as e:
        return {"success": False, "message": f"DB 저장 중 오류: {e}"}
