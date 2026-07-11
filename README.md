# 瑞芳收容所志工隊 粉專新貼文通知器

完全免費、永久有效的自動通知系統。原理：GitHub Actions 每 30 分鐘幫你檢查一次粉專，
發現內容改變就透過 ntfy.sh 推播到你的 iPhone。全程不需要付任何訂閱費。

## 需要準備的帳號（都免費）

1. GitHub 帳號 — https://github.com/join
2. ntfy 不需要註冊帳號，只需要 App Store 下載「ntfy」這個 App

## 設定步驟

### 1. 建立 GitHub Repository

1. 登入 GitHub，右上角 `+` → New repository
2. 名稱隨意，例如 `fb-watcher`
3. 選 **Private**（避免你的 topic 名稱被搜尋到）
4. Create repository

### 2. 上傳這三個檔案

把這個資料夾裡的 `check_facebook_page.py`、`.github/workflows/check.yml`
上傳到剛剛建立的 repo（維持一樣的資料夾結構，`check.yml` 一定要放在
`.github/workflows/` 底下）。

最簡單的方式：在 repo 頁面點 "Add file → Upload files"，把整個資料夾拖進去。

### 3. 修改粉專網址

打開 `check_facebook_page.py`，找到這一行：

```python
FB_PAGE_URL = os.environ.get(
    "FB_PAGE_URL",
    "[https://mbasic.facebook.com/tspca.ruifang](https://www.facebook.com/Ruifang.Volunteers)",  # <-- 請改成實際的粉專 mbasic 網址
)
```

把網址換成你要追蹤的粉專的 mbasic 版網址。取得方式：

1. 用瀏覽器打開該粉專的一般網址，複製網址列裡 `facebook.com/` 後面那段（可能是英數字代號，也可能是頁面 ID）
2. 組成 `https://mbasic.facebook.com/那段代號`
3. 用瀏覽器打開這個 mbasic 網址確認可以正常看到貼文內容（不用登入）

### 4. 設定 ntfy 通知頻道（你的專屬密碼）

隨便想一組**很長、很獨特、別人猜不到**的英數字字串，例如：
`ruifang-tspca-notify-8f3k29xz`

這組字串就是你的通知頻道名稱，等一下要設定兩個地方都用同一組。

### 5. 把頻道名稱加進 GitHub Secrets

1. 到你的 repo → Settings → Secrets and variables → Actions
2. New repository secret
3. Name 填 `NTFY_TOPIC`
4. Secret 填你剛剛想的那組字串
5. Add secret

### 6. 打開 Actions 的寫入權限

1. repo → Settings → Actions → General
2. 拉到最下面 "Workflow permissions"
3. 選 **Read and write permissions**
4. Save

### 7. iPhone 訂閱通知

1. App Store 下載「**ntfy**」（開發商 Anthony Ferrara，免費、開源）
2. 打開 App，點 `+` 新增訂閱
3. 貼上跟 GitHub Secret 一樣的那組字串（例如 `ruifang-tspca-notify-8f3k29xz`）
4. Subscribe

### 8. 手動測試一次

1. 回到 GitHub repo → Actions 分頁
2. 左側選 "Check Facebook page for new posts"
3. 右邊 "Run workflow" → Run workflow
4. 等大約 30 秒到 1 分鐘，點進這次執行紀錄看有沒有成功（綠色勾勾）
5. 第一次執行只會記錄狀態、不會發通知，這是正常的
6. 手動改一下 `state.json` 內容（隨便打亂幾個字）後再 Run 一次，
   應該就會收到 iPhone 推播測試訊息

## 常見問題

**執行失敗，錯誤說抓到登入頁面？**
代表 Facebook 認出這是自動化流量並擋下來了。可以嘗試：
- 把排程間隔拉長（例如 1-2 小時一次），降低被判定為機器人的機率
- 如果持續失敗，這種「爬蟲」方式本來就有被 Facebook 封鎖的風險，
  屆時可能要改成在自己家裡的電腦/樹莓派上跑（用家用網路的 IP，
  比雲端機房的 IP 更不容易被擋），或退回用付費的第三方服務。

**想調整檢查頻率？**
修改 `.github/workflows/check.yml` 裡的：
```yaml
- cron: "*/30 * * * *"
```
例如改成 `0 * * * *` 就是每小時整點檢查一次。

**GitHub Actions 會收費嗎？**
Private repo 每月有 2000 分鐘免費額度，這支程式每次執行大約幾秒到十幾秒，
就算每 30 分鐘跑一次，一個月也才用掉幾十分鐘，完全在免費額度內，永久免費。
