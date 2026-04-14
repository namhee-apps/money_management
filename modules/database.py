"""
database.py - SQLite 데이터베이스 초기화 및 CRUD 작업
"""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "portfolio.db"


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """DB 스키마 초기화 (최초 실행 시 테이블 생성 + 기존 DB 마이그레이션)"""
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            quantity REAL NOT NULL,
            purchase_price REAL NOT NULL,
            purchase_currency TEXT NOT NULL DEFAULT 'USD',
            purchase_exchange_rate REAL NOT NULL DEFAULT 1.0,
            purchase_date TEXT NOT NULL,
            broker TEXT NOT NULL DEFAULT '',
            account_type TEXT NOT NULL DEFAULT '일반계좌',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS sold_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            broker TEXT DEFAULT '',
            account_type TEXT DEFAULT '일반계좌',
            purchase_date TEXT,
            purchase_price REAL,
            purchase_currency TEXT,
            purchase_exchange_rate REAL DEFAULT 1.0,
            sell_date TEXT NOT NULL,
            sell_price REAL NOT NULL,
            sell_currency TEXT NOT NULL,
            sell_exchange_rate REAL NOT NULL DEFAULT 1.0,
            quantity REAL NOT NULL,
            realized_gain_krw REAL,
            realized_gain_pct REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS daily_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            close_price REAL,
            current_exchange_rate REAL,
            value_krw REAL,
            daily_change_pct REAL,
            UNIQUE(date, ticker)
        );

        CREATE TABLE IF NOT EXISTS daily_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            report_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dividends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            payment_date TEXT NOT NULL,
            amount_per_share REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            exchange_rate REAL NOT NULL DEFAULT 1.0,
            quantity REAL NOT NULL,
            total_amount REAL NOT NULL,
            total_amount_krw REAL NOT NULL DEFAULT 0,
            broker TEXT DEFAULT '',
            account_type TEXT DEFAULT '일반계좌',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS recurring_investments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'JPY',
            day_of_month INTEGER NOT NULL DEFAULT 15,
            broker TEXT DEFAULT '',
            account_type TEXT DEFAULT 'NISA (적립투자)',
            portfolio_group TEXT DEFAULT '積立NISA',
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            start_date TEXT NOT NULL,
            last_added_date TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
    """)

    # 기존 DB 마이그레이션: 누락 컬럼 추가
    for col, definition in [
        ("account_type", "TEXT NOT NULL DEFAULT '일반계좌'"),
        ("broker",       "TEXT NOT NULL DEFAULT ''"),
        ("portfolio_group", "TEXT NOT NULL DEFAULT '개별주식'"),
    ]:
        try:
            cur.execute(f"ALTER TABLE portfolio ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # 이미 컬럼이 있으면 무시

    conn.commit()
    conn.close()


# ── 포트폴리오 CRUD ───────────────────────────────────────────

def add_stock(ticker: str, name: str, quantity: float, purchase_price: float,
              purchase_currency: str, purchase_exchange_rate: float, purchase_date: str,
              broker: str = "", account_type: str = "일반계좌", notes: str = "",
              portfolio_group: str = "개별주식") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO portfolio (ticker, name, quantity, purchase_price,
                               purchase_currency, purchase_exchange_rate, purchase_date,
                               broker, account_type, notes, portfolio_group)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker.upper(), name, quantity, purchase_price,
          purchase_currency, purchase_exchange_rate, purchase_date,
          broker, account_type, notes, portfolio_group))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_all_stocks() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM portfolio ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def update_stock(stock_id: int, quantity: float, purchase_price: float,
                 purchase_exchange_rate: float, account_type: str, notes: str,
                 broker: str | None = None):
    conn = get_connection()
    if broker is not None:
        conn.execute("""
            UPDATE portfolio SET quantity=?, purchase_price=?, purchase_exchange_rate=?,
                                 account_type=?, notes=?, broker=?
            WHERE id=?
        """, (quantity, purchase_price, purchase_exchange_rate, account_type, notes, broker, stock_id))
    else:
        conn.execute("""
            UPDATE portfolio SET quantity=?, purchase_price=?, purchase_exchange_rate=?,
                                 account_type=?, notes=?
            WHERE id=?
        """, (quantity, purchase_price, purchase_exchange_rate, account_type, notes, stock_id))
    conn.commit()
    conn.close()


def delete_stock(stock_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM portfolio WHERE id=?", (stock_id,))
    conn.commit()
    conn.close()


def reduce_stock_quantity(stock_id: int, new_qty: float):
    """매도 후 포트폴리오 수량 차감. 0 이하면 항목 삭제."""
    conn = get_connection()
    if new_qty <= 0.0001:
        conn.execute("DELETE FROM portfolio WHERE id=?", (stock_id,))
    else:
        conn.execute("UPDATE portfolio SET quantity=? WHERE id=?", (new_qty, stock_id))
    conn.commit()
    conn.close()


def update_stock_name(stock_id: int, name: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET name=? WHERE id=?", (name, stock_id))
    conn.commit()
    conn.close()


def update_stock_date(stock_id: int, purchase_date: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET purchase_date=? WHERE id=?", (purchase_date, stock_id))
    conn.commit()
    conn.close()


def update_stock_exchange_rate(stock_id: int, rate: float):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET purchase_exchange_rate=? WHERE id=?", (rate, stock_id))
    conn.commit()
    conn.close()


def update_stock_broker(stock_id: int, broker: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET broker=? WHERE id=?", (broker, stock_id))
    conn.commit()
    conn.close()


def update_stock_account_type(stock_id: int, account_type: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET account_type=? WHERE id=?", (account_type, stock_id))
    conn.commit()
    conn.close()


def update_stock_ticker(stock_id: int, ticker: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET ticker=? WHERE id=?", (ticker.upper(), stock_id))
    conn.commit()
    conn.close()


def update_stock_group(stock_id: int, portfolio_group: str):
    conn = get_connection()
    conn.execute("UPDATE portfolio SET portfolio_group=? WHERE id=?", (portfolio_group, stock_id))
    conn.commit()
    conn.close()


# ── 정기 적립 (積立) ──────────────────────────────────────────

def add_recurring_investment(ticker: str, name: str, amount: float, currency: str,
                              day_of_month: int, broker: str, account_type: str,
                              portfolio_group: str, start_date: str, notes: str = "") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurring_investments
        (ticker, name, amount, currency, day_of_month, broker, account_type,
         portfolio_group, start_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, name, amount, currency, day_of_month, broker, account_type,
          portfolio_group, start_date, notes))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_recurring_investments(active_only: bool = True) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    if active_only:
        cur.execute("SELECT * FROM recurring_investments WHERE active=1 ORDER BY created_at DESC")
    else:
        cur.execute("SELECT * FROM recurring_investments ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def update_recurring_last_added(rec_id: int, last_added_date: str):
    conn = get_connection()
    conn.execute("UPDATE recurring_investments SET last_added_date=? WHERE id=?",
                 (last_added_date, rec_id))
    conn.commit()
    conn.close()


def toggle_recurring_investment(rec_id: int, active: bool):
    conn = get_connection()
    conn.execute("UPDATE recurring_investments SET active=? WHERE id=?", (1 if active else 0, rec_id))
    conn.commit()
    conn.close()


def delete_recurring_investment(rec_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM recurring_investments WHERE id=?", (rec_id,))
    conn.commit()
    conn.close()


# ── 일일 스냅샷 ───────────────────────────────────────────────

def save_snapshot(date: str, ticker: str, close_price: float,
                  current_exchange_rate: float, value_krw: float, daily_change_pct: float):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO daily_snapshot
        (date, ticker, close_price, current_exchange_rate, value_krw, daily_change_pct)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date, ticker, close_price, current_exchange_rate, value_krw, daily_change_pct))
    conn.commit()
    conn.close()


def get_snapshots(ticker: str, limit: int = 30) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM daily_snapshot WHERE ticker=?
        ORDER BY date DESC LIMIT ?
    """, (ticker, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_snapshots_by_date(date: str) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_snapshot WHERE date=?", (date,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_portfolio_value_history(limit: int = 90) -> list[dict]:
    """날짜별 포트폴리오 전체 평가금액(KRW) 합산 추이."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date, SUM(value_krw) as total_value_krw, COUNT(ticker) as ticker_count
        FROM daily_snapshot
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── 일일 리포트 ───────────────────────────────────────────────

def save_report(date: str, report_text: str):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO daily_report (date, report_text)
        VALUES (?, ?)
    """, (date, report_text))
    conn.commit()
    conn.close()


def get_reports(limit: int = 10) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_report ORDER BY date DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_report_by_date(date: str) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_report WHERE date=?", (date,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ── 매도 이력 ─────────────────────────────────────────────────

def add_sold_record(ticker: str, name: str, broker: str, account_type: str,
                    purchase_date: str, purchase_price: float, purchase_currency: str,
                    purchase_exchange_rate: float, sell_date: str, sell_price: float,
                    sell_currency: str, sell_exchange_rate: float, quantity: float,
                    realized_gain_krw: float, realized_gain_pct: float, notes: str = "") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sold_history (
            ticker, name, broker, account_type,
            purchase_date, purchase_price, purchase_currency, purchase_exchange_rate,
            sell_date, sell_price, sell_currency, sell_exchange_rate,
            quantity, realized_gain_krw, realized_gain_pct, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, name, broker, account_type,
          purchase_date, purchase_price, purchase_currency, purchase_exchange_rate,
          sell_date, sell_price, sell_currency, sell_exchange_rate,
          quantity, realized_gain_krw, realized_gain_pct, notes))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_sold_history(ticker: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    if ticker:
        cur.execute(
            "SELECT * FROM sold_history WHERE ticker=? ORDER BY sell_date DESC LIMIT ?",
            (ticker, limit)
        )
    else:
        cur.execute(
            "SELECT * FROM sold_history ORDER BY sell_date DESC LIMIT ?",
            (limit,)
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_sold_summary_by_ticker() -> list[dict]:
    """티커별 실현 손익 합산"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, name,
               SUM(quantity)          AS total_sold_qty,
               SUM(realized_gain_krw) AS total_realized_gain_krw,
               COUNT(*)               AS sell_count,
               MAX(sell_date)         AS last_sell_date
        FROM sold_history
        GROUP BY ticker
        ORDER BY ticker
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def delete_sold_record(record_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM sold_history WHERE id=?", (record_id,))
    conn.commit()
    conn.close()


# ── 배당금 ───────────────────────────────────────────────────

def add_dividend(ticker: str, name: str, payment_date: str,
                 amount_per_share: float, currency: str, exchange_rate: float,
                 quantity: float, total_amount: float, total_amount_krw: float,
                 broker: str = "", account_type: str = "일반계좌", notes: str = "") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dividends (ticker, name, payment_date, amount_per_share,
            currency, exchange_rate, quantity, total_amount, total_amount_krw,
            broker, account_type, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, name, payment_date, amount_per_share, currency, exchange_rate,
          quantity, total_amount, total_amount_krw, broker, account_type, notes))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_dividends(ticker=None, limit=500):
    conn = get_connection()
    cur = conn.cursor()
    if ticker:
        cur.execute("SELECT * FROM dividends WHERE ticker=? ORDER BY payment_date DESC LIMIT ?",
                     (ticker, limit))
    else:
        cur.execute("SELECT * FROM dividends ORDER BY payment_date DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_dividend_summary():
    """전체 배당금 합계"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT SUM(total_amount_krw) AS total_krw,
               COUNT(*) AS count
        FROM dividends
    """)
    row = dict(cur.fetchone())
    conn.close()
    return row


def delete_dividend(record_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM dividends WHERE id=?", (record_id,))
    conn.commit()
    conn.close()


# ── 앱 설정 (key-value) ─────────────────────────────────────

def save_setting(key: str, value: str):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def save_settings_bulk(data: dict):
    conn = get_connection()
    for k, v in data.items():
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()


def get_settings_bulk(prefix: str) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_settings WHERE key LIKE ?", (f"{prefix}%",))
    result = {r["key"]: r["value"] for r in cur.fetchall()}
    conn.close()
    return result


# ── Google Sheets 임포트용 전체 초기화 ───────────────────────

def clear_portfolio():
    """포트폴리오 전체 삭제 (Sheets 임포트 전 호출)"""
    conn = get_connection()
    conn.execute("DELETE FROM portfolio")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='portfolio'")
    conn.commit()
    conn.close()


def clear_sold_history():
    """매도기록 전체 삭제 (Sheets 임포트 전 호출)"""
    conn = get_connection()
    conn.execute("DELETE FROM sold_history")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='sold_history'")
    conn.commit()
    conn.close()


# DB 자동 초기화
init_db()
