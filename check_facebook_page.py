"""
瑞芳收容所志工隊 粉專新貼文偵測器
----------------------------------
依序嘗試兩種方式抓取粉專最新內容：
  1. Facebook Page Plugin（官方設計給外部網站嵌入公開粉專用的端點，
     對自動化流量通常比較友善，優先嘗試）
  2. mbasic.facebook.com（Facebook 的輕量版介面，備援方案）
跟上一次記錄的內容做比對，如果不一樣就透過 ntfy.sh 發送推播通知。

這支程式設計成由 GitHub Actions 排程執行，每次執行都是全新的環境，
所以「上一次的內容」是存在 state.json 這個檔案裡，並且執行完會
自動 commit 回 repo，讓下一次執行時可以讀到。

注意：直接抓取 Facebook 本來就有被判定為自動化流量而擋下的風險，
這支程式無法 100% 保證穩定運作，這是所有「免費爬 Facebook」方案的通病。
"""

import hashlib
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

# 用來判斷「這是志工招募文」的關鍵字，符合就會用招募專屬的標題格式
RECRUITMENT_KEYWORDS = ["志工", "招募"]

# ==== 想改通知的文字內容，就改下面這幾行 ====
NOTIFY_TITLE_RECRUITMENT = "🐾 瑞芳志工隊{month}招募公佈了！"   # {month} 會自動代入「7月」這種格式，抓不到月份就是空字串
NOTIFY_TITLE_GENERIC = "瑞芳志工隊粉專有新貼文"
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
            # 短的雜訊文字直接跳過；長文字裡偶爾出現這些字也不該整段丟掉
            continue
        candidates.append(text)
    return candidates


def _pick_best_post_text(candidates):
    """貼文本文通常是頁面裡最長的一段連續文字，所以取最長的當作最可能的貼文內容"""
    if not candidates:
        return ""
    return max(candidates, key=len)


def _extract_post_link(soup, base_url):
    """嘗試找出貼文的永久連結，讓通知可以直接點進去看原文"""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(marker in href for marker in ("/posts/", "/photos/", "story_fbid", "permalink.php")):
            return urllib.parse.urljoin(base_url, href)
    return base_url


def _try_page_plugin():
    """方案一：Facebook Page Plugin，官方給外部網站嵌入用的端點"""
    encoded = urllib.parse.quote(FB_PAGE_URL, safe="")
    plugin_url = (
        f"https://www.facebook.com/plugins/page.php?"
        f"href={encoded}&tabs=timeline&width=500&height=800"
    )
    resp = requests.get(plugin_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if _looks_like_login_wall(resp):
        raise RuntimeError("方案一（Page Plugin）也被導向登入頁面。")

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = _extract_candidates(soup)
    if not candidates:
        raise RuntimeError("方案一（Page Plugin）抓到的頁面沒有可用文字內容。")

    best_text = _pick_best_post_text(candidates)
    link = _extract_post_link(soup, "https://www.facebook.com")
    return best_text, link


def _try_mbasic():
    """方案二：mbasic 輕量版介面，當方案一失敗時的備援"""
    page_path = FB_PAGE_URL.rstrip("/").split("facebook.com/")[-1]
    mbasic_url = f"https://mbasic.facebook.com/{page_path}"
    resp = requests.get(mbasic_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if _looks_like_login_wall(resp):
        raise RuntimeError("方案二（mbasic）也被導向登入頁面。")

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = _extract_candidates(soup)
    if not candidates:
        candidates = [soup.get_text(" ", strip=True)[:2000]]

    best_text = _pick_best_post_text(candidates)
    link = _extract_post_link(soup, mbasic_url)
    return best_text, link


def fetch_latest_post():
    """依序嘗試各種抓取方式，回傳 (內容指紋 hash, 貼文文字, 貼文連結)"""
    errors = []
    for attempt_name, attempt_fn in (("Page Plugin", _try_page_plugin), ("mbasic", _try_mbasic)):
        try:
            text, link = attempt_fn()
            print(f"[{attempt_name}] 抓取成功")
            signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return signature, text, link
        except Exception as e:
            print(f"[{attempt_name}] 失敗：{e}", file=sys.stderr)
            errors.append(f"{attempt_name}: {e}")

    raise RuntimeError(
        "所有抓取方式都失敗了，Facebook 目前擋下了雲端 IP 的請求。詳細：" + " | ".join(errors)
    )


# ==================== 內容解析 ====================

def parse_recruitment_info(text):
    """判斷是不是志工招募文，並且盡量抓出月份，例如貼文開頭常見的 [7月]"""
    is_recruitment = any(kw in text for kw in RECRUITMENT_KEYWORDS)

    month_match = re.search(r"\[?\s*(\d{1,2})\s*月\s*\]?", text)
    month_label = f"{month_match.group(1)}月" if month_match else ""

    return is_recruitment, month_label


# ==================== 狀態存取 ====================

def load_last_signature():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("last_signature")
        except json.JSONDecodeError:
            return None
    return None


def save_signature(signature):
    STATE_FILE.write_text(json.dumps({"last_signature": signature}, ensure_ascii=False))


# ==================== 發送通知 ====================

def send_notification(text, link):
    is_recruitment, month_label = parse_recruitment_info(text)

    if is_recruitment:
        title = NOTIFY_TITLE_RECRUITMENT.format(month=month_label)
    else:
        title = NOTIFY_TITLE_GENERIC

    preview = text[:300]  # 通知內文只放前 300 字，太長手機也顯示不完

    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=preview.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": NOTIFY_PRIORITY,
            "Tags": NOTIFY_TAGS,
            "Click": link,  # 點通知會直接開啟這個連結
        },
        timeout=10,
    )


# ==================== 主流程 ====================

def main():
    try:
        signature, text, link = fetch_latest_post()
    except Exception as e:
        print(f"抓取失敗：{e}", file=sys.stderr)
        sys.exit(1)

    last_signature = load_last_signature()

    if last_signature is None:
        print("第一次執行，先記錄目前狀態，不發通知（避免把舊貼文當新貼文）。")
        save_signature(signature)
        return

    if signature != last_signature:
        print("偵測到內容變化，發送通知！")
        send_notification(text, link)
        save_signature(signature)
    else:
        print("沒有變化。")


if __name__ == "__main__":
    main()
