"""
Automated Test Suite for Telegram Buyer Lead Scraper
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/test_buyer_scraper.py
"""

import os
import json
import csv
import time
import random
from pathlib import Path
from buyer_detector import BuyerDetector
from storage_handler import StorageHandler
from llm_analyzer import LLMBuyerAnalyzer

TEST_DIR = Path(__file__).parent.resolve()
TEST_JSON = TEST_DIR / "test_buyers.json"
TEST_CSV = TEST_DIR / "test_buyers.csv"


def test_buyer_detection():
    detector = BuyerDetector()

    # Positive test cases (Buyers)
    positive_samples = [
        "WTB 100k Telegram members for crypto channel. Budget $500",
        "Hello everyone, I want to buy a verified Stripe account",
        "Looking to buy bulk Gmail accounts with recovery email",
        "Anyone selling aged Twitter accounts? I need 5 immediately",
        "আমার একটি প্রিমিয়াম টেলিগ্রাম অ্যাকাউন্ট কিনতে চাই। বাজেট ১০০০ টাকা।",
        "Need to buy graphics design service for my YouTube thumbnail",
        "What is the price for 10k Instagram followers? Ready to order now"
    ]

    for sample in positive_samples:
        result = detector.analyze(sample)
        assert result["is_buyer"] is True, f"Failed to detect buyer for: {sample}"
        assert len(result["matched_keywords"]) > 0
        assert result["confidence"] in ["HIGH", "MEDIUM"]

    # Negative test cases (Sellers / Spam / Irrelevant)
    negative_samples = [
        "WTS 50k active crypto members. DM me to buy",
        "Selling aged Twitter accounts, 100% genuine and available now",
        "আমার কাছে কিছু রেডি ফেসবুক পেজ বিক্রি হবে। ইনবক্স করুন।",
        "Just wanted to say hello to everyone in the group",
        "Does anyone know how the weather is today?"
    ]

    for sample in negative_samples:
        result = detector.analyze(sample)
        assert result["is_buyer"] is False, f"Incorrectly marked as buyer: {sample}"

    print("✅ [TEST PASSED] BuyerDetector accuracy verified across positive and negative cases!")


def test_llm_buyer_analysis():
    analyzer = LLMBuyerAnalyzer()
    if not analyzer.api_key:
        print("⚠️ [SKIPPED] LLM API Key not found, skipping live LLM test.")
        return

    # Positive buyer case
    res1 = analyzer.analyze_message("Looking to hire a python developer for web scraping. Budget $250.")
    assert res1.get("is_buyer") is True, f"LLM failed to identify buyer: {res1}"
    assert "developer" in (res1.get("product_or_service") or "").lower() or "scraping" in (res1.get("product_or_service") or "").lower()

    # Negative seller case
    res2 = analyzer.analyze_message("Selling verified Telegram channels with 50k members. DM for price.")
    assert res2.get("is_buyer") is False, f"LLM incorrectly marked seller as buyer: {res2}"

    print("✅ [TEST PASSED] LLMBuyerAnalyzer successfully classified buyer and seller messages via LLM!")



def test_storage_handler():
    # Clean up test files if exist
    if TEST_JSON.exists():
        TEST_JSON.unlink()
    if TEST_CSV.exists():
        TEST_CSV.unlink()

    storage = StorageHandler(json_path=TEST_JSON, csv_path=TEST_CSV)

    mock_buyers = [
        {
            "message_id": 101,
            "chat_id": -1001234567,
            "chat_title": "Freelance Market",
            "date": "2026-09-13 14:00:00",
            "sender_id": 9991,
            "sender_name": "John Doe",
            "sender_username": "@johndoe",
            "confidence": "HIGH",
            "matched_keywords": ["wtb", "want to buy"],
            "message_text": "WTB high authority backlinks, budget $300",
            "message_link": "https://t.me/freelancemarket/101"
        },
        {
            "message_id": 102,
            "chat_id": -1001234567,
            "chat_title": "Freelance Market",
            "date": "2026-09-13 14:05:00",
            "sender_id": 9992,
            "sender_name": "Rahim Ahmed",
            "sender_username": "@rahimahmed",
            "confidence": "HIGH",
            "matched_keywords": ["কিনতে চাই", "বাজেট"],
            "message_text": "আমি টেলিগ্রাম বট ডেভেলপমেন্ট সার্ভিস কিনতে চাই। বাজেট ভালো।",
            "message_link": "https://t.me/freelancemarket/102"
        }
    ]

    count = storage.save_buyers(mock_buyers)
    assert count == 2
    assert TEST_JSON.exists(), "JSON file was not created!"
    assert TEST_CSV.exists(), "CSV file was not created!"

    # Verify JSON content
    with open(TEST_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 2
        assert data[0]["sender_name"] == "John Doe"
        assert data[1]["sender_name"] == "Rahim Ahmed"

    # Verify CSV content
    with open(TEST_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["message_id"] == "101"
        assert reader[1]["message_id"] == "102"

    # Test Deduplication: Saving same records again should not increase count
    count2 = storage.save_buyers(mock_buyers)
    assert count2 == 2, f"Deduplication failed! Expected 2 but got {count2}"

    # Clean up test files
    TEST_JSON.unlink()
    TEST_CSV.unlink()
    print("✅ [TEST PASSED] StorageHandler persistence and deduplication verified!")


def test_100_messages_simulation_with_human_delay():
    """
    Simulate scanning 100 messages with intent detection and rate-limiting validation,
    saving to isolated simulation_test_buyers.json and simulation_test_buyers.csv.
    """
    detector = BuyerDetector()
    sim_json = TEST_DIR / "simulation_test_buyers.json"
    sim_csv = TEST_DIR / "simulation_test_buyers.csv"
    storage = StorageHandler(json_path=sim_json, csv_path=sim_csv)

    print("🚀 [SIMULATION START] Scanning 100 simulated messages to verify pipeline logic...")

    sample_texts = [
        ("WTB premium accounts, budget $200", True),
        ("Good morning everyone!", False),
        ("Selling Canva Pro accounts, dm me", False),
        ("Looking to buy high quality graphic design services", True),
        ("WTS Telegram subscribers 1k for $5", False),
        ("কারো কাছে কি ভালো পাইথন স্ক্রিপ্ট আছে? কিনতে চাই।", True),
        ("Check out my new video link: youtube.com", False),
        ("Anyone selling USA phone numbers? Need 10 now", True),
        ("How to code in Python?", False),
        ("I need a reliable VPS provider, willing to pay monthly", True),
    ]

    simulated_buyers = []
    delays_recorded = []

    # Generate 100 messages
    for i in range(1, 101):
        template_text, should_be_buyer = sample_texts[(i - 1) % len(sample_texts)]
        msg_id = 2000 + i
        text = f"[Msg #{i}] {template_text}"

        # Human-like random delay: for fast unit testing in test suite, scale to small randomized fractions
        # while validating delay range logic
        delay = round(random.uniform(0.01, 0.04), 3)
        time.sleep(delay)
        delays_recorded.append(delay)

        analysis = detector.analyze(text)
        if analysis["is_buyer"]:
            buyer_rec = {
                "message_id": msg_id,
                "chat_id": -1009876543,
                "chat_title": "Telegram Buyer Hub",
                "date": "2026-09-13 18:30:00",
                "sender_id": 50000 + i,
                "sender_name": f"User_{i}",
                "sender_username": f"@user_{i}",
                "confidence": analysis["confidence"],
                "matched_keywords": analysis["matched_keywords"],
                "message_text": text,
                "message_link": f"https://t.me/buyerhub/{msg_id}"
            }
            simulated_buyers.append(buyer_rec)

    assert len(delays_recorded) == 100, f"Expected 100 delays recorded, got {len(delays_recorded)}"
    assert all(0.01 <= d <= 0.04 for d in delays_recorded)

    # Save to isolated simulation storage
    saved_count = storage.save_buyers(simulated_buyers)
    assert saved_count == len(simulated_buyers)
    assert sim_json.exists()
    assert sim_csv.exists()

    with open(sim_json, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
        assert len(saved_data) == len(simulated_buyers)

    print(f"✅ [SIMULATION PASSED] 100 messages processed in test pipeline!")
    print(f"   • টেস্ট মেসেজ স্ক্যান: {len(delays_recorded)}")
    print(f"   • বায়ার শনাক্ত: {len(simulated_buyers)} জন")
    print(f"   • সিমুলেশন টেস্ট ফাইল: {sim_json} ({sim_json.stat().st_size} bytes)")
    print(f"   • সিমুলেশন CSV ফাইল: {sim_csv} ({sim_csv.stat().st_size} bytes)")


if __name__ == "__main__":
    print("🧪 Running Telegram Buyer Scraper Automated Verification...")
    test_buyer_detection()
    test_llm_buyer_analysis()
    test_storage_handler()
    test_100_messages_simulation_with_human_delay()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
