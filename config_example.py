#!/usr/bin/env python3
"""
Example configuration file for Telegram Scraper
Edit this file with your credentials and settings
"""

from telegram_scraper import ScraperConfig

# Create a config instance with your settings
config = ScraperConfig(
    # === REQUIRED: Telegram API credentials ===
    # Get these from https://my.telegram.org/apps
    API_ID=12345678,                    # Your API ID (number)
    API_HASH="abcdef1234567890abcdef1234567890",  # Your API Hash (string)
    PHONE="+1234567890",                # Your phone number with country code
    USERNAME="your_username",           # Your Telegram username (optional)

    # === CHANNELS TO SCRAPE ===
    # You must be a member of these channels/groups
    # Use @username format or https://t.me/username
    CHANNELS=[
        "@channel1_name",
        "@channel2_name",
        "https://t.me/channel3_name",
    ],

    # === DATE RANGE ===
    # Format: YYYY-MM-DD
    DATE_MIN="2024-01-01",
    DATE_MAX="2024-12-31",

    # === OPTIONAL FILTERS ===
    KEYWORD="",  # Leave empty to scrape all messages
    MAX_MESSAGES=100000,  # Max messages per channel

    # === OUTPUT SETTINGS ===
    OUTPUT_FORMAT="excel",  # "excel" or "parquet"
    OUTPUT_DIR="./output",
    FILE_PREFIX="my_telegram_data",

    # === SELLER/BUYER KEYWORDS ===
    # These are used to automatically classify messages
    # Edit these lists to match your use case
    SELLER_KEYWORDS=[
        "sell", "selling", "wts", "for sale", "stock available",
        "available", "price", "dm me", "shipping", "posting",
        "minimum order", "moq", "bulk", "wholesale", "retail",
        "offer", "deal", "discount", "promotion"
    ],
    BUYER_KEYWORDS=[
        "buy", "buying", "wtb", "looking for", "need", "want",
        "seeking", "searching", "interested", "inquiry", "quote",
        "pricing", "cost", "budget", "requirement", "specification"
    ]
)

# Export config for use
if __name__ == "__main__":
    print("Configuration loaded successfully!")
    print(f"Channels to scrape: {config.CHANNELS}")
    print(f"Date range: {config.DATE_MIN} to {config.DATE_MAX}")