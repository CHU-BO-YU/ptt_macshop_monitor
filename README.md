# PTT MacShop Monitor

自動監控 PTT MacShop 版上的二手 MacBook 販售貼文，並透過 Telegram Bot 即時推送符合條件的筆電資訊。

## 功能

- 每 5 分鐘自動抓取 PTT MacShop 最新販售貼文
- 智能過濾符合條件的 MacBook（記憶體、儲存空間、價格、處理器規格）
- 自動排除已售出貼文
- 透過 Telegram Bot 即時推送通知
- 避免重複通知同一貼文

## 過濾條件

| 項目 | 門檻 |
|------|------|
| RAM | >= 16 GB |
| SSD | >= 512 GB |
| MacBook Air | M3 或以上 |
| MacBook Pro | M1 Pro / Max / Ultra 或以上 |
| 價格 | <= NT$30,000（未標價也會通知） |
| 狀態 | 排除已售/sold；暫售、洽中加註後仍通知 |

## 安裝

### 1. 安裝 Python 相依套件

```bash
pip3 install requests beautifulsoup4
```

### 2. 設定環境變數

複製 `env.example` 為 `.env` 並填入你的 Telegram Bot 資訊：

```bash
cp env.example .env
# 編輯 .env 填入 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID
```

> 取得 Bot Token：與 [@BotFather](https://t.me/BotFather) 對話建立 bot。  
> 取得 Chat ID：傳訊息給 bot 後，訪問 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在 `message.chat.id` 找到數值。

### 3. 執行方式

**單次執行（測試用，不會記錄已看過的貼文）：**
```bash
python3 ptt_macshop_direct_notify.py --dry-run
```

**正式執行：**
```bash
python3 ptt_macshop_direct_notify.py
```

### 4. 設定定時執行（Cron）

```bash
# 編輯 crontab
crontab -e

# 加入以下行，每 5 分鐘執行一次
*/5 * * * * cd /path/to/ptt_macshop_monitor && /usr/bin/python3 ptt_macshop_direct_notify.py >> /var/log/ptt_macshop.log 2>&1
```

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `ptt_macshop_direct_notify.py` | 主程式，負責爬蟲、過濾、發送通知 |
| `ptt_macshop_monitor.py` | 舊版監控程式（供參考） |
| `env.example` | 環境變數範本 |
| `ptt_macshop_direct_notify.README.md` | 詳細使用說明 |

## 清除去重記錄

若想讓所有貼文重新被判定：

```bash
python3 ptt_macshop_direct_notify.py --dry-run --reset-seen
```

或手動刪除 SQLite 資料庫：

```bash
rm ptt_macshop_seen.db
```

## 授權

MIT License
