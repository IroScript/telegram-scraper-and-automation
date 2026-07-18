#!/usr/bin/env python3
"""
Telegram Scraper GUI Application
Features:
- Add multiple channels/groups/links
- Keyword-based scraping OR date-range scraping
- Built-in buyer/client keywords (English + Chinese)
- Author ID extraction and deduplication
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

# Import scraper components
from telegram_scraper import ScraperConfig, TelegramScraper, remove_unsupported_characters, classify_message, check_credentials_and_open_drive
check_credentials_and_open_drive()
import pandas as pd

# Default timezone - Asia/Dhaka (UTC+6)
DEFAULT_TZ = timezone(timedelta(hours=6))


class TelegramScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Scraper - GUI")
        self.root.geometry("700x780")
        self.root.resizable(True, True)

        self.channels_list: List[str] = []
        self.scraping = False
        self.scraper = None

        # Load config from config.py
        self.load_config_from_file()

        self.setup_ui()

    def load_config_from_file(self):
        """Load configuration from config.py file"""
        try:
            from config import config
            self.config = config
        except Exception as e:
            messagebox.showerror("Config Error", f"Failed to load config: {e}")
            # Use defaults
            self.config = ScraperConfig()

    def setup_ui(self):
        # Title
        title_label = tk.Label(self.root, text="Telegram Scraper - Universal Extractor",
                            font=("Arial", 14, "bold"))
        title_label.pack(pady=10)

        # API Credentials Frame
        api_frame = ttk.LabelFrame(self.root, text="Telegram API Credentials")
        api_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(api_frame, text="API ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.api_id_var = tk.StringVar(value=str(self.config.API_ID))
        api_id_entry = tk.Entry(api_frame, textvariable=self.api_id_var, width=30)
        api_id_entry.grid(row=0, column=1, padx=5, pady=2)

        tk.Label(api_frame, text="API Hash:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.api_hash_var = tk.StringVar(value=self.config.API_HASH)
        api_hash_entry = tk.Entry(api_frame, textvariable=self.api_hash_var, width=50)
        api_hash_entry.grid(row=1, column=1, padx=5, pady=2)

        tk.Label(api_frame, text="Phone (+country code):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.phone_var = tk.StringVar(value=self.config.PHONE)
        phone_entry = tk.Entry(api_frame, textvariable=self.phone_var, width=30)
        phone_entry.grid(row=2, column=1, padx=5, pady=2, sticky="w")

        # Channels/Groups Frame
        channel_frame = ttk.LabelFrame(self.root, text="Channels / Groups / Links")
        channel_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Entry for adding channel
        entry_frame = tk.Frame(channel_frame)
        entry_frame.pack(fill="x", padx=5, pady=5)

        tk.Label(entry_frame, text="Channel/Group Link/ID:").pack(side="left")
        self.channel_entry_var = tk.StringVar()
        channel_entry = tk.Entry(entry_frame, textvariable=self.channel_entry_var, width=40)
        channel_entry.pack(side="left", padx=5)
        channel_entry.bind("<Return>", self.add_channel)

        # Buttons for adding
        btn_frame = tk.Frame(channel_frame)
        btn_frame.pack(fill="x", padx=5, pady=2)

        tk.Button(btn_frame, text="Add Group Link", command=self.add_link, bg="#4CAF50", fg="white").pack(side="left", padx=2)
        tk.Button(btn_frame, text="Add Group ID", command=self.add_id, bg="#2196F3", fg="white").pack(side="left", padx=2)
        tk.Button(btn_frame, text="Clear All", command=self.clear_channels, bg="#f44336", fg="white").pack(side="left", padx=2)

        # Listbox for channels
        self.channels_listbox = tk.Listbox(channel_frame, height=6)
        self.channels_listbox.pack(fill="both", expand=True, padx=5, pady=5)

        # Load existing channels
        for channel in self.config.CHANNELS:
            self.channels_list.append(channel)
            self.channels_listbox.insert(tk.END, channel)

        # Scraping Mode Frame
        mode_frame = ttk.LabelFrame(self.root, text="Scraping Mode")
        mode_frame.pack(fill="x", padx=10, pady=5)

        self.mode_var = tk.StringVar(value="keyword")

        tk.Radiobutton(mode_frame, text="Keyword-based Filtering (Comma-separated values)",
                      variable=self.mode_var, value="keyword", command=self.toggle_keywords_state).pack(anchor="w", padx=10, pady=2)
        tk.Radiobutton(mode_frame, text="All Chats Scraping (Date Range Only)",
                      variable=self.mode_var, value="all", command=self.toggle_keywords_state).pack(anchor="w", padx=10, pady=2)

        # Keywords Frame/Widget
        self.keywords_frame = tk.Frame(mode_frame)
        self.keywords_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Scrollable Text Area for keywords (height increased to 7 for more space)
        self.keywords_text = tk.Text(self.keywords_frame, height=7, width=60, wrap="word")
        self.keywords_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(self.keywords_frame, command=self.keywords_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.keywords_text.config(yscrollcommand=scrollbar.set)

        # Populate with default keywords from config
        default_kws = ", ".join(self.config.BUYER_KEYWORDS)
        self.keywords_text.insert(tk.END, default_kws)

        # Date Range Frame
        date_frame = ttk.LabelFrame(self.root, text="Date Range")
        date_frame.pack(fill="x", padx=10, pady=5)

        # Date and Time selection in the same row
        tk.Label(date_frame, text="Start:").grid(row=0, column=0, sticky="w", padx=5)
        self.date_min_var = tk.StringVar(value=self.config.DATE_MIN.strftime('%Y-%m-%d'))
        tk.Entry(date_frame, textvariable=self.date_min_var, width=12).grid(row=0, column=1, padx=5)
        self.time_start_var = tk.StringVar(value="00:00")
        tk.Entry(date_frame, textvariable=self.time_start_var, width=8).grid(row=0, column=2, padx=5)

        tk.Label(date_frame, text="End:").grid(row=0, column=3, sticky="w", padx=5)
        self.date_max_var = tk.StringVar(value=self.config.DATE_MAX.strftime('%Y-%m-%d'))
        tk.Entry(date_frame, textvariable=self.date_max_var, width=12).grid(row=0, column=4, padx=5)
        self.time_end_var = tk.StringVar(value="23:59")
        tk.Entry(date_frame, textvariable=self.time_end_var, width=8).grid(row=0, column=5, padx=5)

        # Output Options Frame
        output_frame = ttk.LabelFrame(self.root, text="Output Options")
        output_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(output_frame, text="Output Format:").pack(side="left", padx=5)
        self.output_format_var = tk.StringVar(value=self.config.OUTPUT_FORMAT)
        ttk.Combobox(output_frame, textvariable=self.output_format_var,
                    values=["excel", "parquet"], width=10).pack(side="left", padx=5)

        tk.Label(output_frame, text="Output Dir:").pack(side="left", padx=5)
        self.output_dir_var = tk.StringVar(value=self.config.OUTPUT_DIR)
        tk.Entry(output_frame, textvariable=self.output_dir_var, width=20).pack(side="left", padx=5)
        tk.Button(output_frame, text="Browse", command=self.browse_output).pack(side="left", padx=2)

        # Control Buttons
        control_frame = tk.Frame(self.root)
        control_frame.pack(fill="x", padx=10, pady=10)

        self.start_btn = tk.Button(control_frame, text="Start Scraping",
                                 command=self.start_scraping, bg="#4CAF50", fg="white",
                                 font=("Arial", 10, "bold"), width=15)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(control_frame, text="Stop",
                                 command=self.stop_scraping, bg="#f44336", fg="white",
                                 font=("Arial", 10, "bold"), width=10, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        tk.Button(control_frame, text="Exit", command=self.exit_app,
                 bg="#607D8B", fg="white", font=("Arial", 10), width=10).pack(side="right", padx=5)

        # Progress Frame
        progress_frame = ttk.LabelFrame(self.root, text="Progress")
        progress_frame.pack(fill="x", padx=10, pady=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=5, pady=5)

        self.progress_text_var = tk.StringVar(value="Ready")
        tk.Label(progress_frame, textvariable=self.progress_text_var).pack(padx=5)

        # Status Label
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(self.root, textvariable=self.status_var,
                                    relief="sunken", anchor="w")
        self.status_label.pack(fill="x", padx=10, pady=5)

    def add_channel(self, event=None):
        channel = self.channel_entry_var.get().strip()
        if channel:
            self.channels_list.append(channel)
            self.channels_listbox.insert(tk.END, channel)
            self.channel_entry_var.set("")

    def add_link(self):
        channel = self.channel_entry_var.get().strip()
        if channel:
            # Ensure proper format
            if not channel.startswith("@") and not channel.startswith("https://"):
                channel = "@" + channel
            self.channels_list.append(channel)
            self.channels_listbox.insert(tk.END, channel)
            self.channel_entry_var.set("")

    def add_id(self):
        channel_id = self.channel_entry_var.get().strip()
        if channel_id:
            self.channels_list.append(channel_id)
            self.channels_listbox.insert(tk.END, f"ID: {channel_id}")
            self.channel_entry_var.set("")

    def clear_channels(self):
        self.channels_list.clear()
        self.channels_listbox.delete(0, tk.END)

    def browse_output(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.output_dir_var.set(dir_path)

    def start_scraping(self):
        if not self.channels_list:
            messagebox.showerror("Error", "Please add at least one channel/group/link")
            return

        self.scraping = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress_var.set(0)

        # Run in separate thread
        threading.Thread(target=self.run_async_scraping, daemon=True).start()

    def stop_scraping(self):
        self.scraping = False
        self.status_var.set("Stopping...")
        self.progress_text_var.set("Stopping...")

    def exit_app(self):
        if self.scraping:
            if messagebox.askyesno("Confirm", "Scraping in progress. Exit anyway?"):
                self.root.destroy()
        else:
            self.root.destroy()

    def run_async_scraping(self):
        asyncio.run(self.scrape_async())

    async def scrape_async(self):
        # Build config
        config = ScraperConfig()
        config.API_ID = int(self.api_id_var.get() or 0)
        config.API_HASH = self.api_hash_var.get()
        config.PHONE = self.phone_var.get()
        config.CHANNELS = self.channels_list
        # Combine date + time into a full datetime before converting to UTC
        # Time inputs are in local timezone (Asia/Dhaka, UTC+6), convert to UTC
        date_min_str = self.date_min_var.get()
        time_start_str = self.time_start_var.get()
        datetime_min_str = f"{date_min_str} {time_start_str}"
        dt_min_naive = datetime.fromisoformat(datetime_min_str)
        config.DATE_MIN = dt_min_naive.replace(tzinfo=DEFAULT_TZ).astimezone(timezone.utc)

        date_max_str = self.date_max_var.get()
        time_end_str = self.time_end_var.get()
        datetime_max_str = f"{date_max_str} {time_end_str}"
        dt_max_naive = datetime.fromisoformat(datetime_max_str)
        config.DATE_MAX = dt_max_naive.replace(tzinfo=DEFAULT_TZ).astimezone(timezone.utc)

        # Always save as Excel (requirement: always produce an .xlsx file)
        config.OUTPUT_FORMAT = "excel"
        config.OUTPUT_DIR = self.output_dir_var.get()
        config.FILE_PREFIX = "gui_scrape"

        # Extract keywords from GUI textbox
        raw_keywords = self.keywords_text.get("1.0", tk.END).strip()
        if raw_keywords:
            config.BUYER_KEYWORDS = [kw.strip() for kw in raw_keywords.split(",") if kw.strip()]
        else:
            config.BUYER_KEYWORDS = []

        config.SCRAPE_MODE = self.mode_var.get()
        config.SELLER_KEYWORDS = self.config.SELLER_KEYWORDS

        self.status_var.set("Connecting to Telegram...")
        self.progress_text_var.set("Connecting...")

        # Connect with OTP (would be prompted interactively)
        from telethon.sync import TelegramClient

        self.scraper = TelegramScraper(config)
        self.scraper.progress_callback = self.update_gui_progress
        self.scraper.client = TelegramClient(
            config.USERNAME or "session",
            config.API_ID,
            config.API_HASH
        )

        try:
            # This would normally ask for OTP
            self.status_var.set("Enter OTP code when prompted in terminal...")
            self.progress_text_var.set("Waiting for OTP...")
            await self.scraper.client.start(phone=config.PHONE)

            self.status_var.set("Connected! Starting scrape...")
            self.progress_text_var.set("Scraping messages...")

            # Scrape all channels with progress
            for channel in config.CHANNELS:
                if not self.scraping:
                    break

                self.progress_text_var.set(f"Scraping: {channel}")
                await self.scraper.scrape_channel(channel.strip())

            # Save final data (partial or complete) before exiting
            final_path = self.scraper._save_final()

            if self.scraping:
                self.status_var.set("Scraping complete!")
                self.progress_text_var.set(f"Complete! Found {self.scraper.total_processed} unique buyers")
                self.progress_var.set(100)
                messagebox.showinfo("Complete", f"Scraping finished! Found {self.scraper.total_processed} unique buyers.\n\nSaved to:\n{final_path}")
            else:
                self.status_var.set("Stopped by user")
                self.progress_text_var.set(f"Stopped. Data saved to:\n{final_path}")
                messagebox.showinfo("Stopped", f"Scraping stopped by user.\nData saved to:\n{final_path}")

        except Exception as e:
            self.status_var.set(f"Error: {e}")
            self.progress_text_var.set(f"Error: {str(e)[:50]}")
            messagebox.showerror("Error", str(e))

        finally:
            if self.scraper:
                await self.scraper.disconnect()
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.scraping = False

    def update_gui_progress(self, channel, total_processed, msg_date):
        """Update GUI progress bar and text based on actual scraper progress."""
        if not self.scraping:
            if self.scraper:
                self.scraper.stop_requested = True
            return

        total_seconds = (self.scraper.config.DATE_MAX - self.scraper.config.DATE_MIN).total_seconds()
        if total_seconds > 0:
            elapsed_seconds = (self.scraper.config.DATE_MAX - msg_date).total_seconds()
            progress = max(0.0, min(100.0, (elapsed_seconds / total_seconds) * 100))
        else:
            progress = 0.0

        self.progress_var.set(progress)
        self.status_var.set(f"Scraping {channel}... Processed {total_processed} messages")
        self.progress_text_var.set(f"Progress: {progress:.2f}% | Processed: {total_processed} messages")

    def toggle_keywords_state(self):
        """Enable or disable keywords entry text box based on selected mode."""
        if self.mode_var.get() == "keyword":
            self.keywords_text.config(state="normal", bg="white")
        else:
            self.keywords_text.config(state="disabled", bg="#e0e0e0")


def main():
    root = tk.Tk()
    app = TelegramScraperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()