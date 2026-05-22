# ptt_macshop_direct_notify

不依賴 Hermes 的獨立 PTT MacShop MacBook 監控通知腳本。  
每 5 分鐘（透過 systemd timer）抓取 PTT MacShop macbook 搜尋結果，過濾符合規格的二手 MacBook，透過 Telegram Bot 發送通知。

## 過濾條件

| 項目 | 門檻 |
|------|------|
| RAM | ≥ 16 GB |
| SSD | ≥ 512 GB |
| MacBook Air | M3 或以上 |
| MacBook Pro | M1 Pro / Max / Ultra 或以上 |
| 價格 | ≤ NT$30,000（未標價也會通知） |
| 狀態 | 排除已售/sold；暫售、洽中加註後仍通知 |

## 安裝

### 1. 安裝 Python 相依套件

```bash
pip3 install requests beautifulsoup4
# 或使用 --user
pip3 install --user requests beautifulsoup4
```

### 2. 設定環境變數

建立 `~/.config/ptt_macshop_notify.env`：

```ini
TELEGRAM_BOT_TOKEN=1234567890:ABCDEFGabcdefg...
TELEGRAM_CHAT_ID=123456789
```

> 取得 Bot Token：與 @BotFather 對話建立 bot。  
> 取得 Chat ID：傳訊息給 bot 後，訪問 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在 `message.chat.id` 找到數值。

```bash
chmod 600 ~/.config/ptt_macshop_notify.env
```

### 3. 安裝 systemd unit 檔

```bash
mkdir -p ~/.config/systemd/user/
cp ~/.hermes/scripts/ptt_macshop_direct_notify.service ~/.config/systemd/user/
cp ~/.hermes/scripts/ptt_macshop_direct_notify.timer  ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now ptt_macshop_direct_notify.timer
```

### 4. 驗證 timer 已啟用

```bash
systemctl --user list-timers ptt_macshop_direct_notify.timer
```

## 測試方式

### dry-run（不發 Telegram，看會通知哪些內容）

```bash
python3 ~/.hermes/scripts/ptt_macshop_direct_notify.py --dry-run
```

### 手動觸發 systemd service（真實發送）

```bash
systemctl --user start ptt_macshop_direct_notify.service
journalctl --user -u ptt_macshop_direct_notify.service -f
```

### 清除去重記錄（讓所有貼文重新被判斷）

```bash
# 方法 A：使用內建參數
python3 ~/.hermes/scripts/ptt_macshop_direct_notify.py --dry-run --reset-seen

# 方法 B：直接刪資料庫
rm ~/.hermes/data/ptt_macshop_seen.db
```

## 資料路徑

| 檔案 | 用途 |
|------|------|
| `~/.hermes/data/ptt_macshop_seen.db` | SQLite 去重資料庫 |
| `~/.config/ptt_macshop_notify.env` | Telegram 環境變數 |

## 通知格式範例

```
今天新增符合條件：2 台
1. MacBook Air M3 16GB/512GB  NT$28,000
   連結: https://www.ptt.cc/bbs/MacShop/M.1234567890.A.123.html
2. MacBook Pro M1 PRO 16GB/1024GB  NT$26,500 [洽中]
   連結: https://www.ptt.cc/bbs/MacShop/M.9876543210.A.456.html
```
