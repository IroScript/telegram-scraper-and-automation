"""
LLM Semantic Analysis Module for Telegram Messages (20-Tier Fallback Matrix)
File: /home/mdkamruzzamanirak_gmail_com/telegram-bot/llm_analyzer.py

Mandate:
- RAW-FIRST, LLM-ONLY CLASSIFICATION
- Zero Python keyword matching, zero regex filters, zero heuristic pre-classification.
- 20-Tier Fallback Cascade System: Groq, OpenRouter, Google Gemini, DeepSeek,
  Cerebras, SambaNova, Together AI, Mistral AI, OpenCode / Local Ollama.
"""

import os
import re
import json
import time
from typing import Dict, Any, List, Optional
import requests

try:
    from config import (
        LLM_MODEL,
        OPENROUTER_API_KEY,
        GROQ_API_KEY,
        GROQ_API_KEY_2,
        GROQ_MODEL,
        GEMINI_API_KEY,
        GEMINI_MODEL,
        DEEPSEEK_API_KEY,
        DEEPSEEK_MODEL,
        CEREBRAS_API_KEY,
        CEREBRAS_MODEL,
        SAMBANOVA_API_KEY,
        SAMBANOVA_MODEL,
        TOGETHER_API_KEY,
        TOGETHER_MODEL,
        MISTRAL_API_KEY,
        MISTRAL_MODEL,
        OPENCODE_API_URL,
        OPENCODE_API_KEY,
        OPENCODE_MODEL,
        LLM_PROVIDER,
        FALLBACK_TIMEOUT_SECONDS,
        FALLBACK_MAX_RETRIES,
    )
except ImportError:
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
    CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama3.1-8b")
    SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "")
    SAMBANOVA_MODEL = os.getenv("SAMBANOVA_MODEL", "Meta-Llama-3.1-70B-Instruct")
    TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
    TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
    MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    OPENCODE_API_URL = os.getenv("OPENCODE_API_URL", "http://localhost:11434/v1/chat/completions")
    OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
    OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "qwen2.5:7b")
    LLM_PROVIDER = "groq" if GROQ_API_KEY else "openrouter"
    FALLBACK_TIMEOUT_SECONDS = 12.0
    FALLBACK_MAX_RETRIES = 20


def clean_key(val: Optional[str]) -> str:
    """Clean exported bash artifacts and quotes from API key."""
    if not val:
        return ""
    return str(val).replace("export", "").strip().strip("'\"\\")


def extract_json(raw_text: str) -> Dict[str, Any]:
    """Robust JSON extraction from raw model response including markdown and reasoning."""
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        # Search for first { to matching }
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"Could not parse valid JSON from text: {raw_text[:200]}")


# 20-TIER FALLBACK CASCADE SLOTS DEFINITION
FALLBACK_SLOTS: List[Dict[str, Any]] = [
    # --- TIER 1: GROQ HIGH-SPEED CLOUD (Primary Key) ---
    {
        "id": 1,
        "name": "Groq: Qwen 2.5 32B (Primary)",
        "provider": "groq",
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "qwen/qwen3.8-27b",
        "key_env": "GROQ_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },
    {
        "id": 2,
        "name": "Groq: Compound Router",
        "provider": "groq",
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "groq/compound",
        "key_env": "GROQ_API_KEY",
        "max_tokens": 450,
        "temperature": 0.1,
    },
    {
        "id": 3,
        "name": "Groq: Compound Mini",
        "provider": "groq",
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "groq/compound-mini",
        "key_env": "GROQ_API_KEY",
        "max_tokens": 450,
        "temperature": 0.1,
    },
    {
        "id": 4,
        "name": "Groq: Allam 2 7B",
        "provider": "groq",
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "allam-2-7b",
        "key_env": "GROQ_API_KEY",
        "max_tokens": 300,
        "temperature": 0.1,
    },

    # --- TIER 2: OPENROUTER MULTI-MODEL AGGREGATOR ---
    {
        "id": 5,
        "name": "OpenRouter: Meta Llama 3.3 70B Instruct",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },
    {
        "id": 6,
        "name": "OpenRouter: Google Gemini 2.5 Flash Lite",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "google/gemini-2.5-flash-lite",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },
    {
        "id": 7,
        "name": "OpenRouter: Google Gemini 2.5 Flash",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "google/gemini-2.5-flash",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },
    {
        "id": 8,
        "name": "OpenRouter: OpenAI GPT-4o Mini",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "openai/gpt-4o-mini",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },
    {
        "id": 9,
        "name": "OpenRouter: Meta Llama 3.1 8B Instruct",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.1-8b-instruct",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 3: GROQ BACKUP KEY (Key Rotation) ---
    {
        "id": 10,
        "name": "Groq: Secondary Backup Key (Key Rotation)",
        "provider": "groq",
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "qwen/qwen3.8-27b",
        "key_env": "GROQ_API_KEY_2",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 4: GOOGLE GEMINI OFFICIAL API (Direct Generative Language Endpoint) ---
    {
        "id": 11,
        "name": "Google Gemini: Gemini 3.6 Flash (Direct)",
        "provider": "gemini",
        "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
        "model": "gemini-3.6-flash",
        "key_env": "GEMINI_API_KEY",
        "max_tokens": 1000,
        "temperature": 0.1,
    },
    {
        "id": 12,
        "name": "Google Gemini: Gemini 3.8 Flash (Direct)",
        "provider": "gemini",
        "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        "model": "gemini-3.8-flash",
        "key_env": "GEMINI_API_KEY",
        "max_tokens": 1000,
        "temperature": 0.1,
    },
    {
        "id": 13,
        "name": "Google Gemini: Gemini Flash Lite (Direct)",
        "provider": "gemini",
        "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent",
        "model": "gemini-flash-lite-latest",
        "key_env": "GEMINI_API_KEY",
        "max_tokens": 1000,
        "temperature": 0.1,
    },

    # --- TIER 5: DEEPSEEK DIRECT API ---
    {
        "id": 14,
        "name": "DeepSeek: DeepSeek Chat (Direct)",
        "provider": "deepseek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 6: CEREBRAS ULTRA-FAST HARDWARE INFERENCE ---
    {
        "id": 15,
        "name": "Cerebras: Llama 3.1 8B (Ultra-Fast)",
        "provider": "cerebras",
        "api_url": "https://api.cerebras.ai/v1/chat/completions",
        "model": "llama3.1-8b",
        "key_env": "CEREBRAS_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 7: SAMBANOVA CLOUD INFERENCE ---
    {
        "id": 16,
        "name": "SambaNova: Llama 3.1 70B",
        "provider": "sambanova",
        "api_url": "https://api.sambanova.ai/v1/chat/completions",
        "model": "Meta-Llama-3.1-70B-Instruct",
        "key_env": "SAMBANOVA_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 8: TOGETHER AI CLOUD ---
    {
        "id": 17,
        "name": "Together AI: Llama 3.1 70B Turbo",
        "provider": "together",
        "api_url": "https://api.together.xyz/v1/chat/completions",
        "model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "key_env": "TOGETHER_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 9: MISTRAL AI DIRECT ---
    {
        "id": 18,
        "name": "Mistral: Mistral Small Latest",
        "provider": "mistral",
        "api_url": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-small-latest",
        "key_env": "MISTRAL_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
    },

    # --- TIER 10: OPENROUTER FREE TIER EMERGENCY FALLBACK ---
    {
        "id": 19,
        "name": "OpenRouter: Free Tier Emergency",
        "provider": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "liquid/lfm-2.5-2.6b:free",
        "key_env": "OPENROUTER_API_KEY",
        "max_tokens": 200,
        "temperature": 0.1,
    },

    # --- TIER 11: OPENCODE / LOCAL OLLAMA OFFLINE PRIVATE SERVER ---
    {
        "id": 20,
        "name": "OpenCode / Local Ollama (Offline Private)",
        "provider": "opencode",
        "api_url": OPENCODE_API_URL or "http://localhost:11434/v1/chat/completions",
        "model": OPENCODE_MODEL or "qwen2.5:7b",
        "key_env": "OPENCODE_API_KEY",
        "max_tokens": 250,
        "temperature": 0.1,
        "allow_no_key": True,
    }
]


class LLMBuyerAnalyzer:
    """
    20-Tier Fallback Semantic Lead Classification Engine.
    Cascades gracefully across providers and models to ensure 100% continuous uptime.
    """
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = FALLBACK_TIMEOUT_SECONDS
    ):
        self.provider = (provider or LLM_PROVIDER or "groq").lower()
        self.timeout = timeout
        self.slots = list(FALLBACK_SLOTS)

        # Primary backwards-compatibility attributes
        self.api_key = clean_key(api_key or GROQ_API_KEY or OPENROUTER_API_KEY or os.getenv("GROQ_API_KEY", ""))
        self.model = model or GROQ_MODEL or LLM_MODEL or "qwen/qwen3.8-27b"

        # Stats tracking
        self.stats = {
            "total_calls": 0,
            "success_calls": 0,
            "fallback_switches": 0,
            "slot_usage": {s["id"]: 0 for s in self.slots}
        }

    def _get_key_for_slot(self, slot: Dict[str, Any]) -> str:
        """Resolve API key for a specific slot."""
        key_name = slot.get("key_env", "")
        raw = os.getenv(key_name, "")
        if not raw:
            globals_dict = globals()
            raw = globals_dict.get(key_name, "")
        return clean_key(raw)

    def get_active_slots(self) -> List[Dict[str, Any]]:
        """Return list of slots that have valid API keys or allow keyless execution."""
        active = []
        for s in self.slots:
            k = self._get_key_for_slot(s)
            if k or s.get("allow_no_key"):
                active.append({
                    "id": s["id"],
                    "name": s["name"],
                    "provider": s["provider"],
                    "model": s["model"],
                    "has_key": bool(k),
                })
        return active

    def analyze_message(self, text: str) -> Dict[str, Any]:
        """
        Analyze raw Telegram message using 20-tier fallback cascade.
        Zero keyword filters, zero heuristics. 100% LLM classification.
        """
        self.stats["total_calls"] += 1

        if not text or not text.strip():
            return {
                "buyer": False,
                "is_buyer": False,
                "need": None,
                "product_or_service": None,
                "budget": None,
                "quantity": None,
                "urgency": "NONE",
                "confidence": 0.0,
                "evidence": None,
                "provider_used": "none",
                "model_used": "none",
                "slot_used": 0
            }

        prompt = f"""You are an expert lead qualification AI. Analyze the following raw Telegram message to determine if the author wants to BUY, HIRE, or PURCHASE a product or service.

Raw Telegram Message:
\"\"\"{text}\"\"\"

Classification Rules:
- If the sender is SELLING, OFFERING, ADVERTISING (WTS/for sale), PROMOTING, or CASUALLY CHATTING, set buyer to false.
- ONLY set buyer to true if the sender clearly indicates they WANT TO BUY (WTB), NEED TO HIRE, or ARE SEEKING to purchase a product or service.

Respond ONLY with a valid JSON object matching this schema:
If NOT a buyer:
{{"buyer": false, "need": null, "budget": null, "quantity": null, "urgency": "NONE", "confidence": 0.0, "evidence": null}}

If IS a buyer:
{{"buyer": true, "need": "<exact item or service needed>", "budget": "<stated budget or null>", "quantity": "<stated quantity or null>", "urgency": "<HIGH|MEDIUM|LOW>", "confidence": 0.9, "evidence": "<direct quote showing buy intent>"}}"""

        last_error = "No active slots configured"

        # CASCADE THROUGH 20 SLOTS IN PRIORITY ORDER
        for slot in self.slots:
            slot_id = slot["id"]
            slot_name = slot["name"]
            key = self._get_key_for_slot(slot)

            if not key and not slot.get("allow_no_key"):
                # Slot has no API key configured, instantly skip with zero network overhead
                continue

            if slot["provider"] == "gemini":
                headers = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": key
                }
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": slot["max_tokens"],
                        "temperature": slot["temperature"]
                    }
                }
            else:
                headers = {
                    "Content-Type": "application/json"
                }
                if key:
                    headers["Authorization"] = f"Bearer {key}"

                if slot["provider"] == "openrouter":
                    headers["HTTP-Referer"] = "https://github.com/telegram-bot"
                    headers["X-Title"] = "Telegram RAW-LLM Classifier"

                payload = {
                    "model": slot["model"],
                    "max_tokens": slot["max_tokens"],
                    "temperature": slot["temperature"],
                    "messages": [
                        {"role": "system", "content": "You are a concise JSON-only buyer lead qualification assistant. Never output explanations outside JSON."},
                        {"role": "user", "content": prompt}
                    ]
                }

            t0 = time.time()
            try:
                resp = requests.post(
                    slot["api_url"],
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )
                latency = round(time.time() - t0, 3)

                if resp.status_code == 200:
                    data = resp.json()
                    if slot["provider"] == "gemini":
                        content = data["candidates"][0]["content"]["parts"][0]["text"]
                    else:
                        content = data["choices"][0]["message"]["content"]
                    result = extract_json(content)

                    # Normalize results
                    buyer = bool(result.get("buyer", result.get("is_buyer", False)))
                    raw_need = result.get("need") or result.get("product_or_service")
                    if isinstance(raw_need, dict):
                        need = raw_need.get("specification") or raw_need.get("type") or str(raw_need)
                    else:
                        need = str(raw_need).strip() if raw_need else ""

                    raw_budget = result.get("budget")
                    budget = str(raw_budget).strip() if raw_budget else ""
                    raw_quantity = result.get("quantity")
                    quantity = str(raw_quantity).strip() if raw_quantity else ""
                    urgency = result.get("urgency", "NONE")
                    confidence = float(result.get("confidence", 0.0))
                    raw_evidence = result.get("evidence")
                    evidence = str(raw_evidence).strip() if raw_evidence else ""

                    # Filter out schema placeholder echoes
                    if need.lower() in ["item or service", "none", "null", "item", "service", "<exact item or service needed>"]:
                        need = ""
                    if budget.lower() in ["budget string or null", "null", "none", "<stated budget or null>"]:
                        budget = ""
                    if quantity.lower() in ["quantity string or null", "null", "none", "<stated quantity or null>"]:
                        quantity = ""
                    if evidence.lower() in ["exact quote from message", "exact quote", "<direct quote showing buy intent>"]:
                        evidence = ""

                    # If buyer is marked True without valid need or evidence, demote to False
                    if buyer and not need and not evidence:
                        buyer = False

                    # Record stats
                    self.stats["success_calls"] += 1
                    self.stats["slot_usage"][slot_id] += 1

                    return {
                        "buyer": buyer,
                        "is_buyer": buyer,
                        "need": need,
                        "product_or_service": need,
                        "budget": budget,
                        "quantity": quantity,
                        "urgency": urgency,
                        "confidence": confidence,
                        "evidence": evidence,
                        "provider_used": slot["provider"],
                        "model_used": slot["model"],
                        "slot_used": slot_id,
                        "slot_name": slot_name,
                        "latency_seconds": latency
                    }
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    self.stats["fallback_switches"] += 1
                    print(f"⚠️ [FALLBACK] Slot {slot_id} ({slot_name}) failed ({last_error}) -> Switching to next slot...")

            except Exception as e:
                last_error = str(e)
                self.stats["fallback_switches"] += 1
                print(f"⚠️ [FALLBACK] Slot {slot_id} ({slot_name}) exception ({last_error}) -> Switching to next slot...")

        # If all 20 slots fail, return graceful negative output without crashing
        print(f"❌ [ALL FALLBACK SLOTS EXHAUSTED] Last error: {last_error}")
        return {
            "buyer": False,
            "is_buyer": False,
            "need": None,
            "product_or_service": None,
            "budget": None,
            "quantity": None,
            "urgency": "NONE",
            "confidence": 0.0,
            "evidence": None,
            "provider_used": "exhausted",
            "model_used": "none",
            "slot_used": -1,
            "error": last_error
        }

    def analyze_batch(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Batch analysis for multiple messages with fallback support.
        """
        if not messages:
            return []

        formatted_messages = []
        for m in messages:
            formatted_messages.append(f"[MSG_ID: {m.get('id')}] {m.get('text', '').strip()}")

        joined_batch = "\n".join(formatted_messages)

        prompt = f"""You are an expert lead qualification AI. Analyze the following batch of Telegram messages.
Messages:
{joined_batch}

Classification Rules:
- If the sender is SELLING, OFFERING, ADVERTISING (WTS/for sale), PROMOTING, or CASUALLY CHATTING (such as greetings, questions, chat), buyer MUST be false.
- ONLY set buyer to true if the sender clearly indicates they WANT TO BUY (WTB), NEED TO HIRE, or ARE SEEKING to purchase a product or service.

For each message, determine:
- id: <message_id>
- buyer: true/false
- need: exact product/service or null
- budget: price/budget or null
- quantity: units or null
- urgency: "HIGH", "MEDIUM", "LOW", or "NONE"
- confidence: float (0.0 to 1.0)
- evidence: direct quote or null

Respond ONLY with a valid JSON object containing an array 'results' matching this schema:
{{
  "results": [
    {{
      "id": <message_id>,
      "buyer": false,
      "need": null,
      "budget": null,
      "quantity": null,
      "urgency": "NONE",
      "confidence": 0.0,
      "evidence": null
    }}
  ]
}}"""

        for slot in self.slots:
            key = self._get_key_for_slot(slot)
            if not key and not slot.get("allow_no_key"):
                continue

            if slot["provider"] == "gemini":
                headers = {"Content-Type": "application/json", "x-goog-api-key": key}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.1}
                }
            else:
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                if slot["provider"] == "openrouter":
                    headers["HTTP-Referer"] = "https://github.com/telegram-bot"
                    headers["X-Title"] = "Telegram RAW-LLM Classifier"

                payload = {
                    "model": slot["model"],
                    "max_tokens": min(slot["max_tokens"] * 4, 1000),
                    "temperature": slot["temperature"],
                    "messages": [
                        {"role": "system", "content": "You are a concise JSON-only batch buyer lead qualification assistant."},
                        {"role": "user", "content": prompt}
                    ]
                }

            try:
                resp = requests.post(slot["api_url"], headers=headers, json=payload, timeout=self.timeout * 2)
                if resp.status_code == 200:
                    data = resp.json()
                    if slot["provider"] == "gemini":
                        content = data["candidates"][0]["content"]["parts"][0]["text"]
                    else:
                        content = data["choices"][0]["message"]["content"]
                    parsed = extract_json(content)
                    return parsed.get("results", [])
            except Exception:
                continue

        # Fallback to individual message analysis if batch fails
        return [self.analyze_message(m.get("text", "")) for m in messages]

    def print_status(self):
        """Print full 20-slot diagnostic readiness matrix."""
        print("=" * 70)
        print("🏛️ 20-TIER FALLBACK CASCADE ARCHITECTURE STATUS")
        print("=" * 70)
        for s in self.slots:
            k = self._get_key_for_slot(s)
            status = "🟢 CONFIGURED & READY" if k else ("🔵 LOCAL / KEYLESS" if s.get("allow_no_key") else "⚪ KEY NOT SET")
            print(f"Slot {s['id']:02d} | [{s['provider'].upper()}] {s['name'][:36]:<36} | {status}")
        print("=" * 70)
