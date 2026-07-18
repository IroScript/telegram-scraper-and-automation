#!/usr/bin/env python3
"""
Translates the 'Content' column of the latest generated Excel file in the output folder
into Bengali and English using the OpenRouter API (Tencent: Hy3).
Saves the results into two new columns in the same Excel file (appended with _translated).
"""

import os
import sys
import glob
import json
import requests
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def load_api_key():
    if "OPENROUTER_API_KEY" in os.environ:
        return os.environ["OPENROUTER_API_KEY"]
    
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openRouterAPIKey.txt")
    if os.path.exists(key_path):
        try:
            with open(key_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("sk-or-v1-"):
                        return stripped
        except Exception:
            pass
    return ""

API_KEY = load_api_key()
MODEL = "tencent/hy3:free"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

def translate_text(text: str, target_lang: str) -> str:
    if not text or not str(text).strip():
        return ""
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    prompt = (
        f"Translate the following Telegram message into {target_lang}.\n"
        "Return ONLY the translated text. Do not add any explanation, quotes, or introduction.\n\n"
        f"Message:\n{text}"
    )
    
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        res_json = response.json()
        translated = res_json['choices'][0]['message']['content'].strip()
        # Clean any surrounding quotes if returned by the LLM
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1].strip()
        if translated.startswith("'") and translated.endswith("'"):
            translated = translated[1:-1].strip()
        return translated
    except Exception as e:
        print(f"  Error translating '{str(text)[:20]}...' to {target_lang}: {e}")
        return f"Translation Error: {e}"

def main():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    xlsx_files = glob.glob(os.path.join(output_dir, "*.xlsx"))
    # Exclude previously translated files to avoid loops
    xlsx_files = [f for f in xlsx_files if "_translated" not in f]
    
    if not xlsx_files:
        print("No Excel files found in the output directory.")
        return
    
    # Get the latest modified file
    latest_file = max(xlsx_files, key=os.path.getmtime)
    print(f"Latest file found: {os.path.basename(latest_file)}")
    
    # Load Excel
    try:
        df = pd.read_excel(latest_file, engine='openpyxl')
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return
        
    if 'Content' not in df.columns:
        print("Error: 'Content' column not found in the Excel file.")
        return
        
    total_rows = len(df)
    print(f"Starting translation for {total_rows} rows...")
    
    bangla_translations = []
    english_translations = []
    
    for idx, row in df.iterrows():
        content = str(row['Content'])
        print(f"[{idx+1}/{total_rows}] Translating: {content[:40]}...")
        
        # Translate to Bengali
        bn_trans = translate_text(content, "Bengali")
        bangla_translations.append(bn_trans)
        
        # Translate to English
        en_trans = translate_text(content, "English")
        english_translations.append(en_trans)
        
    # Add new columns
    df['Content_Bengali'] = bangla_translations
    df['Content_English'] = english_translations
    
    # Save translated file
    file_dir, file_name = os.path.split(latest_file)
    name, ext = os.path.splitext(file_name)
    new_filename = f"{name}_translated{ext}"
    new_filepath = os.path.join(file_dir, new_filename)
    
    try:
        df.to_excel(new_filepath, index=False, engine='openpyxl')
        print(f"\n✓ Translation complete! Saved to: {new_filepath}")
    except Exception as e:
        print(f"Error saving translated file: {e}")

if __name__ == "__main__":
    main()
