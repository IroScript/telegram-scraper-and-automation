#!/usr/bin/env python3
"""
Runner script — handles OTP code non-interactively,
then launches the scraper.
"""

import asyncio
from telegram_scraper import check_credentials_and_open_drive, TelegramScraper
check_credentials_and_open_drive()
from config import config

OTP_CODE = "83423"

async def main():
    scraper = TelegramScraper(config)

    print("=" * 60)
    print('TELEGRAM SCRAPER - GV1212 Buyers (Last 3 Days)')
    print("=" * 60)
    print(f"Channel: {config.CHANNELS}")
    print(f"Date: {config.DATE_MIN.date()} to {config.DATE_MAX.date()}")
    print(f"Buyer keywords: {len(config.BUYER_KEYWORDS)} terms")
    print(f"Output: {config.OUTPUT_FORMAT.upper()} in {config.OUTPUT_DIR}/")
    print(f"Max messages: {config.MAX_MESSAGES:,}")
    print("=" * 60)

    # Connect with pre-filled OTP
    from telethon.sync import TelegramClient
    scraper.client = TelegramClient(
        config.USERNAME or "session",
        config.API_ID,
        config.API_HASH
    )
    await scraper.client.start(phone=config.PHONE, code_callback=lambda: OTP_CODE)
    print("Connected to Telegram with OTP!")

    try:
        # Run the scraping loop (skip connect since we already connected)
        for channel in config.CHANNELS:
            if scraper.total_processed >= config.MAX_MESSAGES:
                break
            if __import__('time').time() - scraper.start_time > config.TIMEOUT_SECONDS:
                break
            await scraper.scrape_channel(channel.strip())
    finally:
        scraper._save_final()
        await scraper.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
