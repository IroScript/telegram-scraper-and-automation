"""
Buyer Intent Detection Module
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/buyer_detector.py
"""

import re
from typing import Dict, Any, List, Optional
try:
    from config import BUYER_KEYWORDS, NEGATIVE_KEYWORDS
except ImportError:
    from .config import BUYER_KEYWORDS, NEGATIVE_KEYWORDS


class BuyerDetector:
    def __init__(self, buyer_keywords: Optional[List[str]] = None, negative_keywords: Optional[List[str]] = None):
        self.buyer_keywords = buyer_keywords or BUYER_KEYWORDS
        self.negative_keywords = negative_keywords or NEGATIVE_KEYWORDS

    def analyze(self, text: str) -> Dict[str, Any]:
        """
        Analyze a message string to determine if it indicates buyer intent.
        Returns a dictionary containing is_buyer, matched_keywords, negative_matches, and cleaned_text.
        """
        if not text or not isinstance(text, str):
            return {
                "is_buyer": False,
                "confidence": "NONE",
                "matched_keywords": [],
                "negative_matches": [],
                "reason": "Empty or non-text message"
            }

        text_lower = text.lower()

        # Check negative keywords first (e.g., seller / advertisement posts)
        negative_matches = []
        for neg in self.negative_keywords:
            pattern = rf"\b{re.escape(neg.lower())}\b" if neg.isalnum() else re.escape(neg.lower())
            if re.search(pattern, text_lower):
                negative_matches.append(neg)

        # Check buyer keywords
        buyer_matches = []
        for kw in self.buyer_keywords:
            pattern = rf"\b{re.escape(kw.lower())}\b" if kw.isalnum() else re.escape(kw.lower())
            if re.search(pattern, text_lower):
                buyer_matches.append(kw)

        # If buyer keywords contain inquiry phrases like 'anyone selling' or strong buy intent,
        # ignore 'selling' from negative matches
        inquiry_buy_keywords = ["anyone selling", "anybody selling", "who sells", "who is selling", "wtb", "want to buy", "looking to buy", "need to buy", "i need", "we need", "ready to buy", "কিনতে চাই"]
        if any(k in buyer_matches for k in inquiry_buy_keywords):
            negative_matches = [neg for neg in negative_matches if neg not in ["selling", "বিক্রি"]]

        # If negative matches still exist and strongly indicate selling
        if negative_matches and not any(k in ["wtb", "want to buy", "buying", "need to buy", "i need", "কিনতে চাই"] for k in buyer_matches):
            return {
                "is_buyer": False,
                "confidence": "SELLER_REJECTED",
                "matched_keywords": buyer_matches,
                "negative_matches": negative_matches,
                "reason": f"Filtered out due to seller keywords: {', '.join(negative_matches)}"
            }

        if buyer_matches:
            # Determine confidence
            high_intent = ["wtb", "want to buy", "looking to buy", "need to buy", "interested in buying", "buying", "কিনতে চাই", "কেনার ইচ্ছা"]
            if any(k in high_intent for k in buyer_matches):
                confidence = "HIGH"
            elif len(buyer_matches) >= 2:
                confidence = "HIGH"
            else:
                confidence = "MEDIUM"

            return {
                "is_buyer": True,
                "confidence": confidence,
                "matched_keywords": buyer_matches,
                "negative_matches": negative_matches,
                "reason": f"Matched buyer keywords: {', '.join(buyer_matches)}"
            }

        return {
            "is_buyer": False,
            "confidence": "NONE",
            "matched_keywords": [],
            "negative_matches": negative_matches,
            "reason": "No buyer keywords detected"
        }
