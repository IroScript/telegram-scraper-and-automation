# Telegram Scraper - Standalone Python Script

This is a standalone Python version of `ergoncugler/web-scraping-telegram`, converted from the Jupyter notebook to a regular `.py` file. No Jupyter or Google Colab needed.

## 🚀 Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Get your Telegram API credentials

Go to https://my.telegram.org/apps and create an app to get:
- `API_ID` (a number)
- `API_HASH` (a string)

### 3. Run the scraper

**Option A: Command-line (recommended)**
```bash
python telegram_scraper.py \
  --api-id YOUR_API_ID \
  --api-hash YOUR_API_HASH \
  --phone +YOUR_PHONE \
  --channels "@channel1,@channel2,https://t.me/channel3" \
  --date-min 2024-01-01 \
  --date-max 2024-12-31 \
  --output-format excel
```

**Option B: Config file**
1. Copy `config_example.py` to `config.py`
2. Edit with your credentials and channels
3. Run:
```bash
python -c "from config import config; from telegram_scraper import TelegramScraper; import asyncio; asyncio.run(TelegramScraper(config).run())"
```

## 📊 What You Get

The scraper extracts from each message:

| Field | Description |
|-------|-------------|
| `Author ID` | Numeric Telegram user ID of the sender |
| `Content` | Full message text |
| `Date` | Message timestamp |
| `Message ID` | Unique message identifier |
| `Author` | Username (if available) |
| `Views` | View count |
| `Reactions` | Reaction emojis and counts |
| `Shares` | Forward count |
| `Media` | Whether message contains media |
| `Comments List` | JSON with replies/comments |
| `Classification` | Auto-labeled as "seller", "buyer", or "other" |

## 🏷️ Automatic Seller/Buyer Classification

The script automatically classifies messages based on keywords:
- **Seller**: Contains terms like "sell", "wts", "for sale", "price", "stock available"
- **Buyer**: Contains terms like "buy", "wtb", "looking for", "need", "seeking"
- **Other**: No clear signal

You can customize these keywords in `config_example.py` or by editing `ScraperConfig` directly.

## 📁 Output Files

All output goes to `./output/` directory:
- `FINAL_*.xlsx` or `FINAL_*.parquet` - complete scrape results
- `complete_*.xlsx` - per-channel files
- `backup_*.xlsx` - periodic backups every 1000 messages

## ⚠️ Important Notes

1. **You must be a member** of the channels/groups you want to scrape
2. **Telegram ToS**: This tool is for research/legitimate purposes only
3. **Rate limits**: The script handles FloodWait errors automatically (waits then continues)
4. **Session file**: First run will prompt for OTP verification - subsequent runs are automatic
5. **Legal**: Comply with Telegram ToS and local data privacy regulations

## 🔧 Advanced Usage

### Filter by keyword during scraping
```bash
python telegram_scraper.py --keyword "sell" --channels "@marketplace"
```

### Use Parquet output (faster, better for large datasets)
```bash
python telegram_scraper.py --output-format parquet
```

### Limit messages per channel
```bash
python telegram_scraper.py --max-messages 50000
```

## 📝 Original Repository

Based on https://github.com/ergoncugler/web-scraping-telegram by Ergon Cugler de Moraes Silva
License: Free to use and modify. Responsibility for use lies with the user.
