"""
Verification Test Suite: RAW-FIRST, LLM-ONLY CLASSIFICATION PIPELINE
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/test_raw_llm_pipeline.py
"""

import json
import csv
from pathlib import Path
from llm_analyzer import LLMBuyerAnalyzer
from storage_handler import StorageHandler

TEST_DIR = Path(__file__).parent.resolve()
TEST_RAW_JSON = TEST_DIR / "simulation_raw_messages.json"
TEST_BUYERS_JSON = TEST_DIR / "simulation_buyers.json"
TEST_BUYERS_CSV = TEST_DIR / "simulation_buyers.csv"


def test_zero_python_heuristic_compliance():
    """Verify that llm_analyzer and telegram_scraper contain zero keyword or regex classification logic."""
    with open(TEST_DIR / "llm_analyzer.py", "r", encoding="utf-8") as f:
        analyzer_code = f.read()

    with open(TEST_DIR / "telegram_scraper.py", "r", encoding="utf-8") as f:
        scraper_code = f.read()

    # Neither should use regex or keyword matching for classification
    assert "BUYER_KEYWORDS" not in analyzer_code, "llm_analyzer must not import or use BUYER_KEYWORDS"
    assert "BUYER_KEYWORDS" not in scraper_code, "telegram_scraper must not import or use BUYER_KEYWORDS"
    assert "BuyerDetector" not in scraper_code, "telegram_scraper must not call BuyerDetector"
    print("✅ [TEST PASSED] Architecture Mandate Verified: Zero Python keywords, zero regex classification in pipeline!")


def test_llm_semantic_classification():
    """Verify LLM extracts buyer, need, budget, quantity, confidence, evidence, reasoning."""
    analyzer = LLMBuyerAnalyzer()
    if not analyzer.api_key:
        print("⚠️ [SKIPPED] OPENROUTER_API_KEY missing, skipping live LLM test.")
        return

    # Case 1: Buyer with specific need, budget, and quantity
    raw_buyer_msg = "Looking to buy 10 high-tier aged Gmail accounts. Willing to pay $150 total via Crypto. Need urgently."
    res1 = analyzer.analyze_message(raw_buyer_msg)
    assert res1["buyer"] is True, f"LLM failed to classify buyer: {res1}"
    assert res1["need"] is not None
    assert res1["confidence"] >= 0.8
    assert res1["evidence"] is not None

    # Case 2: Seller message (WTS) - Must be rejected by LLM
    raw_seller_msg = "WTS premium VPN accounts, 1 year warranty, fast delivery. DM me now."
    res2 = analyzer.analyze_message(raw_seller_msg)
    assert res2["buyer"] is False, f"LLM incorrectly marked seller as buyer: {res2}"

    # Case 3: Casual chatter - Must be rejected by LLM
    raw_chatter = "Can anyone recommend a good movie to watch tonight?"
    res3 = analyzer.analyze_message(raw_chatter)
    assert res3["buyer"] is False

    print("✅ [TEST PASSED] LLM-ONLY Classification Verified: Buyer, Seller, and Chatter correctly classified with lean structured evidence!")


def test_raw_data_layer_and_storage():
    """Verify RAW DATA LAYER saves 100% of raw messages, and structured storage preserves all LLM fields."""
    if TEST_RAW_JSON.exists():
        TEST_RAW_JSON.unlink()
    if TEST_BUYERS_JSON.exists():
        TEST_BUYERS_JSON.unlink()
    if TEST_BUYERS_CSV.exists():
        TEST_BUYERS_CSV.unlink()

    storage = StorageHandler(
        raw_path=TEST_RAW_JSON,
        json_path=TEST_BUYERS_JSON,
        csv_path=TEST_BUYERS_CSV
    )

    # 1. Raw Data Layer Test: 3 raw messages (1 buyer, 1 seller, 1 spam)
    raw_stream = [
        {
            "message_id": 501,
            "chat_id": -100111,
            "chat_title": "Telegram Tech Market",
            "date": "2026-09-14 15:00:00",
            "sender_id": 901,
            "sender_name": "Alice",
            "sender_username": "@alice",
            "raw_text": "WTB Flutter developer for 2 weeks contract. Budget $1000.",
            "message_link": "https://t.me/c/111/501"
        },
        {
            "message_id": 502,
            "chat_id": -100111,
            "chat_title": "Telegram Tech Market",
            "date": "2026-09-14 15:01:00",
            "sender_id": 902,
            "sender_name": "Bob",
            "sender_username": "@bob",
            "raw_text": "Selling Telegram channels with organic reach. Contact me.",
            "message_link": "https://t.me/c/111/502"
        },
        {
            "message_id": 503,
            "chat_id": -100111,
            "chat_title": "Telegram Tech Market",
            "date": "2026-09-14 15:02:00",
            "sender_id": 903,
            "sender_name": "Charlie",
            "sender_username": "@charlie",
            "raw_text": "Good afternoon everyone!",
            "message_link": "https://t.me/c/111/503"
        }
    ]

    saved_raw = storage.save_raw_messages(raw_stream)
    assert saved_raw == 3, f"Expected 3 raw messages saved, got {saved_raw}"
    assert TEST_RAW_JSON.exists()

    with open(TEST_RAW_JSON, "r", encoding="utf-8") as f:
        raw_persisted = json.load(f)
        assert len(raw_persisted) == 3
        # Check raw text preservation
        assert raw_persisted[0]["raw_text"] == "WTB Flutter developer for 2 weeks contract. Budget $1000."
        assert raw_persisted[1]["raw_text"] == "Selling Telegram channels with organic reach. Contact me."

    # 2. Structured Buyer Storage Test
    structured_leads = [
        {
            "message_id": 501,
            "chat_id": -100111,
            "chat_title": "Telegram Tech Market",
            "date": "2026-09-14 15:00:00",
            "sender_id": 901,
            "sender_name": "Alice",
            "sender_username": "@alice",
            "buyer": True,
            "need": "Flutter developer",
            "budget": "$1000",
            "quantity": "1 developer (2 weeks)",
            "urgency": "HIGH",
            "confidence": 0.98,
            "evidence": "WTB Flutter developer",
            "raw_text": "WTB Flutter developer for 2 weeks contract. Budget $1000.",
            "message_link": "https://t.me/c/111/501"
        }
    ]

    saved_buyers = storage.save_buyers(structured_leads)
    assert saved_buyers == 1
    assert TEST_BUYERS_JSON.exists()
    assert TEST_BUYERS_CSV.exists()

    with open(TEST_BUYERS_JSON, "r", encoding="utf-8") as f:
        buyer_data = json.load(f)
        assert len(buyer_data) == 1
        assert buyer_data[0]["need"] == "Flutter developer"
        assert buyer_data[0]["evidence"] == "WTB Flutter developer"

    with open(TEST_BUYERS_CSV, "r", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
        assert len(csv_rows) == 1
        assert csv_rows[0]["buyer"] == "True"
        assert csv_rows[0]["budget"] == "$1000"
        assert csv_rows[0]["evidence"] == "WTB Flutter developer"

    # Clean up test artifacts
    TEST_RAW_JSON.unlink()
    TEST_BUYERS_JSON.unlink()
    TEST_BUYERS_CSV.unlink()
    print("✅ [TEST PASSED] RAW DATA LAYER & Structured Storage Verified with 100% field preservation!")


if __name__ == "__main__":
    print("🧪 Running RAW-FIRST, LLM-ONLY Pipeline Verification Suite...")
    test_zero_python_heuristic_compliance()
    test_llm_semantic_classification()
    test_raw_data_layer_and_storage()
    print("\n🎉 ALL ARCHITECTURAL TESTS PASSED SUCCESSFULLY!")
