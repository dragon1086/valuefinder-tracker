#!/usr/bin/env python3
"""
ValueFinder Tracker
- valuefinder.co.kr/bbs/board.php?bo_table=report 크롤링
- 신규 종목 감지 → 텔레그램 알림
- 작성일 기준 가격 추적 → 매일 수익률 현황 발송
"""

import os
import re
import json
import sqlite3
import logging
import requests
import subprocess
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from pathlib import Path

# ── 환경변수 로드 (~/.zshrc) ─────────────────────────────────────
def _load_env():
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    for line in zshrc.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and val and key not in os.environ:
                os.environ[key] = val

_load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "7726642089")

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "db" / "valuefinder.sqlite"
LOG_PATH = BASE_DIR / "logs" / "tracker.log"

BOARD_URL = "https://valuefinder.co.kr/bbs/board.php?bo_table=report"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)


# ── DB 초기화 ─────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            wr_id           INTEGER PRIMARY KEY,
            company         TEXT,
            title           TEXT,
            author          TEXT,
            report_date     TEXT,
            url             TEXT,
            ticker          TEXT,
            price_on_date   REAL,
            discovered_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            wr_id       INTEGER,
            snap_date   TEXT,
            price       REAL,
            pct_change  REAL,
            UNIQUE(wr_id, snap_date)
        )
    """)
    conn.commit()
    return conn


# ── 크롤링 ────────────────────────────────────────────────────────
def fetch_board(pages: int = 3) -> list[dict]:
    """
    gnuboard 구조:
    td[0]=날짜  td[1]=빈칸  td[2]=종목명  td[3]=제목(링크)  td[4]=작성자
    td[5]=-    td[6]=rating  td[7]=링크  td[8]=조회수
    """
    items = []
    headers = {"User-Agent": "Mozilla/5.0"}
    wr_id_re = re.compile(r'wr_id=(\d+)')

    for page in range(1, pages + 1):
        url = BOARD_URL if page == 1 else f"{BOARD_URL}&page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
        except Exception as e:
            log.warning("fetch 실패 (page %d): %s", page, e)
            break

        soup = BeautifulSoup(r.text, "html.parser")

        for row in soup.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 8:
                continue

            # td[0]: 날짜 YYYY.MM.DD
            report_date = tds[0].get_text(strip=True)
            if not re.match(r'^\d{4}\.\d{2}\.\d{2}$', report_date):
                continue

            # td[2]: 종목명 (없을 수도 있음 - 산업분석보고서 등)
            company = tds[2].get_text(strip=True)

            # td[3]: 제목 (링크 포함, 텍스트가 2배 중복되는 경우 있음)
            title_td = tds[3]
            a_tag = title_td.find("a", href=wr_id_re)
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            m = wr_id_re.search(href)
            if not m:
                continue
            wr_id = int(m.group(1))

            # 제목: a 태그 직계 텍스트만 (서브텍스트 제외)
            raw_title = a_tag.get_text(strip=True)
            # 중복 텍스트 제거 (절반이 반복되는 패턴)
            half = len(raw_title) // 2
            if raw_title[:half] == raw_title[half:]:
                raw_title = raw_title[:half]
            title = raw_title

            # td[4]: 작성자
            author = tds[4].get_text(strip=True)

            items.append({
                "wr_id":       wr_id,
                "company":     company,
                "title":       title,
                "author":      author,
                "report_date": report_date,
                "url": f"https://valuefinder.co.kr/bbs/board.php?bo_table=report&wr_id={wr_id}",
            })

    # wr_id 기준 중복 제거
    seen, unique = set(), []
    for item in items:
        if item["wr_id"] not in seen:
            seen.add(item["wr_id"])
            unique.append(item)

    log.info("크롤링 완료: %d건", len(unique))
    return unique


# ── 티커 조회 (FinanceDataReader) ─────────────────────────────────
_fdr_listing: object = None  # DataFrame

def get_fdr_listing():
    global _fdr_listing
    if _fdr_listing is not None:
        return _fdr_listing
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        _fdr_listing = df
        log.info("FDR KRX 종목 로드: %d건", len(df))
        return df
    except Exception as e:
        log.warning("FDR StockListing 실패: %s", e)
        return None


def resolve_ticker(company: str) -> str | None:
    if not company:
        return None
    df = get_fdr_listing()
    if df is None:
        return None
    # 완전 일치
    exact = df[df["Name"] == company]
    if not exact.empty:
        return str(exact.iloc[0]["Code"])
    # 부분 일치
    partial = df[df["Name"].str.contains(company, na=False, regex=False)]
    if not partial.empty:
        return str(partial.iloc[0]["Code"])
    # 역방향: 종목명이 company를 포함
    for _, row in df.iterrows():
        if isinstance(row["Name"], str) and row["Name"] in company:
            return str(row["Code"])
    return None


# ── 주가 조회 (FinanceDataReader) ─────────────────────────────────
def get_price_on_date(ticker: str, report_date: str) -> float | None:
    """
    report_date: "2026.03.26" 형식
    → 해당일 또는 이후 첫 거래일 종가 반환
    """
    try:
        import FinanceDataReader as fdr
        start = report_date.replace(".", "-")          # "2026-03-26"
        end   = date.today().strftime("%Y-%m-%d")
        df = fdr.DataReader(ticker, start, end)
        if df is None or df.empty:
            return None
        return float(df.iloc[0]["Close"])
    except Exception as e:
        log.warning("작성일 주가 조회 실패 (%s, %s): %s", ticker, report_date, e)
        return None


def get_latest_price(ticker: str) -> float | None:
    """가장 최근 거래일 종가"""
    try:
        import FinanceDataReader as fdr
        end   = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        df = fdr.DataReader(ticker, start, end)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["Close"])
    except Exception as e:
        log.warning("최신 주가 조회 실패 (%s): %s", ticker, e)
        return None


# ── 텔레그램 ──────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN 없음 — 텔레그램 스킵")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error("텔레그램 전송 실패: %s", e)


# ── 신규 종목 처리 ────────────────────────────────────────────────
def process_new_item(conn: sqlite3.Connection, item: dict):
    ticker = resolve_ticker(item["company"])
    price_on_date = None

    if ticker:
        price_on_date = get_price_on_date(ticker, item["report_date"])
        log.info("  티커: %s → %s, 작성일 가격: %s", item["company"], ticker, price_on_date)
    else:
        log.warning("  티커 미발견: %s", item["company"])

    conn.execute("""
        INSERT OR IGNORE INTO reports
        (wr_id, company, title, author, report_date, url, ticker, price_on_date, discovered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["wr_id"], item["company"], item["title"],
        item["author"], item["report_date"], item["url"],
        ticker, price_on_date,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()

    # 텔레그램 신규 알림
    ticker_str = f"<b>{ticker}</b>" if ticker else "❓ 티커 미발견"
    price_str  = f"{price_on_date:,.0f}원" if price_on_date else "-"
    company_str = item["company"] or "(산업분석)"

    msg = (
        f"📋 <b>밸류파인더 신규 리포트</b>\n\n"
        f"종목: <b>{company_str}</b> ({ticker_str})\n"
        f"제목: {item['title'][:60]}\n"
        f"작성일: {item['report_date']} | 작성자: {item['author']}\n"
        f"작성일 가격: {price_str}\n"
        f"🔗 <a href=\"{item['url']}\">리포트 보기</a>"
    )
    send_telegram(msg)


# ── 일일 수익률 현황 ──────────────────────────────────────────────
def daily_performance_report(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT wr_id, company, ticker, report_date, price_on_date
        FROM reports
        WHERE ticker IS NOT NULL AND price_on_date IS NOT NULL
        ORDER BY report_date DESC
        LIMIT 30
    """).fetchall()

    if not rows:
        log.info("추적 중인 종목 없음 — 수익률 리포트 스킵")
        return

    today_str = date.today().strftime("%Y-%m-%d")
    lines = [f"📊 <b>밸류파인더 수익률 현황</b> ({today_str})\n"]

    for wr_id, company, ticker, report_date, base_price in rows:
        current = get_latest_price(ticker)
        if current is None:
            continue

        pct = (current - base_price) / base_price * 100
        emoji = "🟢" if pct >= 0 else "🔴"

        # 스냅샷 저장
        conn.execute("""
            INSERT OR IGNORE INTO price_snapshots (wr_id, snap_date, price, pct_change)
            VALUES (?, ?, ?, ?)
        """, (wr_id, today_str, current, pct))

        lines.append(
            f"{emoji} <b>{company}</b> ({ticker})\n"
            f"   {report_date} {base_price:,.0f}원 → {current:,.0f}원 "
            f"<b>{pct:+.1f}%</b>"
        )

    conn.commit()

    if len(lines) <= 1:
        log.info("수익률 계산된 종목 없음")
        return

    send_telegram("\n".join(lines))


# ── JSON Export ───────────────────────────────────────────────────
def export_json(conn: sqlite3.Connection):
    """DB에서 최신 price_snapshots 조인해서 data/reports.json 생성 후 git push"""
    today_str = date.today().strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT
            r.wr_id,
            r.company,
            r.ticker,
            r.title,
            r.author,
            r.report_date,
            r.url,
            r.price_on_date,
            ps.price   AS latest_price,
            ps.pct_change
        FROM reports r
        LEFT JOIN price_snapshots ps
            ON ps.wr_id = r.wr_id
            AND ps.snap_date = (
                SELECT MAX(snap_date)
                FROM price_snapshots
                WHERE wr_id = r.wr_id
            )
        WHERE r.ticker IS NOT NULL AND r.price_on_date IS NOT NULL
        ORDER BY r.report_date DESC
    """).fetchall()

    reports = []
    for wr_id, company, ticker, title, author, report_date, url, price_on_date, latest_price, pct_change in rows:
        if latest_price is None:
            latest_price = price_on_date
        if pct_change is None and price_on_date and latest_price:
            pct_change = (latest_price - price_on_date) / price_on_date * 100
        reports.append({
            "wr_id":         wr_id,
            "company":       company or "",
            "ticker":        ticker or "",
            "title":         title or "",
            "author":        author or "",
            "report_date":   report_date or "",
            "url":           url or "",
            "price_on_date": price_on_date,
            "latest_price":  latest_price,
            "pct_change":    round(pct_change, 2) if pct_change is not None else None,
        })

    data_dir = BASE_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    out_path = data_dir / "reports.json"

    payload = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "reports":    reports,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info("JSON export 완료: %s (%d건)", out_path, len(reports))

    # git push
    try:
        subprocess.run(
            ["git", "add", "data/reports.json"],
            cwd=BASE_DIR, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "🤖 data: update reports"],
            cwd=BASE_DIR, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=BASE_DIR, check=True, capture_output=True,
        )
        log.info("git push 완료")
    except subprocess.CalledProcessError as e:
        log.warning("git push 실패 (변경 없음이거나 에러): %s", e.stderr.decode() if e.stderr else e)


# ── 메인 ──────────────────────────────────────────────────────────
def main(report_only: bool = False):
    log.info("=== ValueFinder Tracker 시작 ===")
    conn = init_db()

    if not report_only:
        # 1) 크롤링
        items = fetch_board(pages=2)

        # 2) 신규 감지
        new_count = 0
        for item in items:
            exists = conn.execute(
                "SELECT 1 FROM reports WHERE wr_id = ?", (item["wr_id"],)
            ).fetchone()
            if not exists:
                log.info("신규 발견: %s [%s] (%s)", item["company"], item["title"][:30], item["report_date"])
                process_new_item(conn, item)
                new_count += 1

        log.info("신규 종목: %d건", new_count)

    # 3) 일일 수익률 리포트
    daily_performance_report(conn)

    # 4) JSON export
    export_json(conn)

    conn.close()
    log.info("=== 완료 ===")


if __name__ == "__main__":
    import sys
    report_only = "--report-only" in sys.argv
    main(report_only=report_only)
