"""
scheduler.py - 매일 자동으로 뉴스 + 포트폴리오 리포트를 텔레그램으로 전송

실행 모드:
  python scheduler.py                       # 로컬 상시 실행 (BlockingScheduler, Ctrl+C로 종료)
  python scheduler.py --now                 # 시작 직후 일일 리포트 1회 즉시 실행 + 상시 유지
  python scheduler.py --job report          # 일일 리포트 1회만 실행 후 종료 (GitHub Actions용)
  python scheduler.py --job news            # 뉴스만 1회 실행 후 종료 (GitHub Actions용)

CI(GitHub Actions)에서 실행 시 로컬 DB가 비어있으므로 시작 시 Google Sheets에서 포트폴리오를 자동 로드.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# 프로젝트 루트 경로 설정
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from modules.database import get_all_stocks, get_sold_history, get_setting
from modules.stock_data import get_portfolio_summary, aggregate_stocks_by_ticker
from modules.analysis import run_daily_analysis
from modules.notifications import send_daily_notification, send_telegram_message, fetch_watchlist_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "data" / "scheduler.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def bootstrap_from_sheets_if_empty():
    """
    CI에서 DB가 비어있으면 Google Sheets → DB 로드 (Streamlit 없이 직접 호출).
    modules.sheets_sync는 streamlit session_state에 의존하므로 여기서는 gsheets를 직접 사용.
    """
    sheet_id = os.getenv("GOOGLE_SHEETS_ID", "").strip()
    if not sheet_id:
        logger.info("GOOGLE_SHEETS_ID 미설정 — 시트 부트스트랩 건너뜀")
        return
    if get_all_stocks() or get_sold_history():
        logger.info("로컬 DB에 데이터 있음 — 시트 부트스트랩 건너뜀")
        return
    try:
        from modules.gsheets import sync_from_sheets, apply_import
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "").strip()
        result = sync_from_sheets(sheet_id, creds_path)
        if not result.get("success"):
            logger.warning(f"시트 로드 실패: {result.get('message')}")
            return
        portfolio = result.get("portfolio") or []
        sold = result.get("sold") or []
        if not portfolio and not sold:
            logger.info("시트에 데이터 없음")
            return
        apply = apply_import(portfolio, sold)
        if apply.get("success"):
            logger.info(f"시트에서 DB 복원: {apply.get('message')}")
        else:
            logger.warning(f"DB 적용 실패: {apply.get('message')}")
    except Exception as e:
        logger.exception(f"시트 부트스트랩 오류: {e}")


def news_job():
    """관심 키워드 뉴스만 전송"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info(f"=== 뉴스 전송: {now} ===")
    try:
        nw_str = get_setting("news_watchlist", "")
        inv_kw_str = get_setting("invest_watchlist_keywords", "")
        nw_list = [k.strip() for k in nw_str.split(",") if k.strip()] if nw_str else []
        inv_kws = [k.strip() for k in inv_kw_str.split(",") if k.strip()] if inv_kw_str else []
        all_watchlist = list(dict.fromkeys(nw_list + inv_kws))
        if all_watchlist:
            logger.info(f"키워드 뉴스 수집: {all_watchlist}")
            news_msg = fetch_watchlist_news(all_watchlist, max_per_item=3)
            if news_msg:
                result = send_telegram_message(news_msg)
                if result.get("success"):
                    logger.info("✅ 뉴스 전송 성공")
                else:
                    logger.error(f"❌ 뉴스 전송 실패: {result.get('error')}")
        else:
            logger.info("등록된 키워드 없음, 건너뜀")
    except Exception as e:
        logger.exception(f"뉴스 전송 오류: {e}")


def daily_report_job():
    """일일 포트폴리오 리포트 + 뉴스 전송 (하루 1회)"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info(f"=== 일일 리포트: {now} ===")
    try:
        raw_stocks = get_all_stocks()
        if not raw_stocks:
            logger.warning("보유 종목이 없어 건너뜁니다.")
        else:
            # 같은 티커 여러 매수 건은 합쳐서 1개로 (TOP 5가 전부 같은 종목이 되는 문제 방지)
            stocks = aggregate_stocks_by_ticker(raw_stocks)
            logger.info(f"종목 {len(raw_stocks)}건 → {len(stocks)}개 티커로 집계, 데이터 수집 중...")
            summary = get_portfolio_summary(stocks)
            logger.info("AI 분석 리포트 생성 중...")
            report = run_daily_analysis(stocks, summary["items"])
            logger.info("텔레그램 전송 중...")
            result = send_daily_notification(summary, report)
            if result.get("success"):
                logger.info("✅ 리포트 전송 성공")
            else:
                logger.error(f"❌ 리포트 전송 실패: {result.get('error')}")

        # 뉴스도 함께 전송
        news_job()

    except Exception as e:
        logger.exception(f"일일 리포트 오류: {e}")


def parse_times(time_str: str) -> list[tuple[int, int]]:
    """쉼표 구분 HH:MM 파싱"""
    times = []
    for part in time_str.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h, m = part.split(":")
            times.append((int(h), int(m)))
        except Exception:
            continue
    return times if times else [(8, 20)]


def _parse_arg_value(argv: list[str], flag: str) -> str | None:
    """'--job report' 또는 '--job=report' 둘 다 지원"""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(f"{flag}="):
            return a.split("=", 1)[1]
    return None


if __name__ == "__main__":
    # ── 원샷 모드 (GitHub Actions용): --job report | news ──
    job_arg = _parse_arg_value(sys.argv, "--job")
    if job_arg:
        bootstrap_from_sheets_if_empty()
        if job_arg == "report":
            logger.info("원샷 모드: 일일 리포트 실행")
            daily_report_job()
        elif job_arg == "news":
            logger.info("원샷 모드: 뉴스 실행")
            news_job()
        else:
            logger.error(f"알 수 없는 --job 값: {job_arg} (report | news 중 하나)")
            sys.exit(1)
        logger.info("원샷 모드 완료")
        sys.exit(0)

    # ── 상시 실행 모드 (로컬 Mac) ──
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    news_time_str = os.getenv("NOTIFICATION_TIME", "08:20, 12:20, 21:00")
    news_times = parse_times(news_time_str)

    report_time_str = os.getenv("DAILY_REPORT_TIME", "19:00")
    rpt_h, rpt_m = parse_times(report_time_str)[0]

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 일일 리포트: 하루 1회
    scheduler.add_job(
        daily_report_job,
        trigger=CronTrigger(hour=rpt_h, minute=rpt_m, timezone="Asia/Seoul"),
        id="daily_report",
        name=f"매일 {rpt_h:02d}:{rpt_m:02d} 일일 리포트",
        replace_existing=True,
    )
    logger.info(f"  일일 리포트: 매일 {rpt_h:02d}:{rpt_m:02d} (KST)")

    # 뉴스: 설정된 모든 시간 (리포트 시간 제외)
    for i, (h, m) in enumerate(news_times):
        if (h, m) == (rpt_h, rpt_m):
            continue
        scheduler.add_job(
            news_job,
            trigger=CronTrigger(hour=h, minute=m, timezone="Asia/Seoul"),
            id=f"news_{i}",
            name=f"매일 {h:02d}:{m:02d} 뉴스",
            replace_existing=True,
        )
        logger.info(f"  뉴스: 매일 {h:02d}:{m:02d} (KST)")

    logger.info("스케줄러 시작. 종료하려면 Ctrl+C")

    if "--now" in sys.argv:
        logger.info("--now 옵션: 즉시 실행")
        daily_report_job()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
