"""
Comprehensive Verification Suite: 20-Tier Fallback LLM Architecture
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/test_fallback_matrix.py
"""

import sys
from pathlib import Path
from llm_analyzer import LLMBuyerAnalyzer, FALLBACK_SLOTS

BASE_DIR = Path("/home/mdkamruzzamanirak_gmail_com/telegram-bot")


def test_fallback_slots_integrity():
    """Verify that exactly 20 distinct slots are configured with proper schemas."""
    print("🔍 [TEST 1] Verifying 20-Tier Fallback Slot Matrix Configuration...")
    assert len(FALLBACK_SLOTS) == 20, f"Expected 20 fallback slots, but found {len(FALLBACK_SLOTS)}"

    slot_ids = [s["id"] for s in FALLBACK_SLOTS]
    assert slot_ids == list(range(1, 21)), f"Slot IDs must be sequentially numbered 1 to 20: {slot_ids}"

    required_keys = {"id", "name", "provider", "api_url", "model", "key_env", "max_tokens", "temperature"}
    for slot in FALLBACK_SLOTS:
        missing = required_keys - set(slot.keys())
        assert not missing, f"Slot {slot.get('id')} missing required fields: {missing}"

    print("✅ [TEST 1 PASSED] 20 Fallback Slots verified with 100% schema completeness!")


def test_zero_heuristic_compliance():
    """Verify zero python keyword or regex matching in analyzer."""
    print("🔍 [TEST 2] Verifying Zero-Keyword Architectural Mandate...")
    with open(BASE_DIR / "llm_analyzer.py", "r", encoding="utf-8") as f:
        code = f.read()

    assert "BUYER_KEYWORDS" not in code, "llm_analyzer.py must not import or reference BUYER_KEYWORDS"
    assert "NEGATIVE_KEYWORDS" not in code, "llm_analyzer.py must not reference NEGATIVE_KEYWORDS"
    assert "BuyerDetector" not in code, "llm_analyzer.py must not call BuyerDetector"
    print("✅ [TEST 2 PASSED] Strict LLM-Only mandate preserved with zero Python keywords/heuristics!")


def test_primary_slot_live_execution():
    """Verify primary active slot live inference."""
    print("🔍 [TEST 3] Testing Live Message Inference on Primary Active Slot...")
    analyzer = LLMBuyerAnalyzer()
    sample_buyer = "Looking to buy 10 high-tier aged Gmail accounts. Willing to pay $150 total via Crypto. Need urgently."
    res = analyzer.analyze_message(sample_buyer)

    assert res["buyer"] is True, f"Expected buyer=True, got {res}"
    assert res["need"] is not None, f"Expected need to be populated, got {res}"
    assert res["slot_used"] > 0, f"Expected valid slot_used, got {res.get('slot_used')}"
    print(f"✅ [TEST 3 PASSED] Primary live execution successful! Handled by Slot {res['slot_used']} ({res['slot_name']}) in {res['latency_seconds']}s")


def test_simulated_failover():
    """Verify that when primary slot fails, the system automatically cascades to next active slot."""
    print("🔍 [TEST 4] Testing Automated Failover Cascade (Simulated Outage)...")
    analyzer = LLMBuyerAnalyzer()

    # Intentionally corrupt slot 1 model to simulate rate limit (429) or model outage
    original_model = analyzer.slots[0]["model"]
    analyzer.slots[0]["model"] = "simulated_broken_outage_model"

    sample_buyer = "Want to buy 5 verified Google Voice accounts immediately. Budget $50."
    res = analyzer.analyze_message(sample_buyer)

    # Restore model
    analyzer.slots[0]["model"] = original_model

    assert res["buyer"] is True, f"Failover failed to qualify buyer: {res}"
    assert res["slot_used"] != 1, f"Expected failover to bypass broken Slot 1, but got Slot {res['slot_used']}"
    assert analyzer.stats["fallback_switches"] >= 1, "Expected at least 1 fallback switch recorded"
    print(f"✅ [TEST 4 PASSED] Failover succeeded! Automatically switched to Slot {res['slot_used']} ({res['slot_name']}) without user interruption!")


def test_batch_fallback_inference():
    """Verify batch analysis with fallback."""
    print("🔍 [TEST 5] Testing Batch Message Analysis...")
    analyzer = LLMBuyerAnalyzer()
    batch = [
        {"id": 101, "text": "Need to purchase bulk Telegram sessions. Budget $200."},
        {"id": 102, "text": "Good morning everyone, how is your day?"}
    ]
    results = analyzer.analyze_batch(batch)
    assert len(results) == 2, f"Expected 2 batch results, got {len(results)}"
    assert results[0].get("buyer") is True, f"Expected message 101 to be buyer: {results[0]}"
    assert results[1].get("buyer") is False, f"Expected message 102 to be non-buyer: {results[1]}"
    print("✅ [TEST 5 PASSED] Batch analysis verified with accurate buyer/non-buyer qualification!")


def run_all_tests():
    print("=" * 70)
    print("🧪 RUNNING 20-TIER FALLBACK VERIFICATION TEST SUITE")
    print("=" * 70)
    test_fallback_slots_integrity()
    test_zero_heuristic_compliance()
    test_primary_slot_live_execution()
    test_simulated_failover()
    test_batch_fallback_inference()
    print("=" * 70)
    print("🎉 ALL 5 COMPREHENSIVE FALLBACK MATRIX TESTS PASSED (100% PROVEN)!")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
