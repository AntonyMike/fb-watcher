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
import sys
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---- 設定區：把下面這個網址換成你要追蹤的粉專的完整網址 ----
# 例如 https://www.facebook.com/tspca.ruifang
# 可以用環境變數 FB_PAGE_URL 覆蓋，不一定要改這裡
FB_PAGE_URL = os.environ.get(
    "FB_PAGE_URL",
    "https://www.facebook.com/Ruifang.Volunteers",  # <-- 請改成實際的粉專網址
)

# ntfy 的 topic 名稱，等於是你的「通知頻道密碼」，從 GitHub Secrets 讀取
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

STATE_FILE = Path("state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
}


def _looks_like_login_wall(resp):
    return "login" in resp.url.lower() or "登入" in resp.text[:800]


def _extract_text_blocks(soup):
    candidate_divs = soup.find_all("div")
    return [
        d.get_text(" ", strip=True)
        for d in candidate_divs
        if d.get_text(strip=True) and len(d.get_text(strip=True)) > 15
    ]


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
    text_blocks = _extract_text_blocks(soup)
    if not text_blocks:
        raise RuntimeError("方案一（Page Plugin）抓到的頁面沒有可用文字內容。")
    return text_blocks[0]


def _try_mbasic():
    """方案二：mbasic 輕量版介面，當方案一失敗時的備援"""
    page_path = FB_PAGE_URL.rstrip("/").split("facebook.com/")[-1]
    mbasic_url = f"https://mbasic.facebook.com/{page_path}"
    resp = requests.get(mbasic_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if _looks_like_login_wall(resp):
        raise RuntimeError("方案二（mbasic）也被導向登入頁面。")

    soup = BeautifulSoup(resp.text, "html.parser")
    text_blocks = _extract_text_blocks(soup)
    if not text_blocks:
        text_blocks = [soup.get_text(" ", strip=True)[:2000]]
    return text_blocks[0]


def fetch_latest_post_signature():
    """依序嘗試各種抓取方式，回傳 (內容指紋 hash, 用於通知的文字預覽)"""
    errors = []
    for attempt_name, attempt_fn in (("Page Plugin", _try_page_plugin), ("mbasic", _try_mbasic)):
        try:
            latest_text = attempt_fn()
            print(f"[{attempt_name}] 抓取成功")
            signature = hashlib.sha256(latest_text.encode("utf-8")).hexdigest()
            return signature, latest_text[:200]
        except Exception as e:
            print(f"[{attempt_name}] 失敗：{e}", file=sys.stderr)
            errors.append(f"{attempt_name}: {e}")

    raise RuntimeError(
        "所有抓取方式都失敗了，Facebook 目前擋下了雲端 IP 的請求。詳細：" + " | ".join(errors)
    )


def load_last_signature():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("last_signature")
        except json.JSONDecodeError:
            return None
    return None


def save_signature(signature):
    STATE_FILE.write_text(json.dumps({"last_signature": signature}, ensure_ascii=False))


def send_notification(preview_text):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=preview_text.encode("utf-8"),
        headers={
            "Title": "瑞芳志工隊粉專可能有新貼文".encode("utf-8"),
            "Priority": "high",
            "Tags": "dog,bell",
        },
        timeout=10,
    )


def main():
    try:
        signature, preview_text = fetch_latest_post_signature()
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
        send_notification(preview_text)
        save_signature(signature)
    else:
        print("沒有變化。")


if __name__ == "__main__":
    main()
