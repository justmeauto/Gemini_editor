"""
Tools/Gemini_Modules/gemini_router_module/list_models.py — Dynamic Gemini Model Discovery & Categorization Engine
===================================================================================================================
Google Gemini AI System Architecture Specification (2026 Production Ready).

Features:
1. Queries live Google GenAI API (`client.models.list()`) using modern `google.genai` SDK (with fallback to `google.generativeai`).
2. Filters out non-generative endpoints (embeddings, imagen, aqa, bison).
3. Evaluates and scores discovered models across 10 task categories:
   - creative, reasoning, cheap, master, watermark, vision, caption, narrative, price, analysis.
4. Generates a clean, flat list of active production models sorted by capability tier.
5. Performs atomic file persistence to `storage/gemini_models_cache.json`.
6. Enforces a 5-minute cooldown timer between dynamic refresh attempts to prevent API quota storms on 404 error loops.
"""

import os
import re
import json
import time
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("list_models")

def _find_project_root() -> str:
    """Find project root by looking for common root indicators, or fallback to sensible parent/cwd."""
    env_root = os.getenv("PROJECT_ROOT") or os.getenv("REPO_ROOT")
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_markers = {".git", "Credentials", "storage", "pyproject.toml", "setup.py", ".env"}
    
    ptr = current_dir
    while True:
        try:
            entries = set(os.listdir(ptr))
            if entries & root_markers:
                return ptr
        except Exception:
            pass
        parent = os.path.dirname(ptr)
        if parent == ptr:
            break
        ptr = parent

    if os.path.exists(os.path.join(os.getcwd(), "storage")) or os.path.exists(os.path.join(os.getcwd(), ".env")):
        return os.getcwd()
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_cache_file_path() -> str:
    if os.getenv("GEMINI_CACHE_FILE"):
        return os.path.abspath(os.getenv("GEMINI_CACHE_FILE"))
    
    root = _find_project_root()
    storage_dir = os.path.join(root, "storage")
    if not os.path.exists(storage_dir):
        cwd_storage = os.path.join(os.getcwd(), "storage")
        if os.path.exists(cwd_storage):
            storage_dir = cwd_storage
        else:
            try:
                os.makedirs(storage_dir, exist_ok=True)
            except Exception:
                storage_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")
    return os.path.join(storage_dir, "gemini_models_cache.json")


_REPO_ROOT = _find_project_root()
CACHE_FILE = _get_cache_file_path()
REFRESH_COOLDOWN_SECONDS = 300  # 5-minute throttling between API scans

# Early dotenv loading for GEMINI_API_KEY
try:
    from dotenv import load_dotenv
    # 1. Standard search
    load_dotenv(override=False)
    # 2. Check candidate locations
    _search_dirs = [
        _REPO_ROOT,
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]
    for _d in _search_dirs:
        for _f in [".env", os.path.join("Credentials", ".env")]:
            _epath = os.path.normpath(os.path.join(_d, _f))
            if os.path.exists(_epath):
                load_dotenv(_epath, override=False)
except ImportError:
    pass

_LAST_REFRESH_TIME = 0.0


# ── Hardcoded Baseline Default Task Matrix (Fallback) ─────────────────────────

DEFAULT_TASK_MODEL_RATINGS = {
    "creative": {
        "gemini-3.8-flash-lite": 4.3,
        "gemini-3.5-flash-lite": 4.0,
        "gemini-3.8-flash": 4.0,
        "gemini-3.5-flash": 3.7,
        "gemini-2.5-flash-lite": 3.0,
        "gemini-flash-lite-latest": 2.9,
        "gemini-2.0-flash-lite": 2.8,
        "gemini-2.5-flash": 2.7,
        "gemini-2.0-flash": 2.6,
        "gemini-flash-latest": 2.5,
    },
    "reasoning": {
        "gemini-3.8-flash": 5.1,
        "gemini-3.5-flash": 4.8,
        "gemini-3.8-flash-lite": 4.1,
        "gemini-3.5-flash-lite": 3.8,
        "gemini-2.5-flash": 3.8,
        "gemini-2.0-flash": 3.6,
        "gemini-flash-latest": 3.5,
        "gemini-2.5-flash-lite": 2.8,
        "gemini-2.0-flash-lite": 2.7,
        "gemini-flash-lite-latest": 2.2,
    },
    "cheap": {
        "gemini-3.8-flash-lite": 5.2,
        "gemini-3.5-flash-lite": 4.9,
        "gemini-2.5-flash-lite": 3.9,
        "gemini-flash-lite-latest": 3.8,
        "gemini-2.0-flash-lite": 3.7,
        "gemini-2.0-flash-lite-001": 3.6,
        "gemini-3.8-flash": 3.3,
        "gemini-3.5-flash": 3.0,
        "gemini-2.5-flash": 2.0,
        "gemini-2.0-flash": 1.8,
    },
    "master": {
        "gemini-3.8-flash": 4.5,
        "gemini-3.5-flash": 4.2,
        "gemini-3.8-flash-lite": 4.0,
        "gemini-3.5-flash-lite": 3.7,
        "gemini-2.5-flash": 3.2,
        "gemini-2.0-flash": 3.0,
        "gemini-flash-latest": 2.9,
        "gemini-2.5-flash-lite": 2.7,
        "gemini-2.0-flash-lite": 2.6,
    },
    "watermark": {
        "gemini-3.8-flash": 5.3,
        "gemini-3.5-flash": 5.0,
        "gemini-2.5-flash": 4.0,
        "gemini-3.8-flash-lite": 3.8,
        "gemini-2.0-flash": 3.8,
        "gemini-flash-latest": 3.6,
        "gemini-3.5-flash-lite": 3.5,
        "gemini-2.5-flash-lite": 2.5,
        "gemini-2.0-flash-lite": 2.4,
    },
    "vision": {
        "gemini-3.8-flash": 5.1,
        "gemini-3.5-flash": 4.8,
        "gemini-3.8-flash-lite": 4.1,
        "gemini-2.5-flash": 3.8,
        "gemini-3.5-flash-lite": 3.8,
        "gemini-2.0-flash": 3.6,
        "gemini-flash-latest": 3.5,
        "gemini-2.5-flash-lite": 2.6,
        "gemini-2.0-flash-lite": 2.5,
    },
    "caption": {
        "gemini-3.8-flash": 4.8,
        "gemini-3.5-flash": 4.5,
        "gemini-3.8-flash-lite": 4.3,
        "gemini-3.5-flash-lite": 4.0,
        "gemini-2.5-flash": 3.5,
        "gemini-2.0-flash": 3.3,
        "gemini-flash-latest": 3.2,
        "gemini-2.5-flash-lite": 3.0,
        "gemini-2.0-flash-lite": 2.9,
    },
    "narrative": {
        "gemini-3.8-flash": 4.6,
        "gemini-3.5-flash": 4.3,
        "gemini-3.8-flash-lite": 4.1,
        "gemini-3.5-flash-lite": 3.8,
        "gemini-2.5-flash": 3.3,
        "gemini-2.0-flash": 3.1,
        "gemini-flash-latest": 3.0,
        "gemini-2.5-flash-lite": 2.8,
        "gemini-2.0-flash-lite": 2.7,
    },
    "price": {
        "gemini-3.8-flash-lite": 5.1,
        "gemini-3.5-flash-lite": 4.8,
        "gemini-3.8-flash": 4.6,
        "gemini-3.5-flash": 4.3,
        "gemini-2.5-flash-lite": 3.8,
        "gemini-2.0-flash-lite": 3.7,
        "gemini-flash-lite-latest": 3.6,
        "gemini-2.5-flash": 3.3,
        "gemini-2.0-flash": 3.1,
    },
    "analysis": {
        "gemini-3.8-flash-lite": 5.0,
        "gemini-3.8-flash": 4.7,
        "gemini-3.5-flash-lite": 4.7,
        "gemini-3.5-flash": 4.4,
        "gemini-2.5-flash-lite": 3.7,
        "gemini-2.0-flash-lite": 3.6,
        "gemini-2.5-flash": 3.4,
        "gemini-2.0-flash": 3.2,
        "gemini-flash-latest": 3.0,
    },
}

DEFAULT_MODELS_LIST = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


# ── Dynamic Model Discovery Engine ───────────────────────────────────────────

def discover_api_models(api_key: str = "") -> List[str]:
    """
    Queries Google GenAI API for currently active models.
    Filters out non-generative models (embedding, imagen, bison, aqa).
    """
    key = api_key or os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        logger.warning("⚠️ No API key found for model discovery — returning default list.")
        return DEFAULT_MODELS_LIST

    discovered = []

    # 1. Try modern google.genai SDK
    try:
        from google import genai
        client = genai.Client(api_key=key)
        all_models = list(client.models.list())
        for m in all_models:
            name = getattr(m, "name", "") or getattr(m, "display_name", "")
            name = name.replace("models/", "").strip()
            actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None)
            if actions and "generateContent" not in actions:
                continue
            if _is_valid_generative_model(name):
                discovered.append(name)
        if discovered:
            logger.info(f"✅ Discovered {len(discovered)} active Gemini models via google.genai SDK.")
            return _sort_models_by_tier(discovered)
    except Exception as e:
        logger.debug(f"google.genai models.list failed: {e}")

    # 2. Fallback to google.generativeai legacy SDK
    try:
        import google.generativeai as genai_legacy
        genai_legacy.configure(api_key=key)
        all_models = list(genai_legacy.list_models())
        for m in all_models:
            name = getattr(m, "name", "").replace("models/", "").strip()
            supported_methods = getattr(m, "supported_generation_methods", [])
            if "generateContent" in supported_methods and _is_valid_generative_model(name):
                discovered.append(name)
        if discovered:
            logger.info(f"✅ Discovered {len(discovered)} active Gemini models via google.generativeai SDK.")
            return _sort_models_by_tier(discovered)
    except Exception as e:
        logger.debug(f"google.generativeai list_models failed: {e}")

    # 3. Fallback to direct Google GenAI REST API (zero external SDK dependencies)
    try:
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "AMTCE-Model-Discovery/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            raw_models = data.get("models", [])
            for m in raw_models:
                name = m.get("name", "").replace("models/", "").strip()
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and _is_valid_generative_model(name):
                    discovered.append(name)
            if discovered:
                logger.info(f"✅ Discovered {len(discovered)} active Gemini models via direct REST API.")
                return _sort_models_by_tier(discovered)
    except Exception as e:
        logger.debug(f"Direct REST API list_models failed: {e}")

    return DEFAULT_MODELS_LIST


def _is_valid_generative_model(model_name: str) -> bool:
    """Filters out embeddings, audio-only, imagen, WebSocket bidi, and non-gemini endpoints."""
    name = model_name.lower()
    if not name.startswith("gemini"):
        return False
    # Exclude non-generative, audio-only, or experimental preview endpoints
    excluded_keywords = [
        "embedding", "embed", "imagen", "image", "preview-image", "bison", "aqa", "gecko", "text-001",
        "tts", "preview-tts", "customtools", "transcribe", "robotics",
        "computer-use", "live-translate", "thinking", "preview",
        "audio", "native-audio", "bidi", "realtime", "omni"
    ]
    for kw in excluded_keywords:
        if kw in name:
            return False
    return True


def _sort_models_by_tier(models: List[str]) -> List[str]:
    """Sorts discovered models into Flash -> Lite -> Other -> Pro tiers, prioritizing higher version numbers."""
    def _version_key(name: str) -> float:
        nl = name.lower()
        if "3.8" in nl or "3-8" in nl:
            return 3.8
        if "3.5" in nl or "3-5" in nl:
            return 3.5
        if "3." in nl or "3-" in nl:
            return 3.0
        if "2.5" in nl or "2-5" in nl:
            return 2.5
        if "2.0" in nl or "2-" in nl:
            return 2.0
        if "latest" in nl:
            return 2.4
        return 1.0

    flash_models = sorted([m for m in models if "flash" in m.lower() and "lite" not in m.lower()], key=_version_key, reverse=True)
    lite_models = sorted([m for m in models if "lite" in m.lower()], key=_version_key, reverse=True)
    other_models = sorted([m for m in models if "pro" not in m.lower() and "flash" not in m.lower() and "lite" not in m.lower()], key=_version_key, reverse=True)
    pro_models = sorted([m for m in models if "pro" in m.lower()], key=_version_key, reverse=True)

    # Free/high-RPM tiers first (Flash -> Lite), with paid/expensive Pro models at the very bottom
    ordered = []
    # Include default known models if discovered
    for m in DEFAULT_MODELS_LIST:
        if m in models and m not in ordered:
            ordered.append(m)
    for m in flash_models + lite_models + other_models + pro_models:
        if m not in ordered:
            ordered.append(m)
    return ordered


def calculate_task_matrix(models: List[str]) -> Dict[str, Dict[str, float]]:
    """
    Computes heuristic category rating matrices for all 10 task categories
    based on model version, speed tier (lite vs flash vs pro), and accuracy characteristics.
    """
    matrix = {task: {} for task in DEFAULT_TASK_MODEL_RATINGS.keys()}

    for m in models:
        m_lower = m.lower()
        
        # Base version score multiplier: prioritize latest generation models (3.8 > 3.5 > 3.0 > 2.5 > 2.0 > 1.5)
        if "3.8" in m_lower or "3-8" in m_lower:
            version_score = 3.8
        elif "3.5" in m_lower or "3-5" in m_lower:
            version_score = 3.5
        elif "3." in m_lower or "3-" in m_lower:
            version_score = 3.0
        elif "2.5" in m_lower or "2-5" in m_lower:
            version_score = 2.5
        elif "2.0" in m_lower or "2-" in m_lower:
            version_score = 2.0
        else:
            version_score = 1.5

        is_lite = "lite" in m_lower
        is_pro = "pro" in m_lower
        is_flash = "flash" in m_lower and not is_lite

        # 1. creative (Lite highest RPM > Flash > Pro)
        if is_lite:
            matrix["creative"][m] = round(version_score + 0.5, 1)
        elif is_flash:
            matrix["creative"][m] = round(version_score + 0.2, 1)
        else:
            matrix["creative"][m] = 1.5 if is_pro else 1.0

        # 2. reasoning (Production Flash highest for tool calling & multi-turn stability, Lite secondary, Pro fallback)
        if is_flash:
            matrix["reasoning"][m] = round(version_score + 1.3, 1)
        elif is_lite:
            matrix["reasoning"][m] = round(version_score + 0.3, 1)
        else:
            matrix["reasoning"][m] = round(version_score - 0.5, 1)

        # 3. cheap (Lite dominates)
        if is_lite:
            matrix["cheap"][m] = round(version_score + 1.4, 1)
        elif is_flash:
            matrix["cheap"][m] = round(version_score - 0.5, 1)
        else:
            matrix["cheap"][m] = 1.0

        # 4. master (Flash high-quota first, Pro fallback)
        if is_flash:
            matrix["master"][m] = round(version_score + 0.7, 1)
        elif is_lite:
            matrix["master"][m] = round(version_score + 0.2, 1)
        else:
            matrix["master"][m] = 1.7 if is_pro else 1.1

        # 5. watermark (Standard Flash highest 4.0 for forensic vision accuracy)
        if is_flash:
            matrix["watermark"][m] = round(version_score + 1.5, 1)
        elif is_lite:
            matrix["watermark"][m] = round(version_score, 1)
        else:
            matrix["watermark"][m] = 1.5 if is_pro else 1.1

        # 6. vision (Production Flash highest for multi-image stability, Lite secondary)
        if is_flash:
            matrix["vision"][m] = round(version_score + 1.3, 1)
        elif is_lite:
            matrix["vision"][m] = round(version_score + 0.3, 1)
        else:
            matrix["vision"][m] = 1.5 if is_pro else 1.1

        # 7. caption (Flash & Lite)
        if is_flash:
            matrix["caption"][m] = round(version_score + 1.0, 1)
        elif is_lite:
            matrix["caption"][m] = round(version_score + 0.5, 1)
        else:
            matrix["caption"][m] = 1.4 if is_pro else 1.0

        # 8. narrative (Flash & Lite)
        if is_flash:
            matrix["narrative"][m] = round(version_score + 0.8, 1)
        elif is_lite:
            matrix["narrative"][m] = round(version_score + 0.3, 1)
        else:
            matrix["narrative"][m] = 1.6 if is_pro else 1.1

        # 9. price (Lite first)
        if is_lite:
            matrix["price"][m] = round(version_score + 1.3, 1)
        elif is_flash:
            matrix["price"][m] = round(version_score + 0.8, 1)
        else:
            matrix["price"][m] = 1.4 if is_pro else 1.1

        # 10. analysis (Lite & Flash)
        if is_lite:
            matrix["analysis"][m] = round(version_score + 1.2, 1)
        elif is_flash:
            matrix["analysis"][m] = round(version_score + 0.9, 1)
        else:
            matrix["analysis"][m] = 1.5 if is_pro else 1.0

    return matrix


# ── Cache & Refresh Operations ───────────────────────────────────────────────

def refresh_gemini_models_cache(api_key: str = "", force: bool = False) -> dict:
    """
    Refreshes storage/gemini_models_cache.json.
    Enforces a 5-minute cooldown timer unless force=True.
    """
    global _LAST_REFRESH_TIME
    now = time.time()
    
    if not force and (now - _LAST_REFRESH_TIME) < REFRESH_COOLDOWN_SECONDS:
        cached_data = load_cached_models()
        if cached_data:
            return cached_data

    logger.info("🔄 [MODEL REFRESH] Querying Google GenAI API for active Gemini models...")
    discovered_models = discover_api_models(api_key)
    task_ratings = calculate_task_matrix(discovered_models)

    cache_payload = {
        "schema_version": "1.0",
        "updated_at": now,
        "models": discovered_models,
        "task_ratings": task_ratings
    }

    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp_file = CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache_payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, CACHE_FILE)
        _LAST_REFRESH_TIME = now
        logger.info(f"💾 Saved refreshed Gemini model matrix ({len(discovered_models)} models) to {CACHE_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save models cache: {e}")

    return cache_payload


def load_cached_models(max_age_seconds: Optional[float] = None) -> Optional[dict]:
    """
    Loads model cache from storage/gemini_models_cache.json if valid and not expired.
    Default TTL is 24 hours (86,400 seconds), configurable via GEMINI_CACHE_TTL_SECONDS.
    """
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "models" in data and "task_ratings" in data:
                ttl = max_age_seconds if max_age_seconds is not None else float(os.getenv("GEMINI_CACHE_TTL_SECONDS", "86400"))
                updated_at = data.get("updated_at", 0)
                # If cache is older than TTL, treat as expired so it auto-refreshes from live API
                if ttl > 0 and (time.time() - updated_at) > ttl:
                    logger.info(f"⏳ [CACHE EXPIRED] Gemini model cache is older than {ttl/3600:.1f}h. Triggering auto-refresh from live API...")
                    return None
                return data
    except Exception as e:
        logger.warning(f"Failed to read models cache {CACHE_FILE}: {e}")
    return None


def get_active_models_and_ratings(force: bool = False) -> Tuple[List[str], Dict[str, Dict[str, float]]]:
    """
    Returns active models list and task ratings matrix.
    Loads from cache if valid and within TTL, or initiates live API discovery automatically.
    """
    if not force:
        cached = load_cached_models()
        if cached:
            return cached.get("models", DEFAULT_MODELS_LIST), cached.get("task_ratings", DEFAULT_TASK_MODEL_RATINGS)
    
    refreshed = refresh_gemini_models_cache(force=force)
    return refreshed.get("models", DEFAULT_MODELS_LIST), refreshed.get("task_ratings", DEFAULT_TASK_MODEL_RATINGS)


def get_models_by_capability(capability: str = "reasoning") -> List[str]:
    """
    Returns an ordered list of active models sorted by their affinity/score
    for a given capability (e.g. 'reasoning', 'vision', 'watermark', 'creative').
    Filters out experimental 3.x and non-generative endpoints.
    """
    models, ratings = get_active_models_and_ratings()
    cap_ratings = ratings.get(capability, {})
    valid = [m for m in models if _is_valid_generative_model(m)]
    if not cap_ratings:
        return valid
    return sorted(valid, key=lambda m: cap_ratings.get(m, 0.0), reverse=True)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Discovering & Refreshing active Gemini models...")
    res = refresh_gemini_models_cache(force=True)
    print("\nVALID GENERATIVE MODELS LIST (Tier-sorted & filtered):")
    print(json.dumps(res.get("models", []), indent=2))
    
    print("\n📊 CAPABILITY RANKINGS (Top models per task):")
    ranked_summary = {}
    for t in res.get("task_ratings", {}).keys():
        top_m = get_models_by_capability(t)
        ranked_summary[t] = [
            {"model": m, "score": res["task_ratings"][t].get(m, 0.0)}
            for m in top_m
        ]
    print(json.dumps(ranked_summary, indent=2))


