"""
Telegram Authentication & Group Discovery Helper
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/auth_telegram.py
"""

import asyncio
import json
import sys
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

try:
    from config import API_ID, API_HASH, PHONE_NUMBER, BASE_DIR
except ImportError:
    from .config import API_ID, API_HASH, PHONE_NUMBER, BASE_DIR

AUTH_STATE_FILE = BASE_DIR / "auth_state.json"
SESSION_NAME = str(BASE_DIR / "tg_buyer_session")
USER_GROUPS_FILE = BASE_DIR / "user_groups.json"


async def request_code(phone: str = PHONE_NUMBER):
    if not API_ID or not API_HASH:
        print("ERROR: API_ID বা API_HASH কনফিগার করা নেই।")
        return False

    if not phone:
        print("ERROR: PHONE_NUMBER কনফিগার করা নেই।")
        return False

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_AUTHORIZED: {me.first_name} (@{me.username or 'N/A'}, ID: {me.id})")
        await client.disconnect()
        return True

    print(f"REQUESTING_CODE: {phone} নম্বরে কোড পাঠানো হচ্ছে...")
    sent = await client.send_code_request(phone)
    auth_state = {
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash
    }
    with open(AUTH_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(auth_state, f, indent=2)

    print(f"CODE_SENT_SUCCESSFULLY: কোড সফলভাবে পাঠানো হয়েছে। phone_code_hash সংরক্ষিত।")
    await client.disconnect()
    return True


async def verify_code(code: str, password: str = None):
    if not AUTH_STATE_FILE.exists():
        print("ERROR: auth_state.json পাওয়া যায়নি। অনুগ্রহ করে প্রথমে কোড রিকোয়েস্ট করুন।")
        return False

    with open(AUTH_STATE_FILE, "r", encoding="utf-8") as f:
        auth_state = json.load(f)

    phone = auth_state.get("phone", PHONE_NUMBER)
    phone_code_hash = auth_state.get("phone_code_hash")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if password:
            await client.sign_in(password=password)
        else:
            print("2FA_PASSWORD_REQUIRED: এই একাউন্টে 2FA পাসওয়ার্ড চালু আছে। অনুগ্রহ করে পাসওয়ার্ড প্রদান করুন।")
            await client.disconnect()
            return False
    except PhoneCodeInvalidError:
        print("INVALID_CODE: প্রদত্ত কোডটি সঠিক নয়। অনুগ্রহ করে পুনরায় সঠিক কোডটি দিন।")
        await client.disconnect()
        return False
    except PhoneCodeExpiredError:
        print("CODE_EXPIRED: কোডের মেয়াদ শেষ হয়ে গেছে। অনুগ্রহ করে নতুন করে কোড রিকোয়েস্ট করুন।")
        await client.disconnect()
        return False

    me = await client.get_me()
    print(f"LOGIN_SUCCESSFUL: স্বাগতম {me.first_name} {me.last_name or ''} (@{me.username or 'N/A'}, ID: {me.id})")

    # Clean up state file
    try:
        AUTH_STATE_FILE.unlink()
    except Exception:
        pass

    # Fetch and save groups
    await fetch_and_save_groups(client)
    await client.disconnect()
    return True


async def fetch_and_save_groups(client: TelegramClient = None):
    should_disconnect = False
    if client is None:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await client.connect()
        should_disconnect = True

    if not await client.is_user_authorized():
        print("NOT_AUTHORIZED: একাউন্ট অথোরাইজড নয়।")
        if should_disconnect:
            await client.disconnect()
        return []

    print("FETCHING_DIALOGS: আপনার একাউন্টের গ্রুপ ও চ্যানেলগুলো অনুসন্ধান করা হচ্ছে...")
    groups = []
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            entity = dialog.entity
            title = dialog.name
            chat_id = dialog.id
            username = getattr(entity, "username", None)
            is_megagroup = getattr(entity, "megagroup", False)
            is_channel = getattr(entity, "broadcast", False)
            
            group_type = "Channel" if is_channel else ("Supergroup" if is_megagroup else "Group")

            groups.append({
                "title": title,
                "id": chat_id,
                "username": f"@{username}" if username else None,
                "type": group_type,
                "unread_count": dialog.unread_count
            })

    with open(USER_GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    print(f"FOUND_GROUPS: মোট {len(groups)} টি গ্রুপ/চ্যানেল শনাক্ত করা হয়েছে এবং {USER_GROUPS_FILE} ফাইলে সংরক্ষিত হয়েছে।")
    for idx, g in enumerate(groups[:15], 1):
        uname = f" ({g['username']})" if g['username'] else ""
        print(f"  {idx}. [{g['type']}] {g['title']}{uname} (ID: {g['id']})")
    if len(groups) > 15:
        print(f"  ... এবং আরও {len(groups) - 15} টি গ্রুপ।")

    if should_disconnect:
        await client.disconnect()
    return groups


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "request"
    if action == "request":
        asyncio.run(request_code())
    elif action == "verify":
        code_input = sys.argv[2] if len(sys.argv) > 2 else ""
        pwd_input = sys.argv[3] if len(sys.argv) > 3 else None
        if not code_input:
            print("ERROR: কোড প্রদান করা হয়নি। ব্যবহার: python3 auth_telegram.py verify <code> [password]")
        else:
            asyncio.run(verify_code(code_input, pwd_input))
    elif action == "groups":
        asyncio.run(fetch_and_save_groups())
    else:
        print("Unknown action")
