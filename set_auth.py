"""
set_auth.py - 로그인 자격 증명 설정 도우미 (일회성 실행)

사용법:
    .venv/bin/python set_auth.py

실행하면 아이디·비밀번호를 터미널에서 입력받아 bcrypt 해시로 변환하고,
쿠키 서명 키도 새로 생성해서 .env와 Streamlit Secrets용 블록을 출력합니다.

비밀번호는 화면에 표시되지 않고, 파일이나 로그에 평문으로 남지도 않습니다.
"""
import getpass
import secrets
import sys
import re
from pathlib import Path

try:
    import bcrypt
except ImportError:
    print("❌ bcrypt 없음. 먼저: pip install bcrypt")
    sys.exit(1)


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _update_env_file(env_path: Path, values: dict):
    """기존 .env의 AUTH_* 키를 갈아끼움. 없으면 추가."""
    existing = {}
    lines = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                existing[k] = True
                if k in values:
                    continue  # 기존 AUTH_* 라인은 제거 후 재작성
            lines.append(line)

    if lines and lines[-1].strip() != "":
        lines.append("")
    for k, v in values.items():
        lines.append(f"{k}={v}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    root = Path(__file__).parent
    env_path = root / ".env"

    print("\n🔐 로그인 자격 증명 설정\n")

    username = input("사용하실 아이디 (영문·숫자·underscore만): ").strip()
    if not re.match(r"^[A-Za-z0-9_]{3,32}$", username):
        print("❌ 아이디는 3~32자, 영문·숫자·언더바만.")
        sys.exit(1)

    name = input(f"표시용 이름 (엔터시 '{username}'): ").strip() or username

    while True:
        pw1 = getpass.getpass("비밀번호 (최소 8자): ")
        if len(pw1) < 8:
            print("❌ 8자 이상.")
            continue
        pw2 = getpass.getpass("비밀번호 다시: ")
        if pw1 != pw2:
            print("❌ 두 번 입력한 값이 다릅니다.")
            continue
        break

    pw_hash = _hash_password(pw1)
    cookie_key = secrets.token_urlsafe(32)

    values = {
        "AUTH_USERNAME":     username,
        "AUTH_NAME":         name,
        "AUTH_PASSWORD_HASH": pw_hash,
        "AUTH_COOKIE_KEY":   cookie_key,
    }

    _update_env_file(env_path, values)

    print(f"\n✅ 로컬 .env 업데이트 완료 → {env_path}\n")
    print("━" * 60)
    print("Streamlit Cloud / GitHub Secrets에 넣을 블록 (복사용):")
    print("━" * 60)
    for k, v in values.items():
        # bcrypt 해시·쿠키 키는 쌍따옴표 감쌈
        print(f'{k} = "{v}"')
    print("━" * 60)
    print("\n이제 챗으로 '완료' 답해주시면 app.py 로그인 교체 + 푸시까지 처리합니다.")
    print("비밀번호는 어디에도 평문으로 저장되지 않았습니다.")


if __name__ == "__main__":
    main()
