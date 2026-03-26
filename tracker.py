#!/usr/bin/env python3
"""
ValueFinder Tracker (JSON-only, DB-free)
- valuefinder.co.kr 크롤링
- data/reports.json으로 상태 관리 (SQLite 없음)
- 신규 종목 감지 → 텔레그램 알림
- 매일 전체 업데이트: latest_price, pct_change, peak, trough
"""

import os, re, json, logging, requests, subprocess
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from pathlib import Path

SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

def _fetch_with_fallback(url: str, timeout: int = 30) -> requests.Response:
    """ScraperAPI 우선 (한국 IP). 키 없으면 직접 연결 시도."""
    if SCRAPER_API_KEY:
        scraper_url = (
            f"https://api.scraperapi.com/"
            f"?api_key={SCRAPER_API_KEY}"
            f"&url={requests.utils.quote(url, safe='')}"
            f"&country_code=kr"
        )
        r = requests.get(scraper_url, timeout=timeout)
        r.raise_for_status()
        return r

    # ScraperAPI 키 없을 때만 직접 연결 (로컬 실행용)
    log.info("ScraperAPI 키 없음 — 직접 연결 시도")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    return r

# ── 환경변수 로드 ─────────────────────────────────────────────────
def _load_env():
    # 1) .env.local (로컬 전용, git 제외)
    env_local = Path(__file__).parent / ".env.local"
    if env_local.exists():
        for line in env_local.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    # 2) ~/.zshrc fallback
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
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

BASE_DIR  = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "reports.json"
LOG_PATH  = BASE_DIR / "logs" / "tracker.log"

LOG_PATH.parent.mkdir(exist_ok=True)
DATA_FILE.parent.mkdir(exist_ok=True)

BOARD_URL = "https://valuefinder.co.kr/bbs/board.php?bo_table=report"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)


# ── JSON 로드/저장 ────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated_at": "", "reports": []}

def save_data(data: dict):
    data["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("JSON 저장: %d건", len(data["reports"]))


# ── 크롤링 ───────────────────────────────────────────────────────
def fetch_board(pages: int = 2) -> list[dict]:
    items = []
    wr_id_re = re.compile(r"wr_id=(\d+)")

    for page in range(1, pages + 1):
        url = BOARD_URL if page == 1 else f"{BOARD_URL}&page={page}"
        try:
            r = _fetch_with_fallback(url)
        except Exception as e:
            log.warning("fetch 실패 (page %d): %s — 크롤링 중단", page, e)
            break

        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 8:
                continue
            report_date = tds[0].get_text(strip=True)
            if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", report_date):
                continue
            company = tds[2].get_text(strip=True)
            a_tag = tds[3].find("a", href=wr_id_re)
            if not a_tag:
                continue
            m = wr_id_re.search(a_tag.get("href", ""))
            if not m:
                continue
            wr_id = int(m.group(1))
            raw_title = a_tag.get_text(strip=True)
            half = len(raw_title) // 2
            if raw_title[:half] == raw_title[half:]:
                raw_title = raw_title[:half]
            author = tds[4].get_text(strip=True)
            items.append({
                "wr_id": wr_id,
                "company": company,
                "title": raw_title,
                "author": author,
                "report_date": report_date,
                "url": f"https://valuefinder.co.kr/bbs/board.php?bo_table=report&wr_id={wr_id}",
            })

    seen, unique = set(), []
    for item in items:
        if item["wr_id"] not in seen:
            seen.add(item["wr_id"])
            unique.append(item)
    log.info("크롤링 완료: %d건", len(unique))
    return unique


# ── 티커 조회 (FinanceDataReader) ─────────────────────────────────
_fdr_df = None

def get_fdr_df():
    global _fdr_df
    if _fdr_df is not None:
        return _fdr_df
    try:
        import FinanceDataReader as fdr
        _fdr_df = fdr.StockListing("KRX")
        log.info("FDR KRX 로드: %d건", len(_fdr_df))
        return _fdr_df
    except Exception as e:
        log.warning("FDR 실패: %s", e)
        return None

def resolve_ticker(company: str) -> str | None:
    if not company:
        return None
    df = get_fdr_df()
    if df is None:
        return None
    exact = df[df["Name"] == company]
    if not exact.empty:
        return str(exact.iloc[0]["Code"])
    partial = df[df["Name"].str.contains(company, na=False, regex=False)]
    if not partial.empty:
        return str(partial.iloc[0]["Code"])
    for _, row in df.iterrows():
        if isinstance(row["Name"], str) and row["Name"] in company:
            return str(row["Code"])
    return None


# ── 주가 조회 (FinanceDataReader) ─────────────────────────────────
def get_ohlcv(ticker: str, from_date: str) -> object:
    """from_date: '2026.03.26' → 해당일부터 오늘까지 OHLCV DataFrame"""
    try:
        import FinanceDataReader as fdr
        start = from_date.replace(".", "-")
        end   = date.today().strftime("%Y-%m-%d")
        df = fdr.DataReader(ticker, start, end)
        return df if (df is not None and not df.empty) else None
    except Exception as e:
        log.warning("OHLCV 조회 실패 (%s): %s", ticker, e)
        return None

def calc_stats(df, base_price: float) -> dict:
    """OHLCV DataFrame + 기준가 → latest/peak/trough 계산"""
    latest_price = float(df.iloc[-1]["Close"])
    pct_change   = round((latest_price - base_price) / base_price * 100, 2)

    # 0 제거 (FDR 데이터 오류 방어)
    high_df = df[df["High"] > 0]
    low_df  = df[df["Low"]  > 0]

    peak_idx   = high_df["High"].idxmax()
    peak_price = float(high_df["High"].max())
    peak_date  = str(peak_idx.date()) if hasattr(peak_idx, "date") else str(peak_idx)[:10]
    peak_pct   = round((peak_price - base_price) / base_price * 100, 2)

    trough_idx   = low_df["Low"].idxmin()
    trough_price = float(low_df["Low"].min())
    trough_date  = str(trough_idx.date()) if hasattr(trough_idx, "date") else str(trough_idx)[:10]
    trough_pct   = round((trough_price - base_price) / base_price * 100, 2)

    return {
        "latest_price": latest_price,
        "pct_change":   pct_change,
        "peak_price":   peak_price,
        "peak_date":    peak_date,
        "peak_pct":     peak_pct,
        "trough_price": trough_price,
        "trough_date":  trough_date,
        "trough_pct":   trough_pct,
        "last_updated": date.today().isoformat(),
    }


# ── 텔레그램 ─────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN 없음 — 스킵")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error("텔레그램 실패: %s", e)


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    log.info("=== ValueFinder Tracker 시작 ===")

    data = load_data()
    reports_map  = {r["wr_id"]: r for r in data["reports"]}
    existing_ids = set(reports_map.keys())

    # 1) 신규 감지
    crawled   = fetch_board(pages=2)
    new_count = 0
    for item in crawled:
        if item["wr_id"] in existing_ids or not item["company"]:
            continue

        ticker = resolve_ticker(item["company"])
        df     = get_ohlcv(ticker, item["report_date"]) if ticker else None

        if df is not None and not df.empty:
            base   = float(df.iloc[0]["Close"])
            stats  = calc_stats(df, base)
        else:
            base, stats = None, {}

        log.info("신규: %s → %s, 작성일가: %s", item["company"], ticker, base)

        entry = {**item, "ticker": ticker, "price_on_date": base, **stats}
        # None 필드 기본값
        for key in ("latest_price","pct_change","peak_price","peak_date","peak_pct",
                    "trough_price","trough_date","trough_pct"):
            entry.setdefault(key, None)

        reports_map[item["wr_id"]] = entry
        existing_ids.add(item["wr_id"])
        new_count += 1

        ticker_str = f"<b>{ticker}</b>" if ticker else "❓ 티커 미발견"
        price_str  = f"{base:,.0f}원" if base else "-"
        send_telegram(
            f"📋 <b>밸류파인더 신규 리포트</b>\n\n"
            f"종목: <b>{item['company']}</b> ({ticker_str})\n"
            f"제목: {item['title'][:60]}\n"
            f"작성일: {item['report_date']} | 작성자: {item['author']}\n"
            f"작성일 가격: {price_str}\n"
            f"🔗 <a href=\"{item['url']}\">리포트 보기</a>"
        )

    log.info("신규 종목: %d건", new_count)

    # 2) 전체 주가 업데이트 (latest + peak + trough)
    updated = 0
    for wr_id, entry in reports_map.items():
        ticker = entry.get("ticker")
        base   = entry.get("price_on_date")
        if not ticker or not base:
            continue
        df = get_ohlcv(ticker, entry["report_date"])
        if df is None:
            continue
        stats = calc_stats(df, base)
        entry.update(stats)
        updated += 1

    log.info("주가 업데이트: %d건", updated)

    # 3) 저장 (report_date 내림차순)
    data["reports"] = sorted(reports_map.values(),
                             key=lambda r: r["report_date"], reverse=True)
    save_data(data)

    # 4) git push
    cwd = str(BASE_DIR)
    subprocess.run(["git", "add", "data/reports.json"], cwd=cwd)
    result = subprocess.run(
        ["git", "commit", "-m", f"🤖 data: update {date.today()}"],
        cwd=cwd, capture_output=True, text=True
    )
    if "nothing to commit" not in result.stdout + result.stderr:
        subprocess.run(["git", "push", "--force", "origin", "main"], cwd=cwd)
        log.info("git push 완료")
    else:
        log.info("변경사항 없음 — git push 스킵")

    log.info("=== 완료 ===")


if __name__ == "__main__":
    main()
