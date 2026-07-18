#!/usr/bin/env python3
"""
Reads the latest translated Excel file.
If the 'Final Semantic Category' or 'Primary Intent' matches configured buyer criteria,
it fetches the user's Author ID and dynamically sends a personal message using Telethon.
Features:
- Handles rate-limiting and account safety using custom delay configurations.
- Avoids messaging the same Author ID multiple times using a 'Messaged' log column.
- Skips sending if the client session is restricted or encounters FloodWait.
"""

import asyncio
import sys
import glob
import os
import random
import time
import pandas as pd
from config import config
from telethon import TelegramClient
from telethon.errors import FloodWaitError

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

async def main():
    # Find latest translated file
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    files = glob.glob(os.path.join(output_dir, "*_translated.xlsx"))
    if not files:
        print("No translated Excel files found in output directory.")
        return
        
    latest_file = max(files, key=os.path.getmtime)
    print(f"Reading translated file: {os.path.basename(latest_file)}")
    
    try:
        df = pd.read_excel(latest_file, engine='openpyxl')
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return
        
    if "Author ID" not in df.columns or "Final Semantic Category" not in df.columns or "Message ID" not in df.columns:
        print("Required columns (Author ID, Message ID, Final Semantic Category) not found.")
        return
        
    # Add Messaged tracking column if not exists
    if "Messaged" not in df.columns:
        df["Messaged"] = "No"
        
    client = TelegramClient(
        config.USERNAME or "session",
        config.API_ID,
        config.API_HASH
    )
    
    await client.start(phone=config.PHONE)
    print("Connected to Telegram successfully.")
    
    # Configure buyer category filters
    buyer_filters = [c.lower().strip() for c in getattr(config, 'BUYER_CATEGORIES', [])]
    message_template = getattr(config, 'BUYER_MESSAGE_TEMPLATE', "Hello!")
    
    total_rows = len(df)
    messages_sent_count = 0
    print(f"Checking {total_rows} entries for buyer categories: {buyer_filters}...")
    
    try:
        for idx, row in df.iterrows():
            author_id_val = row["Author ID"]
            msg_id_val = row["Message ID"]
            group_val = row.get("Group", "")
            category = str(row["Final Semantic Category"]).lower().strip()
            intent = str(row.get("Primary Intent", "")).lower().strip()
            
            # Check if this row matches buyer definitions (e.g. contains 'buyer' or matches intent)
            is_buyer = any(bf in category for bf in buyer_filters) or intent == "buying"
            
            if not is_buyer:
                continue
                
            # Check if already messaged
            if str(row["Messaged"]).strip().lower() == "yes":
                print(f"[{idx+1}/{total_rows}] User {author_id_val} already messaged. Skipping.")
                continue
                
            try:
                target_id = int(author_id_val)
                message_id = int(msg_id_val)
                target_group = str(group_val)
            except (ValueError, TypeError):
                continue
                
            print(f"\n[{idx+1}/{total_rows}] Match Found! Category: '{row['Final Semantic Category']}'. Sender: {target_id}")
            
            # Message Sending Logic
            try:
                print(f"  Attempting to send message to user {target_id}...")
                
                # Fetching message first is crucial to cache the entity in the session database
                if target_group and message_id:
                    try:
                        await client.get_messages(target_group, ids=message_id)
                    except Exception:
                        pass
                
                # Send PM
                await client.send_message(target_id, message_template)
                df.at[idx, "Messaged"] = "Yes"
                messages_sent_count += 1
                print(f"  ✓ Personal Message successfully sent to {target_id}!")
                
                # Random safety delay (1 to 2.5 minutes) between successful messages
                wait_time = random.randint(60, 150)
                print(f"  Waiting {wait_time} seconds before checking next row (safe bot bypass)...")
                await asyncio.sleep(wait_time)
                
            except FloodWaitError as fe:
                print(f"  ⚠ Rate limited by Telegram (FloodWait). Need to sleep for {fe.seconds} seconds.")
                await asyncio.sleep(fe.seconds)
            except Exception as e:
                print(f"  ✗ Failed to send message to {target_id}: {e}")
                df.at[idx, "Messaged"] = f"Failed: {e}"
                
    except KeyboardInterrupt:
        print("\n⚠ Stopped by user. Saving message status...")
    finally:
        # Save file with updated message status
        try:
            df.to_excel(latest_file, index=False, engine='openpyxl')
            print(f"\n✓ Processed. Messaged status saved to: {os.path.basename(latest_file)}")
        except Exception as e:
            print(f"Error saving file: {e}")
            
        await client.disconnect()
        print(f"Disconnected. Sent {messages_sent_count} messages in this run.")

if __name__ == "__main__":
    asyncio.run(main())
