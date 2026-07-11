"""
瑞芳收容所志工隊 粉專新貼文偵測器
----------------------------------
用 mbasic.facebook.com（Facebook 的輕量版介面）抓取粉專最新內容，
跟上一次記錄的內容做比對，如果不一樣就透過 ntfy.sh 發送推播通知。

這支程式設計成由 GitHub Actions 排程執行，每次執行都是全新的環境，
所以「上一次的內容」是存在 state.json 這個檔案裡，並且執行完會
自動 commit 回 repo，讓下一次執行時可以讀到。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---- 設定區：把下面這個網址換成你要追蹤的粉專的 mbasic 版網址 ----
# 做法：在瀏覽器網址列輸入 https://mbasic.facebook.com/你的粉專名稱或ID
# 可以用環境變數 FB_PAGE_URL 覆蓋，不一定要改這裡
FB_PAGE_URL = os.environ.get(
    "FB_PAGE_URL",
    "https://mbasic.facebook.com/tspca.ruifang",  # <-- 請改成實際的粉專 mbasic 網址
)

# ntfy 的 topic 名稱，等於是你的「通知頻道密碼」，從 GitHub Secrets 讀取
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

STATE_FILE = Path("state.json")

HEADERS = {
    # 用手機版 User-Agent，mbasic 介面對手機瀏覽器比較友善
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


def fetch_latest_post_signature():
    """抓取頁面，回傳 (內容指紋 hash, 用於通知的文字預覽)"""
    resp = requests.get(FB_PAGE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    if "login" in resp.url.lower() or "登入" in resp.text[:500]:
        raise RuntimeError(
            "抓到的是登入頁面，代表 Facebook 擋下了這次請求（可能是雲端 IP 被限制）。"
        )

    soup = BeautifulSoup(resp.text, "html.parser")

    # mbasic 版面貼文文字通常會出現在多個 <div> 裡；
    # 這段選擇器未必永遠準確，Facebook 改版時可能要調整。
    candidate_divs = soup.find_all("div")
    text_blocks = [
        d.get_text(" ", strip=True)
        for d in candidate_divs
        if d.get_text(strip=True) and len(d.get_text(strip=True)) > 15
    ]

    if not text_blocks:
        # 保底方案：整頁前 2000 字，至少能偵測「頁面有變化」
        text_blocks = [soup.get_text(" ", strip=True)[:2000]]

    latest_text = text_blocks[0]
    signature = hashlib.sha256(latest_text.encode("utf-8")).hexdigest()
    return signature, latest_text[:200]


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
