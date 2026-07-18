import asyncio
import sys
from config import config
from telethon import TelegramClient
import pandas as pd
import glob
import os

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

async def main():
    # Find latest file
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    files = glob.glob(os.path.join(output_dir, "*.xlsx"))
    if not files:
        print("No Excel files found.")
        return
        
    latest_file = max(files, key=os.path.getmtime)
    print(f"Reading from file: {os.path.basename(latest_file)}")
    
    df = pd.read_excel(latest_file)
    if "Author ID" not in df.columns:
        print("Author ID column not found.")
        return
        
    # Get first non-null Author ID
    author_ids = df["Author ID"].dropna().tolist()
    if not author_ids:
        print("No Author IDs found in the file.")
        return
        
    # Find a valid integer ID and its associated Group & Message ID
    target_id = None
    target_group = None
    message_id = None
    for idx, val in enumerate(author_ids):
        try:
            target_id = int(val)
            target_group = df.iloc[idx]["Group"]
            message_id = int(df.iloc[idx]["Message ID"])
            break
        except (ValueError, KeyError, TypeError):
            continue
            
    if target_id is None:
        print("No valid numerical Author ID found.")
        return
        
    print(f"Target Author ID: {target_id} (from group {target_group}, Message ID: {message_id})")
    
    client = TelegramClient(
        config.USERNAME or "session",
        config.API_ID,
        config.API_HASH
    )
    
    # Start client (reusing session)
    await client.start(phone=config.PHONE)
    print("Connected to Telegram successfully.")
    
    try:
        user = None
        if target_group and message_id:
            print(f"Fetching message {message_id} from {target_group} to resolve sender...")
            try:
                msg = await client.get_messages(target_group, ids=message_id)
                if msg:
                    user = await msg.get_sender()
            except Exception as me:
                print(f"Warning: Could not fetch message: {me}")
                
        if not user:
            print("Falling back to direct get_entity resolution...")
            user = await client.get_entity(target_id)
            
        print("\n" + "="*40)
        print(f"ID: {user.id}")
        print(f"Username: @{user.username if user.username else 'No Username'}")
        print(f"First Name: {user.first_name if user.first_name else ''}")
        print(f"Last Name: {user.last_name if user.last_name else ''}")
        print(f"Is Bot: {user.bot}")
        print("="*40)
    except Exception as e:
        print(f"Error fetching user entity: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
