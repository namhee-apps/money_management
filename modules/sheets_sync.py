"""
sheets_sync.py - 포트폴리오 DB ↔ Google Sheets 자동 동기화 헬퍼

정책:
  - 시트가 원본(source of truth), 로컬 DB는 캐시.
  - 앱 cold-start 시: 로컬 DB가 비어 있고 시트에 데이터가 있으면 시트 → DB 로드.
    (로컬 DB에 데이터가 있으면 절대 자동 교체하지 않음 — 사용자 데이터 손실 방지)
  - 사용자 편집 시: 세션 플래그(_sheet_dirty)를 세팅 → 다음 rerun 초기에 시트로 푸시.
"""

from __future__ import annotations

from datetime import datetime

SESSION_FLAG_DIRTY = "_sheet_dirty"
SESSION_FLAG_LOADED = "_sheet_autoloaded"
SESSION_FLAG_LAST_PUSH = "_sheet_last_push_at"
SESSION_FLAG_LAST_ERROR = "_sheet_last_error"


def _get_sheet_config() -> tuple[str, str]:
    """env에서 시트 ID와 credentials 경로 조회"""
    from modules.notifications import _load_env
    env = _load_env()
    return env.get("GOOGLE_SHEETS_ID", "").strip(), env.get("GOOGLE_SHEETS_CREDENTIALS", "").strip()


def is_sheets_configured() -> bool:
    sid, _ = _get_sheet_config()
    return bool(sid)


def mark_dirty():
    """DB 변경 사항 발생 표시. 다음 push_if_dirty() 호출 시 시트로 업로드됨."""
    try:
        import streamlit as st
        st.session_state[SESSION_FLAG_DIRTY] = True
    except Exception:
        pass


def auto_load_if_empty() -> dict:
    """
    DB가 비어 있고 시트에 데이터가 있으면 시트 → DB 로드.
    세션당 한 번만 실행. 이미 데이터가 있는 DB는 건드리지 않음 (데이터 손실 방지).
    반환: {"loaded": bool, "message": str}
    """
    try:
        import streamlit as st
    except Exception:
        return {"loaded": False, "message": "streamlit unavailable"}

    if st.session_state.get(SESSION_FLAG_LOADED):
        return {"loaded": False, "message": "already checked this session"}
    st.session_state[SESSION_FLAG_LOADED] = True

    sid, creds = _get_sheet_config()
    if not sid:
        return {"loaded": False, "message": "sheet not configured"}

    try:
        from modules.database import get_all_stocks, get_sold_history
        from modules.gsheets import sync_from_sheets, apply_import

        # 로컬 DB에 데이터가 있으면 절대 덮어쓰지 않음
        if get_all_stocks() or get_sold_history():
            return {"loaded": False, "message": "local db already has data"}

        result = sync_from_sheets(sid, creds)
        if not result.get("success"):
            return {"loaded": False, "message": result.get("message", "sheet read failed")}

        portfolio = result.get("portfolio") or []
        sold = result.get("sold") or []
        if not portfolio and not sold:
            return {"loaded": False, "message": "sheet is empty"}

        apply = apply_import(portfolio, sold)
        if apply.get("success"):
            return {"loaded": True, "message": apply.get("message", "loaded from sheet")}
        return {"loaded": False, "message": apply.get("message", "apply failed")}
    except Exception as e:
        return {"loaded": False, "message": f"error: {e}"}


def push_if_dirty() -> dict:
    """
    _sheet_dirty 플래그가 세팅되어 있으면 시트로 업로드.
    실패해도 조용히 기록만 남기고 사용자 흐름을 막지 않음.
    """
    try:
        import streamlit as st
    except Exception:
        return {"pushed": False, "message": "streamlit unavailable"}

    if not st.session_state.get(SESSION_FLAG_DIRTY):
        return {"pushed": False, "message": "not dirty"}

    sid, creds = _get_sheet_config()
    if not sid:
        # 시트가 설정되어 있지 않으면 dirty 플래그만 비움
        st.session_state[SESSION_FLAG_DIRTY] = False
        return {"pushed": False, "message": "sheet not configured"}

    try:
        from modules.database import get_all_stocks, get_sold_history
        from modules.gsheets import sync_to_sheets

        result = sync_to_sheets(sid, creds, get_all_stocks(), get_sold_history())
        st.session_state[SESSION_FLAG_DIRTY] = False
        if result.get("success"):
            st.session_state[SESSION_FLAG_LAST_PUSH] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state[SESSION_FLAG_LAST_ERROR] = ""
            return {"pushed": True, "message": result.get("message", "pushed")}
        st.session_state[SESSION_FLAG_LAST_ERROR] = result.get("message", "push failed")
        return {"pushed": False, "message": st.session_state[SESSION_FLAG_LAST_ERROR]}
    except Exception as e:
        st.session_state[SESSION_FLAG_DIRTY] = False
        st.session_state[SESSION_FLAG_LAST_ERROR] = str(e)
        return {"pushed": False, "message": f"error: {e}"}


def wrap_mutations(db_module):
    """
    database 모듈의 변경 함수들을 감싸서, 호출되면 자동으로 dirty 플래그를 세팅.
    app.py가 import한 이름들을 직접 재지정하는 방식이 아니라,
    app.py 쪽에서 이 함수로 래핑된 버전을 받아 쓰는 형태.
    """
    mutation_names = (
        "add_stock", "update_stock", "delete_stock",
        "update_stock_name", "update_stock_date", "update_stock_exchange_rate",
        "update_stock_broker", "update_stock_account_type", "update_stock_ticker",
        "update_stock_group", "reduce_stock_quantity",
        "add_sold_record", "delete_sold_record",
    )
    wrapped = {}
    for name in mutation_names:
        fn = getattr(db_module, name, None)
        if fn is None:
            continue
        wrapped[name] = _make_wrapper(fn)
    return wrapped


def _make_wrapper(fn):
    def _wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        mark_dirty()
        return result
    _wrapped.__name__ = fn.__name__
    _wrapped.__doc__ = fn.__doc__
    return _wrapped
