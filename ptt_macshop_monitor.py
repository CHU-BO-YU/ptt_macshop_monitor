#!/usr/bin/env python3
import html
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

SEARCH_URL = "https://www.ptt.cc/bbs/MacShop/search?q=macbook"
BASE = "https://www.ptt.cc"
TRACKED_URL = "https://www.ptt.cc/bbs/MacShop/M.1777638035.A.23E.html"
STATE_PATH = Path.home() / ".hermes" / "state" / "ptt_macshop_macbook_seen.json"
MAX_TRACKED = 50
MAX_POSTS_TO_CHECK = 20
PRICE_LIMIT = 30000


def fetch_html(url: str) -> str:
    last_error = None
    for attempt in range(4):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cookie": "over18=1",
                },
            )
            with urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_error = exc
    raise last_error


def clean_text(raw: str) -> str:
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = raw.replace("\r", "")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n+", "\n", raw)
    return raw.strip()


def parse_posts(page_html: str):
    pattern = re.compile(
        r'<div class="title">\s*<a href="(?P<href>/bbs/MacShop/[^"#?]+\.html)"[^>]*>(?P<title>.*?)</a>',
        re.S,
    )
    posts = []
    seen = set()
    for match in pattern.finditer(page_html):
        href = match.group("href")
        article_id = href.rsplit("/", 1)[-1].replace(".html", "")
        if article_id in seen:
            continue
        seen.add(article_id)
        raw_title = html.unescape(re.sub(r"\s+", " ", match.group("title")).strip())
        posts.append({
            "id": article_id,
            "title": raw_title,
            "url": BASE + href,
        })
    return posts


def article_is_sold(text: str, title: str) -> bool:
    hay = f"{title}\n{text}"
    return any(token in hay for token in ["已售出", "<已售出>", "（已售）", "(已售)", "售出", "售出了"])


def extract_price(text: str):
    patterns = [
        r"\[售價\]\s*\$?\s*([0-9][0-9,]{3,})",
        r"售價[^0-9]{0,20}\$?\s*([0-9][0-9,]{3,})",
        r"\$\s*([0-9][0-9,]{3,})",
        r"([0-9][0-9,]{3,})\s*元",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def extract_storage(text: str):
    if re.search(r"1\s*tb|1t\b", text, re.I):
        return "1T"
    if re.search(r"(^|[^0-9])512(\s*(gb|g)\b|\b|\s*/)", text, re.I):
        return "512"
    if re.search(r"(^|[^0-9])256(\s*(gb|g)\b|\b|\s*/)", text, re.I):
        return "256"
    return None


def extract_memory(text: str):
    if re.search(r"16\s*(gb|g)\b", text, re.I):
        return "16"
    return None


def classify_machine(title: str, text: str):
    hay = f"{title}\n{text}"
    low = hay.lower()
    if "macbook pro" in low and "m3 max" in low:
        return "MacBook Pro M3 Max"
    if "macbook pro" in low and "m3 pro" in low:
        return "MacBook Pro M3 Pro"
    if "macbook pro" in low and re.search(r"macbook pro.*\bm3\b", low):
        return "MacBook Pro M3"
    if "macbook pro" in low and "m2 max" in low:
        return "MacBook Pro M2 Max"
    if "macbook pro" in low and "m2 pro" in low:
        return "MacBook Pro M2 Pro"
    if "macbook pro" in low and "m1 max" in low:
        return "MacBook Pro M1 Max"
    if "macbook pro" in low and "m1 pro" in low:
        return "MacBook Pro M1 Pro"
    if "macbook air" in low and "m5" in low:
        return "MacBook Air M5"
    if "macbook air" in low and "m4" in low:
        return "MacBook Air M4"
    if "macbook air" in low and "m3" in low:
        return "MacBook Air M3"
    return None


def candidate_allowed(label: str, memory: str, storage: str, price: int):
    if not label or memory != "16" or price is None or price > PRICE_LIMIT:
        return False
    if storage not in {"512", "1T"}:
        return False
    return label in {
        "MacBook Air M3",
        "MacBook Air M4",
        "MacBook Air M5",
        "MacBook Pro M1 Pro",
        "MacBook Pro M1 Max",
        "MacBook Pro M2 Pro",
        "MacBook Pro M2 Max",
        "MacBook Pro M3",
        "MacBook Pro M3 Pro",
        "MacBook Pro M3 Max",
    }


def parse_article(article_url: str):
    page_html = fetch_html(article_url)
    title_match = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
    title = html.unescape(title_match.group(1)).strip() if title_match else article_url
    text = clean_text(page_html)
    sold = article_is_sold(text, title)
    price = extract_price(text)
    memory = extract_memory(text)
    storage = extract_storage(text)
    label = classify_machine(title, text)
    return {
        "title": title,
        "text": text,
        "sold": sold,
        "price": price,
        "memory": memory,
        "storage": storage,
        "label": label,
    }


def build_candidate(post):
    details = parse_article(post["url"])
    if details["sold"]:
        return None
    if not candidate_allowed(details["label"], details["memory"], details["storage"], details["price"]):
        return None
    storage = details["storage"] or "?"
    display_price = f"{round(details['price'] / 1000)}k" if details["price"] % 1000 == 0 else str(details["price"])
    return {
        "title": post["title"],
        "url": post["url"],
        "label": details["label"],
        "memory": details["memory"],
        "storage": storage,
        "price": details["price"],
        "display": f"{details['label']} {details['memory']}/{storage} {display_price}",
    }


def load_state():
    if not STATE_PATH.exists():
        return {"seen_ids": [], "tracked_status": None, "candidate_signature": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"seen_ids": [], "tracked_status": None, "candidate_signature": []}


def save_state(seen_ids, tracked_status, candidate_signature):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "seen_ids": seen_ids[:MAX_TRACKED],
                "tracked_status": tracked_status,
                "candidate_signature": candidate_signature,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    search_html = fetch_html(SEARCH_URL)
    posts = parse_posts(search_html)
    current_ids = [p["id"] for p in posts]
    previous = load_state()
    previous_ids = previous.get("seen_ids", [])
    previous_tracked = previous.get("tracked_status")
    previous_signature = previous.get("candidate_signature", [])

    tracked_details = parse_article(TRACKED_URL)
    tracked_status = "sold" if tracked_details["sold"] else "available"

    viable = []
    for post in posts[:MAX_POSTS_TO_CHECK]:
        title_low = post["title"].lower()
        if "[販售]" not in post["title"]:
            continue
        if not any(token in title_low for token in ["macbook air", "macbook pro", "m1 pro", "m3", "m4", "m5"]):
            continue
        try:
            candidate = build_candidate(post)
        except Exception:
            continue
        if candidate:
            viable.append(candidate)

    signature = [f"{item['display']}|{item['url']}" for item in viable]
    new_posts = [p for p in posts if p["id"] not in set(previous_ids)] if previous_ids else []
    changed = {
        "new_posts": bool(new_posts),
        "tracked_changed": previous_tracked is not None and tracked_status != previous_tracked,
        "viable_changed": previous_signature != signature if previous_signature else False,
    }

    if not previous_ids:
        save_state(current_ids, tracked_status, signature)
        print(json.dumps({
            "status": "initialized",
            "message": "Baseline created.",
            "tracked_listing": {
                "display": "MacBook Pro M1 Pro 16/512 23k",
                "url": TRACKED_URL,
                "status": tracked_status,
            },
            "viable": viable,
            "latest_count": len(posts),
        }, ensure_ascii=False))
        return

    save_state(current_ids, tracked_status, signature)
    status = "changed" if any(changed.values()) else "no_change"
    print(json.dumps({
        "status": status,
        "changes": changed,
        "new_count": len(new_posts),
        "new_posts": new_posts,
        "tracked_listing": {
            "display": "MacBook Pro M1 Pro 16/512 23k",
            "url": TRACKED_URL,
            "status": tracked_status,
        },
        "viable": viable,
        "latest_count": len(posts),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
