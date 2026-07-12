"""
瑞芳收容所志工隊 粉專新貼文偵測器
----------------------------------
依序嘗試兩種方式抓取粉專內容：
  1. Facebook Page Plugin（官方設計給外部網站嵌入公開粉專用的端點）
  2. mbasic.facebook.com（Facebook 的輕量版介面，備援方案）

判斷條件（兩個都要符合才算「符合」）：
  1. 文字裡包含指定的固定文字：「TSPCA 瑞芳收容所志工服務」
  2. 文字裡的月份標記（例如 [7月]）符合「現在的月份」

發通知的規則很單純：**同一個月最多只發一次通知**。
做法是在 state.json 裡記錄「上次發過通知的年月」，例如 "2026-07"，
如果這次符合條件、但年月跟上次發過的一樣，就不會重複發送。
換月之後（例如變成 8月），符合條件時就會再發一次新的通知。

注意：直接抓取 Facebook 本來就有被判定為自動化流量而擋下的風險，
這支程式無法 100% 保證穩定運作，這是所有「免費爬 Facebook」方案的通病。
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ==================== 設定區 ====================

# 把下面這個網址換成你要追蹤的粉專的完整網址
FB_PAGE_URL = os.environ.get(
    "FB_PAGE_URL",
    "https://www.facebook.com/Ruifang.Volunteers",
)

# ntfy 的 topic 名稱，等於是你的「通知頻道密碼」，從 GitHub Secrets 讀取
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

# 必須完全包含這段文字，才會被當成「這個月的志工招募文」
REQUIRED_PHRASE = "TSPCA 瑞芳收容所志工服務"

# ==== 想改通知的文字內容，就改下面這幾行 ====
NOTIFY_TITLE = "🐾 瑞芳志工隊{month}招募公佈了！"
NOTIFY_TAGS = "dog,bell"
NOTIFY_PRIORITY = "high"
# ==============================================

STATE_FILE = Path("state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# 常見的網站介面文字（登入、隱私權等），用來過濾掉不是貼文內容的雜訊
JUNK_MARKERS = [
    "登入", "註冊", "忘記密碼", "隱私權", "使用條款", "Cookie",
    "繼續使用行動版網站", "建立粉絲專頁", "English (US)",
]


# ==================== 抓取邏輯 ====================

def _looks_like_login_wall(resp):
    return "login" in resp.url.lower() or "登入" in resp.text[:800]


def _extract_candidates(soup):
    """抓出頁面裡所有夠長的文字區塊，過濾掉常見的網站介面雜訊"""
    candidates = []
    for d in soup.find_all("div"):
        text = d.get_text(" ", strip=True)
        if len(text) <= 15:
            continue
        if any(marker in text for marker in JUNK_MARKERS) and len(text) < 60:
            continue
        candidates.append(text)
    return candidates


def _extract_post_link(soup, base_url):
    """嘗試找出貼文的永久連結，讓通知可以直接點進去看原文"""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(marker in href for marker in ("/posts/", "/photos/", "story_fbid", "permalink.php")):
            return urllib.parse.urljoin(base_url, href)
    return base_url


def _fetch_candidates_page_plugin():
    """方案一：Facebook Page Plugin，回傳 [(候選文字, 連結), ...]"""
    encoded = urllib.parse.quote(FB_PAGE_URL, safe="")
    plugin_url = (
        f"https://www.facebook.com/plugins/page.php?"
        f"href={encoded}&tabs=timeline&width=500&height=800"
    )
    resp = requests.get(plugin_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if _looks_like_login_wall(resp):
        raise RuntimeError("方案一（Page Plugin）被導向登入頁面。")

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = _extract_candidates(soup)
    link = _extract_post_link(soup, "https://www.facebook.com")
    return [(text, link) for text in candidates]


def _fetch_candidates_mbasic():
    """方案二：mbasic 輕量版介面，回傳 [(候選文字, 連結), ...]"""
    page_path = FB_PAGE_URL.rstrip("/").split("facebook.com/")[-1]
    mbasic_url = f"https://mbasic.facebook.com/{page_path}"
    resp = requests.get(mbasic_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if _looks_like_login_wall(resp):
        raise RuntimeError("方案二（mbasic）被導向登入頁面。")

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = _extract_candidates(soup)
    link = _extract_post_link(soup, mbasic_url)
    return [(text, link) for text in candidates]


def _matches_this_month_recruitment(text):
    """必須同時符合：包含指定文字、月份標記等於現在的月份"""
    if REQUIRED_PHRASE not in text:
        return False

    month_match = re.search(r"\[\s*(\d{1,2})\s*月\s*\]", text)
    if not month_match:
        return False

    current_month = datetime.datetime.now().month
    return int(month_match.group(1)) == current_month


def fetch_matching_post():
    """
    依序嘗試各種抓取方式，只要有任何候選同時符合『指定文字』和『當月月份』，
    就回傳 (text, link)；都不符合就回傳 None。
    只有在『所有抓取方式都失敗』時才會 raise Exception。
    """
    all_candidates = []
    fetch_errors = []
    any_success = False

    for attempt_name, attempt_fn in (
        ("Page Plugin", _fetch_candidates_page_plugin),
        ("mbasic", _fetch_candidates_mbasic),
    ):
        try:
            candidates = attempt_fn()
            print(f"[{attempt_name}] 抓取成功，取得 {len(candidates)} 個文字區塊")
            all_candidates.extend(candidates)
            any_success = True
        except Exception as e:
            print(f"[{attempt_name}] 失敗：{e}", file=sys.stderr)
            fetch_errors.append(f"{attempt_name}: {e}")

    if not any_success:
        raise RuntimeError(
            "所有抓取方式都失敗了，Facebook 目前擋下了雲端 IP 的請求。詳細：" + " | ".join(fetch_errors)
        )

    matches = [(t, l) for t, l in all_candidates if _matches_this_month_recruitment(t)]
    if not matches:
        return None
    return max(matches, key=lambda pair: len(pair[0]))


# ==================== 狀態存取（記錄「這個月通知過了沒」） ====================

def load_last_notified_month():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("last_notified")
        except json.JSONDecodeError:
            return None
    return None


def save_last_notified_month(year_month):
    STATE_FILE.write_text(json.dumps({"last_notified": year_month}, ensure_ascii=False))


# ==================== 發送通知 ====================

def send_notification(text, link):
    current_month = datetime.datetime.now().month
    title = NOTIFY_TITLE.format(month=f"{current_month}月")
    preview = text[:300]

    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=preview.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": NOTIFY_PRIORITY,
            "Tags": NOTIFY_TAGS,
            "Click": link,
        },
        timeout=10,
    )


# ==================== 主流程 ====================

def main():
    try:
        match = fetch_matching_post()
    except Exception as e:
        print(f"抓取失敗：{e}", file=sys.stderr)
        sys.exit(1)

    if match is None:
        print("這次沒有偵測到符合條件（本月 + 指定文字）的貼文，不發通知。")
        return

    now = datetime.datetime.now()
    current_year_month = f"{now.year}-{now.month:02d}"
    last_notified = load_last_notified_month()

    if last_notified == current_year_month:
        print(f"符合條件，但 {current_year_month} 已經通知過了，不重複發送。")
        return

    text, link = match
    print(f"偵測到符合條件的貼文，且 {current_year_month} 還沒通知過，發送通知！")
    send_notification(text, link)
    save_last_notified_month(current_year_month)


if __name__ == "__main__":
    main()
