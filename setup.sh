#!/bin/bash
# setup.sh - 주식 포트폴리오 앱 초기 설치 스크립트

set -e

echo "================================================"
echo "  주식 포트폴리오 모니터링 앱 설치"
echo "================================================"

# Python 버전 확인
python3 --version

# 가상환경 생성
echo ""
echo "▶ 가상환경 생성 중..."
python3 -m venv .venv

# 활성화
source .venv/bin/activate

# pip 업그레이드
pip install --upgrade pip -q

# 패키지 설치
echo "▶ 패키지 설치 중 (약 1-2분 소요)..."
pip install -r requirements.txt -q

# .env 파일 생성 (없으면)
if [ ! -f .env ]; then
    cp .env.example .env
    echo "▶ .env 파일 생성됨 (API 키를 입력하세요)"
fi

echo ""
echo "================================================"
echo "  설치 완료!"
echo "================================================"
echo ""
echo "▶ 앱 실행 방법:"
echo "   source .venv/bin/activate"
echo "   streamlit run app.py"
echo ""
echo "▶ 일일 자동 알림 실행 방법:"
echo "   source .venv/bin/activate"
echo "   python scheduler.py"
echo ""
echo "▶ 즉시 알림 테스트:"
echo "   python scheduler.py --now"
echo ""
