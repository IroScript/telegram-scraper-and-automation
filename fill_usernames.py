#!/usr/bin/env python3
"""
Reads the latest Excel file from the output folder, resolves the usernames of all Author IDs
using Telethon (via message sender caching), and saves them back into a new 'Username' column.
Waits 60 to 150 seconds (1 to 2.5 minutes) between each resolution to avoid Telegram bot detection.
"""

import asyncio
import sys
import glob
import os
import random
import time
from datetime import datetime
import pandas as pd
from config import config
from telethon import TelegramClient

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

async def main():
    # Find latest file
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    files = glob.glob(os.path.join(output_dir, "*.xlsx"))
    files = [f for f in files if "_with_usernames" not in f] # exclude outputs
    if not files:
        print("No Excel files found in output directory.")
        return
        
    latest_file = max(files, key=os.path.getmtime)
    print(f"Reading file: {os.path.basename(latest_file)}")
    
    try:
        df = pd.read_excel(latest_file, engine='openpyxl')
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return
        
    if "Author ID" not in df.columns or "Message ID" not in df.columns or "Group" not in df.columns:
        print("Required columns (Author ID, Message ID, Group) not found.")
        return
        
    # Add Username column if not exists
    if "Username" not in df.columns:
        df["Username"] = ""
        
    client = TelegramClient(
        config.USERNAME or "session",
        config.API_ID,
        config.API_HASH
    )
    
    await client.start(phone=config.PHONE)
    print("Connected to Telegram successfully.")
    
    total_rows = len(df)
    print(f"Starting username resolution for {total_rows} entries...")
    
    try:
        for idx, row in df.iterrows():
            author_id_val = row["Author ID"]
            msg_id_val = row["Message ID"]
            group_val = row["Group"]
            
            # Check if username is already filled
            if pd.notna(row["Username"]) and str(row["Username"]).strip():
                print(f"[{idx+1}/{total_rows}] Already has username: {row['Username']}. Skipping.")
                continue
                
            try:
                target_id = int(author_id_val)
                message_id = int(msg_id_val)
                target_group = str(group_val)
            except (ValueError, TypeError):
                print(f"[{idx+1}/{total_rows}] Invalid Author/Message ID format: {author_id_val} / {msg_id_val}. Skipping.")
                continue
                
            print(f"\n[{idx+1}/{total_rows}] Resolving Username for ID: {target_id}...")
            user = None
            try:
                # Fetch message to cache peer automatically
                msg = await client.get_messages(target_group, ids=message_id)
                if msg:
                    user = await msg.get_sender()
            except Exception as me:
                print(f"  Warning: Could not fetch message: {me}")
                
            if not user:
                try:
                    # Fallback to direct resolution
                    user = await client.get_entity(target_id)
                except Exception as ge:
                    print(f"  Warning: Could not fetch entity: {ge}")
                    
            if user:
                username = f"@{user.username}" if user.username else "No Username"
                display_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                df.at[idx, "Username"] = username
                print(f"  Found: {username} (Name: {display_name})")
            else:
                df.at[idx, "Username"] = "Unknown"
                print("  Could not resolve user details.")
                
            # Random delay 60 to 150 seconds (1 to 2.5 minutes) to avoid rate limits / bot detection
            if idx < total_rows - 1:
                wait_time = random.randint(60, 150)
                print(f"  Waiting {wait_time} seconds (to avoid Telegram bot detection)...")
                await asyncio.sleep(wait_time)
                
    except KeyboardInterrupt:
        print("\n⚠ Stopped by user. Saving resolved progress so far...")
    finally:
        # Save output file (either overwrite or save as new)
        file_dir, file_name = os.path.split(latest_file)
        name, ext = os.path.splitext(file_name)
        
        # Save to a new file to preserve history
        new_filename = f"{name}_with_usernames{ext}"
        new_filepath = os.path.join(file_dir, new_filename)
        
        try:
            df.to_excel(new_filepath, index=False, engine='openpyxl')
            print(f"\n✓ Completed! Excel file with resolved usernames saved to:\n{new_filepath}")
        except Exception as e:
            print(f"Error saving file: {e}")
            
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
