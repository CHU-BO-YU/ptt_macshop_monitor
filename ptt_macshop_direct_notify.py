#!/usr/bin/env python3
"""
PTT MacShop 直接通知腳本
不依賴 Hermes，每次執行抓取 PTT MacShop macbook 搜尋結果，
過濾符合條件的貼文，透過 Telegram Bot 發送通知。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ── 設定 ──────────────────────────────────────────────────────────────────────
PTT_BASE = "https://www.ptt.cc"
SEARCH_URL = "https://www.ptt.cc/bbs/MacShop/search?q=macbook"
SEARCH_PAGES = 5
DB_PATH = os.path.expanduser("~/.hermes/data/ptt_macshop_seen.db")
TELEGRAM_SEND_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_EDIT_API = "https://api.telegram.org/bot{token}/editMessageText"
STATE_PATH = os.path.expanduser("~/.hermes/state/ptt_macshop_direct_notify_state.json")

FILTER = {
    "min_ram_gb": 16,
    "min_ssd_gb": 512,
    "max_price": 30000,
    # Air 需 M3+，Pro 需 M1 Pro+
}

REQUEST_TIMEOUT = 15
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Cookie": "over18=1",
}
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 1.2


# ── SQLite 去重 ───────────────────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_posts "
        "(post_id TEXT PRIMARY KEY, notified_at TEXT)"
    )
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, post_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_posts WHERE post_id = ?", (post_id,)
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, post_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_posts (post_id, notified_at) VALUES (?, ?)",
        (post_id, datetime.utcnow().isoformat()),
    )
    conn.commit()


# ── PTT 抓取 ─────────────────────────────────────────────────────────────────

def fetch_search_page(url: str) -> tuple[list[dict], str | None]:
    """抓取搜尋結果頁，回傳 (posts, 下一頁url)。"""
    resp = None
    for i in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if i == MAX_RETRIES - 1:
                print(f"[ERROR] fetch search page failed: {e}", file=sys.stderr)
                return [], None
            time.sleep(RETRY_BACKOFF_SEC * (i + 1))

    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []
    for div in soup.select("div.r-ent"):
        a_tag = div.select_one("div.title a")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        # post_id = 最後路徑段，如 M.1234567890.A.123
        post_id = href.rstrip("/").split("/")[-1].replace(".html", "")
        date_tag = div.select_one("div.date")
        posts.append({
            "post_id": post_id,
            "title": a_tag.get_text(strip=True),
            "url": urljoin(PTT_BASE, href),
            "date": date_tag.get_text(strip=True) if date_tag else "",
        })

    next_url = None
    next_link = soup.select_one('a.btn.wide:contains("‹ 上頁")')
    if next_link and next_link.get("href"):
        next_url = urljoin(PTT_BASE, next_link.get("href"))

    return posts, next_url


def fetch_post_body(url: str) -> tuple[str, str]:
    """抓取單篇貼文；回傳 (原始內文, 推文文字)。失敗回傳 ("", "")。"""
    resp = None
    for i in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if i == MAX_RETRIES - 1:
                print(f"[WARN] fetch post body failed ({url}): {e}", file=sys.stderr)
                return "", ""
            time.sleep(RETRY_BACKOFF_SEC * (i + 1))
    soup = BeautifulSoup(resp.text, "html.parser")
    content_div = soup.select_one("div#main-content")
    if not content_div:
        return "", ""

    push_texts = []
    for tag in content_div.select("div.push"):
        push_texts.append(tag.get_text(" ", strip=True))
        tag.decompose()  # 移除推文，只保留原始內文做規格解析

    return content_div.get_text(), "\n".join(push_texts)


# ── 規格解析 ──────────────────────────────────────────────────────────────────

_RAM_RE = re.compile(r"(\d+)\s*[Gg][Bb]?\s*(?:ram|記憶體|unified memory)?", re.I)
_SSD_RE = re.compile(r"(\d+)\s*[Gg][Bb]?\s*(?:ssd|固態|storage)?", re.I)
_TB_RE  = re.compile(r"(\d+(?:\.\d+)?)\s*[Tt][Bb]?\s*(?:ssd|固態|storage)?", re.I)
_RAM_SSD_SLASH_RE = re.compile(r"\b(8|16|24|32|48|64|96|128|192)\s*(?:G|GB)?\s*/\s*(256|512|1024|2048|4096)\s*(?:G|GB)?\b", re.I)
_PRICE_RE = re.compile(r"(?:售價|price|NT\$?|台幣|ntd)?\s*(\d[\d,]+)", re.I)

# 晶片辨識
_CHIP_MAP = {
    # Air 系列（需 M3+）
    "m4": ("air", 4), "m3": ("air", 3),
    # Pro/Max/Ultra 系列（需 M1 Pro+）
    "m4 pro": ("pro", 41), "m3 pro": ("pro", 31), "m2 pro": ("pro", 21),
    "m1 pro": ("pro", 11), "m4 max": ("pro", 42), "m3 max": ("pro", 32),
    "m2 max": ("pro", 22), "m1 max": ("pro", 12), "m4 ultra": ("pro", 43),
    "m3 ultra": ("pro", 33), "m2 ultra": ("pro", 23), "m1 ultra": ("pro", 13),
    # 舊晶片
    "m2": ("air", 2), "m1": ("air", 1),
    "intel": ("intel", 0),
}


def parse_ram(text: str) -> int | None:
    """從文字擷取最大 RAM GB 數值。"""
    values = [int(m.group(1)) for m in _RAM_RE.finditer(text)
              if int(m.group(1)) in (8, 16, 24, 32, 48, 64, 96, 128, 192)]
    # 支援 16/512、16G/512G 這種常見寫法
    values += [int(m.group(1)) for m in _RAM_SSD_SLASH_RE.finditer(text)]
    return max(values) if values else None


def parse_ssd(text: str) -> int | None:
    """從文字擷取最大 SSD GB（TB 轉換為 GB）。"""
    gb_vals = [int(m.group(1)) for m in _SSD_RE.finditer(text)
               if int(m.group(1)) >= 128]
    tb_vals = [int(float(m.group(1)) * 1024) for m in _TB_RE.finditer(text)]
    # 支援 16/512、16G/512G 這種常見寫法
    slash_vals = [int(m.group(2)) for m in _RAM_SSD_SLASH_RE.finditer(text)]
    all_vals = gb_vals + tb_vals + slash_vals
    return max(all_vals) if all_vals else None


def parse_price(text: str) -> int | None:
    """從文字擷取最低合理價格（避免規格數字被誤判）。"""
    candidates = []
    for m in _PRICE_RE.finditer(text):
        val = int(m.group(1).replace(",", ""))
        # 合理 Mac 二手價：5000 ~ 100000
        if 5000 <= val <= 100_000:
            candidates.append(val)
    return min(candidates) if candidates else None


def detect_chip(text: str) -> tuple[str, int] | None:
    """回傳 (category, rank)；category: 'air'|'pro'|'intel'。"""
    lower = text.lower()
    # 先嘗試長字串（M1 Pro 優先於 M1）
    for chip, info in sorted(_CHIP_MAP.items(), key=lambda x: -len(x[0])):
        if chip in lower:
            return info
    return None


def detect_status(title: str, body: str, push_text: str = "") -> str:
    """
    回傳狀態字串：'sold' | 'temp_sold' | 'negotiating' | 'available'
    """
    combined = (title + " " + body + " " + push_text).lower()
    if re.search(r"已售|售出|sold out|\bsold\b", combined):
        return "sold"
    if re.search(r"暫售|暫時售出", combined):
        return "temp_sold"
    if re.search(r"洽中|洽談中", combined):
        return "negotiating"
    return "available"


def is_macbook(title: str) -> bool:
    return bool(re.search(r"macbook|mac\s*book", title, re.I))


# ── 過濾邏輯 ──────────────────────────────────────────────────────────────────

def passes_filter(title: str, body: str, push_text: str = "") -> dict | None:
    """
    通過所有過濾條件時回傳摘要 dict，否則回傳 None。
    摘要包含 ram, ssd, price, chip, status, model_label。
    """
    combined = title + "\n" + body

    # 只收 [販售]，明確排除徵求/收購/交換
    if not re.search(r"^\s*\[販售\]", title, re.I):
        return None
    if re.search(r"\[(徵求|收購|交換)\]", title):
        return None

    status = detect_status(title, body, push_text)
    if status == "sold":
        return None

    if not is_macbook(title):
        return None

    chip_info = detect_chip(combined)
    if chip_info is None:
        # 無法辨識晶片，跳過
        return None
    category, rank = chip_info

    # Intel 直接略過
    if category == "intel":
        return None

    # Air 需 M3（rank 3）以上
    if category == "air" and rank < 3:
        return None

    # Pro/Max/Ultra 系列：rank 11 = M1 Pro
    if category == "pro" and rank < 11:
        return None

    ram = parse_ram(combined)
    if ram is None or ram < FILTER["min_ram_gb"]:
        return None

    ssd = parse_ssd(combined)
    if ssd is None or ssd < FILTER["min_ssd_gb"]:
        return None

    price = parse_price(combined)
    if price is not None and price > FILTER["max_price"]:
        return None

    # 找晶片名稱標籤
    chip_label = next(
        (k.upper() for k, v in sorted(_CHIP_MAP.items(), key=lambda x: -len(x[0]))
         if k in combined.lower() and v == chip_info),
        "?"
    )
    price_str = f"NT${price:,}" if price else "價格未標"
    model_type = "MacBook Air" if category == "air" else "MacBook Pro"
    model_label = f"{model_type} {chip_label} {ram}GB/{ssd}GB"

    return {
        "model_label": model_label,
        "price_str": price_str,
        "price": price,
        "status": status,
        "ram": ram,
        "ssd": ssd,
    }


# ── Telegram 通知 ─────────────────────────────────────────────────────────────

def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[WARN] load state failed: {e}", file=sys.stderr)
    return {}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, int | None, str | None]:
    url = TELEGRAM_SEND_API.format(token=token)
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": ""},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            return False, None, str(data.get("description", "send failed"))
        msg_id = data.get("result", {}).get("message_id")
        return True, msg_id, None
    except requests.RequestException as e:
        return False, None, str(e)


def edit_telegram(token: str, chat_id: str, message_id: int, text: str) -> tuple[bool, str | None]:
    url = TELEGRAM_EDIT_API.format(token=token)
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        if data.get("ok"):
            return True, None
        return False, str(data.get("description", "edit failed"))
    except requests.RequestException as e:
        return False, str(e)


def build_message(matches: list[dict]) -> str:
    updated_at = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%H:%M")

    if not matches:
        return (
            "目前沒有適合購買的筆電\n"
            "------------\n"
            "共 0 台\n"
            "\n"
            f"更新時間 {updated_at}"
        )

    lines = ["搜尋到符合條件的筆電", "------------", f"共 {len(matches)} 台"]
    for i, m in enumerate(matches, 1):
        status_note = ""
        if m["status"] == "temp_sold":
            status_note = " [暫售]"
        elif m["status"] == "negotiating":
            status_note = " [洽中]"
        lines.append(f"{i}.{m['title']}{status_note}")
        lines.append(f"價格：{m.get('price_str', '價格未標')}")
        lines.append(m["url"])

    lines.append("")
    lines.append(f"更新時間 {updated_at}")
    return "\n".join(lines)


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="PTT MacShop 直接通知腳本")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="顯示會通知的內容但不發送 Telegram"
    )
    parser.add_argument(
        "--reset-seen", action="store_true",
        help="清空已通知去重資料（seen_posts）後再執行"
    )
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not args.dry_run and (not token or not chat_id):
        print(
            "[ERROR] 需設定環境變數 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID",
            file=sys.stderr,
        )
        return 1

    # 初始化 DB（放在網路請求之前，確保 DB 一定能開）
    try:
        conn = init_db(DB_PATH)
    except Exception as e:
        print(f"[ERROR] DB init failed: {e}", file=sys.stderr)
        return 1

    if args.reset_seen:
        try:
            conn.execute("DELETE FROM seen_posts")
            conn.commit()
            if args.dry_run:
                print("[INFO] seen_posts 已清空")
        except Exception as e:
            print(f"[ERROR] reset seen failed: {e}", file=sys.stderr)
            return 1

    all_posts = []
    page_url = SEARCH_URL
    for _ in range(SEARCH_PAGES):
        posts, next_url = fetch_search_page(page_url)
        if not posts:
            break
        all_posts.extend(posts)
        if not next_url:
            break
        page_url = next_url

    if not all_posts:
        return 0  # 靜默退出

    # 先依 post_id 去重（跨頁重複）
    uniq_posts = []
    seen_post_ids = set()
    for p in all_posts:
        if p["post_id"] in seen_post_ids:
            continue
        seen_post_ids.add(p["post_id"])
        uniq_posts.append(p)

    matches = []
    for post in uniq_posts:
        # 抓內文（加間隔避免被擋）
        body, push_text = fetch_post_body(post["url"])
        time.sleep(0.5)

        info = passes_filter(post["title"], body, push_text)
        if info is None:
            continue

        matches.append({**post, **info})

    # 連結去重（保留首次）
    dedup = []
    seen_urls = set()
    for m in matches:
        if m["url"] in seen_urls:
            continue
        seen_urls.add(m["url"])
        dedup.append(m)
    matches = dedup

    message = build_message(matches)

    if args.dry_run:
        print("=== DRY RUN ===")
        print(message)
        print("===============")
    else:
        state = load_state(STATE_PATH)

        last_message_id = state.get("message_id")
        if isinstance(last_message_id, int):
            ok, err = edit_telegram(token, chat_id, last_message_id, message)
            if ok:
                state["last_text"] = message
                save_state(STATE_PATH, state)
                return 0
            if err and "message is not modified" in err.lower():
                state["last_text"] = message
                save_state(STATE_PATH, state)
                return 0
            print(f"[WARN] edit failed, fallback to send: {err}", file=sys.stderr)

        # 沒有可編輯的舊訊息（或舊訊息已被刪除）時，直接補發新訊息
        ok, msg_id, err = send_telegram(token, chat_id, message)
        if not ok:
            print(f"[ERROR] Telegram send failed: {err}", file=sys.stderr)
            return 1
        state = {"message_id": msg_id, "last_text": message}
        save_state(STATE_PATH, state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
