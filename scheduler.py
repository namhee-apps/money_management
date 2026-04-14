"""
scheduler.py - 매일 자동으로 뉴스 + 포트폴리오 리포트를 텔레그램으로 전송
실행: python scheduler.py
(백그라운드 유지 실행 → Ctrl+C로 종료)
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 프로젝트 루트 경로 설정
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from modules.database import get_all_stocks, get_setting
from modules.stock_data import get_portfolio_summary
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
        stocks = get_all_stocks()
        if not stocks:
            logger.warning("보유 종목이 없어 건너뜁니다.")
        else:
            logger.info(f"종목 {len(stocks)}개 데이터 수집 중...")
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


if __name__ == "__main__":
    # 뉴스 시간
    news_time_str = os.getenv("NOTIFICATION_TIME", "08:20, 12:20, 21:00")
    news_times = parse_times(news_time_str)

    # 리포트 시간
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
