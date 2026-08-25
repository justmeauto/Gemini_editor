"""
Gemini_Modules/platform_seo_generator.py
=========================================
Platform-Specific SEO Content Generator

Generates optimized titles, hashtags, and descriptions for multiple platforms
(YouTube, Instagram, Facebook, Telegram) using Gemini AI with platform-specific
SEO algorithms and best practices.

Features:
- Platform-specific title optimization (character limits, keyword placement)
- Hashtag generation with platform-specific volume and relevance
- SEO-optimized descriptions with CTAs and engagement triggers
- Cache injection for context preservation
- User approval workflow with edit capability
- Multi-platform batch generation

Author: AMTCE Platform SEO Engine v1.0
"""

import json
import logging
import os
import re
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("platform_seo_generator")

# ── Router Import ─────────────────────────────────────────────────────────────
try:
    from Gemini_Modules.gemini_router_module.gemini_governor import GeminiGovernor
    _router = GeminiGovernor()
    _HAS_ROUTER = True
except ImportError:
    try:
        from gemini_router_module.gemini_governor import GeminiGovernor
        _router = GeminiGovernor()
        _HAS_ROUTER = True
    except ImportError:
        _router = None
        _HAS_ROUTER = False
        logger.warning("⚠️ GeminiGovernor not found. SEO generator will use heuristic fallback.")

# ── Platform SEO Constraints ───────────────────────────────────────────────────
PLATFORM_LIMITS = {
    "youtube": {
        "title_max": 100,
        "description_max": 5000,
        "hashtags_max": 15,
        "hashtag_style": "#",
        "title_emoji": "moderate",
        "description_style": "detailed",
        "cta_style": "subscribe",
    },
    "instagram": {
        "title_max": 2200,  # Caption limit
        "description_max": 2200,
        "hashtags_max": 30,
        "hashtag_style": "#",
        "title_emoji": "high",
        "description_style": "engaging",
        "cta_style": "link",
    },
    "facebook": {
        "title_max": 255,
        "description_max": 63206,
        "hashtags_max": 10,
        "hashtag_style": "#",
        "title_emoji": "moderate",
        "description_style": "conversational",
        "cta_style": "share",
    },
    "telegram": {
        "title_max": 255,
        "description_max": 4096,
        "hashtags_max": 10,
        "hashtag_style": "#",
        "title_emoji": "moderate",
        "description_style": "concise",
        "cta_style": "join",
    },
}

def extract_celebrity_human_name(handle: str) -> str:
    """Extract a human name or clean title from a handle string."""
    if not handle:
        return ""
    clean = handle.strip().lstrip("@")
    clean = re.sub(r"[._\-\d]+", " ", clean).strip()
    words = [w.capitalize() for w in clean.split() if w.lower() not in {"official", "real", "daily", "page", "fp", "club", "fan"}]
    return " ".join(words)

def extract_main_subject_and_context(
    video_context: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    cache: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Analyzes video context, raw metadata (titles, captions, tags), and prior Gemini call caches
    (Forensic perception, Content Director, Visual Vectors, Audio Context) to discover:
    1. main_subject: Primary hero entity, human name, pet name, brand name, or core focal topic
       (e.g., 'Akanksha Puri', 'Mittu', 'Fingers', 'Elon Musk', '2026').
    2. applicable_context: Supporting descriptors, secondary context, action details
       (e.g., 'effortlessly glowing', 'the dog', 'PC cabinet', 'trillionaire', 'new details').
    """
    metadata = metadata or {}
    cache = cache or {}

    # Extract fields from cache
    vc = cache.get("visual_context", {}) if isinstance(cache.get("visual_context"), dict) else {}
    ep = cache.get("editing_plan", {}) if isinstance(cache.get("editing_plan"), dict) else {}
    audio_ctx = cache.get("audio_data", {}).get("context", {}) if isinstance(cache.get("audio_data"), dict) else {}

    raw_caption = metadata.get("raw_caption") or metadata.get("caption") or ""
    source_title = metadata.get("title") or metadata.get("source_title") or ""
    tags = metadata.get("hashtags") or []
    if isinstance(tags, list):
        tags_str = " ".join(tags)
    else:
        tags_str = str(tags)

    detected_entities = vc.get("detected_entities") or cache.get("detected_entities") or []
    if isinstance(detected_entities, str):
        detected_entities = [detected_entities]

    person_name = vc.get("person_name") or cache.get("person_name") or ""
    cached_subject = vc.get("main_subject") or cache.get("main_subject") or ""

    full_text = f"{video_context} {source_title} {raw_caption} {tags_str} {' '.join(detected_entities)}".strip()

    # 1. Main Subject Discovery
    main_subject = ""
    if person_name and "celeb" not in person_name.lower():
        main_subject = person_name
    elif cached_subject and "celeb" not in cached_subject.lower():
        main_subject = cached_subject
    elif detected_entities and len(detected_entities) > 0:
        main_subject = detected_entities[0]

    if not main_subject and full_text:
        # Search for capitalized names/entities (e.g. "Akanksha Puri", "Elon Musk")
        cap_matches = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", full_text)
        if cap_matches:
            main_subject = cap_matches[0]

    if not main_subject and full_text:
        # Match single notable keywords
        words = [w for w in re.findall(r"\b[A-Za-z0-9_]+\b", full_text) if len(w) > 3 and w.lower() not in {"video", "short", "reels", "trending", "viral", "post"}]
        if words:
            main_subject = words[0].title()

    if not main_subject:
        main_subject = "Trending Feature"

    # 2. Applicable Context & Supporting Descriptors
    descriptors = []
    if vc.get("intent"):
        descriptors.append(str(vc.get("intent")).replace("_", " "))
    if vc.get("tone"):
        descriptors.append(str(vc.get("tone")))
    if ep.get("vibe_summary"):
        descriptors.append(str(ep.get("vibe_summary")))

    # Parse snippets from raw caption / source title
    caption_snippets = [s.strip() for s in re.split(r"[\n\r\t,#|.]+", f"{source_title} {raw_caption}") if s.strip() and len(s.strip()) > 3]
    for snip in caption_snippets[:4]:
        if main_subject.lower() not in snip.lower() and snip.lower() not in main_subject.lower():
            descriptors.append(snip)

    applicable_context = ", ".join(dict.fromkeys(descriptors)) if descriptors else "daily inspiration, viral moment"

    return {
        "main_subject": main_subject,
        "applicable_context": applicable_context,
        "raw_caption": raw_caption,
        "source_title": source_title,
        "detected_entities": detected_entities,
        "intent": vc.get("intent", "general"),
        "tone": vc.get("tone", "engaging"),
        "audio_vibe": audio_ctx.get("dominant_emotion", "")
    }

def sanitize_raw_handles_out(text_or_obj: Any, raw_handle: str = "", discovered_subject: str = "") -> Any:
    """
    Sanitizes titles, descriptions, and hashtags to ensure raw account handles/IDs
    (e.g., 'creator_handle', '@username') are stripped out and replaced with
    the real discovered subject/star name or clean hashtags.
    """
    if not raw_handle or len(raw_handle) < 3:
        return text_or_obj

    handle_clean = raw_handle.strip().lstrip("@")
    replacement_text = discovered_subject if (discovered_subject and "celeb" not in discovered_subject.lower()) else ""

    if isinstance(text_or_obj, str):
        text = text_or_obj
        # Remove handle hashtags like #creator_handle
        text = re.sub(rf"#{re.escape(handle_clean)}\b", f"#{replacement_text.replace(' ', '')}" if replacement_text else "#viral", text, flags=re.IGNORECASE)
        # Remove handle mentions like @creator_handle
        text = re.sub(rf"@{re.escape(handle_clean)}\b", replacement_text or "", text, flags=re.IGNORECASE)
        # Remove standalone handle ID string
        text = re.sub(rf"\b{re.escape(handle_clean)}\b", replacement_text or "", text, flags=re.IGNORECASE)
        # Clean up double spaces or dangling punctuation
        text = re.sub(r"\s+", " ", text).strip()
        return text
    elif isinstance(text_or_obj, list):
        return [sanitize_raw_handles_out(item, raw_handle, discovered_subject) for item in text_or_obj if item]
    elif isinstance(text_or_obj, dict):
        return {k: sanitize_raw_handles_out(v, raw_handle, discovered_subject) for k, v in text_or_obj.items()}
    return text_or_obj


# ── SEO Generation Prompt ──────────────────────────────────────────────────────
_SEO_GENERATION_PROMPT = """\
You are an expert social media SEO content creator. Your task is to generate
platform-optimized titles, hashtags, and descriptions for a video based on the
extracted MAIN SUBJECT, APPLICABLE CONTEXT, and PRIOR GEMINI CALL CACHES.

DISCOVERED MAIN SUBJECT (PRIMARY HERO ANCHOR):
{main_subject}

APPLICABLE SECONDARY CONTEXT & DESCRIPTORS:
{applicable_context}

RAW VIDEO METADATA & SOURCE CAPTION:
{raw_metadata}

VIDEO CONTEXT & SCENE SUMMARY:
{video_context}

USER PROVIDED TITLE (if any):
{user_title}

BRAND/CHANNEL INFO:
{brand_info}

PRIOR GEMINI CALL CACHE (Forensic Perception, Audio, Editing Plan):
{cache_context}

CRITICAL RULES (STRICTLY ENFORCED):
1. HERO MAIN SUBJECT FIRST: Always use the DISCOVERED MAIN SUBJECT as the core hero anchor in all platform titles, descriptions, and hashtags (e.g. if main subject is 'Akanksha Puri', write 'Akanksha Puri's Effortless Glow ✨ | Daily Look'; if 'Mittu', write 'Mittu the Dog Steals the Show 🐶'; if 'Fingers', write 'Fingers PC Cabinet Unboxing & Review 🖥️').
2. ZERO REPETITION RULE (STRICT): ABSOLUTELY NO repeating words or phrases within a single title (e.g. NEVER write 'Fashion Style & Lifestyle | Fashion Inspiration' or 'Trending Lookbook | Lookbook 2023'). Every title segment MUST be unique and complementary.
3. WEAVE APPLICABLE DESCRIPTORS: Seamlessly integrate the applicable secondary context (e.g., 'effortlessly glowing', 'the dog', 'PC cabinet', 'trillionaire') to create compelling hooks.
4. NO RAW ACCOUNT HANDLES OR IDS: Never include raw aggregator account handles, channel IDs, or @username in titles or hashtags.
5. DYNAMIC REAL CONTEXT: Use exact metadata details and avoid generic hardcoded titles or outdated years.

Generate SEO-optimized content for the following platforms: {platforms}

For EACH platform, output:
1. **Title**: Optimized for character limits, keyword placement, click-through rate, and ZERO word repetition.
2. **Description**: SEO-optimized with relevant keywords, engaging hooks, and platform-appropriate CTAs.
3. **Hashtags**: Mix of high-volume, medium-volume, and niche hashtags relevant to {main_subject} and {applicable_context}.
4. **SEO Score**: 0-100 rating based on optimization quality.

PLATFORM-SPECIFIC GUIDELINES:

**YouTube**:
- Title: 60-100 chars, main keyword at start, zero repeating words, power words
- Description: First 150 chars crucial for SEO, include keywords naturally
- Hashtags: 3-5 high-volume, 5-10 relevant niche tags
- CTA: Subscribe-focused with channel link
- Emojis: Use sparingly (2-3 max)

**Instagram**:
- Title/Caption: Up to 2200 chars, hook in first line, use line breaks
- Description: Storytelling approach, emotional engagement
- Hashtags: 5-10 high-volume, 10-20 niche, mix of branded tags
- CTA: Link-focused with "Link in bio" or direct URL
- Emojis: High usage for visual appeal

**Facebook**:
- Title: 60-100 chars, curiosity-inducing but not clickbait, zero repeating words
- Description: Conversational tone, ask questions to drive comments
- Hashtags: 3-5 relevant tags
- CTA: Share-focused to boost algorithm reach
- Emojis: Moderate usage

**Telegram**:
- Title: 50-100 chars, direct and informative, zero repeating words
- Description: Concise, value-focused, easy to scan
- Hashtags: 3-5 relevant tags
- CTA: Join group/channel focused
- Emojis: Moderate usage

OUTPUT SCHEMA — return ONLY this JSON, no other text:
{{
  "generated_at": "<ISO timestamp>",
  "platforms": {{
    "youtube": {{
      "title": "<optimized title>",
      "description": "<SEO description>",
      "hashtags": ["#tag1", "#tag2", ...],
      "seo_score": <0-100>,
      "keyword_density": "<analysis>"
    }},
    "instagram": {{
      "title": "<optimized caption>",
      "description": "<engaging description>",
      "hashtags": ["#tag1", "#tag2", ...],
      "seo_score": <0-100>,
      "keyword_density": "<analysis>"
    }},
    "facebook": {{
      "title": "<optimized title>",
      "description": "<conversational description>",
      "hashtags": ["#tag1", "#tag2", ...],
      "seo_score": <0-100>,
      "keyword_density": "<analysis>"
    }},
    "telegram": {{
      "title": "<optimized title>",
      "description": "<concise description>",
      "hashtags": ["#tag1", "#tag2", ...],
      "seo_score": <0-100>,
      "keyword_density": "<analysis>"
    }}
  }},
  "global_keywords": ["<main keyword>", "<secondary keyword>", ...],
  "content_category": "<category>",
  "target_audience": "<audience description>",
  "engagement_prediction": "<high/medium/low with reasoning>"
}}
"""

def _clean_json(text: str) -> str:
    """Extract JSON from Gemini response."""
    if not text:
        return "{}"
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            text = text.replace("```json", "").replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end+1].strip()
    return text.strip()

def _validate_platform_content(platform: str, content: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates and adjusts content based on platform limits.
    Truncates if exceeds limits, adds defaults if missing.
    """
    limits = PLATFORM_LIMITS.get(platform, {})
    validated = {}

    # Title validation
    title = content.get("title", "")
    max_title = limits.get("title_max", 255)
    if len(title) > max_title:
        title = title[:max_title-3] + "..."
    validated["title"] = title or "Untitled Video"

    # Description validation
    description = content.get("description", "")
    max_desc = limits.get("description_max", 5000)
    if len(description) > max_desc:
        description = description[:max_desc-3] + "..."
    validated["description"] = description or "Check out this amazing video!"

    # Hashtag validation
    hashtags = content.get("hashtags", [])
    max_tags = limits.get("hashtags_max", 15)
    if len(hashtags) > max_tags:
        hashtags = hashtags[:max_tags]
    # Ensure hashtags start with #
    hashtags = [tag if tag.startswith("#") else f"#{tag}" for tag in hashtags]
    validated["hashtags"] = hashtags

    # SEO score
    validated["seo_score"] = content.get("seo_score", 75)
    validated["keyword_density"] = content.get("keyword_density", "N/A")

    return validated

def _heuristic_fallback(
    video_context: str,
    user_title: str = "",
    brand_info: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    cache: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Heuristic fallback when Gemini router is unavailable.
    Generates non-repetitive, subject-focused SEO content.
    """
    extracted = extract_main_subject_and_context(video_context, metadata, cache)
    main_subject = extracted["main_subject"]
    applicable = extracted["applicable_context"]

    # Deduplicate words in base title
    if user_title:
        base_title = user_title
    else:
        desc_first = applicable.split(",")[0].strip().title() if applicable else "Daily Special"
        base_title = f"{main_subject} — {desc_first}"

    # Remove any repeated words from base_title
    words_seen = set()
    clean_title_words = []
    for word in base_title.split():
        w_lower = re.sub(r"\W+", "", word).lower()
        if w_lower and w_lower not in words_seen:
            words_seen.add(w_lower)
            clean_title_words.append(word)
    clean_title = " ".join(clean_title_words)

    # Build clean hashtags
    subj_clean_str = re.sub(r"\W+", "", main_subject)
    clean_subj_tag = f"#{subj_clean_str}" if subj_clean_str else "#viral"
    desc_tags = []
    for d in applicable.split(","):
        tag_word = re.sub(r"\W+", "", d.strip())
        if tag_word and len(tag_word) > 2:
            desc_tags.append(f"#{tag_word}")
    tags = list(dict.fromkeys([clean_subj_tag] + desc_tags + ["#viral", "#trending", "#reels", "#shorts"]))[:10]

    platforms = {
        "youtube": {
            "title": clean_title[:100],
            "description": f"{clean_title}\n\n{applicable}\n\nSubscribe for more!",
            "hashtags": tags[:5],
            "seo_score": 75,
            "keyword_density": "subject_heuristic"
        },
        "instagram": {
            "title": f"{clean_title} ✨",
            "description": f"{main_subject} in action! {applicable} 🔥",
            "hashtags": tags[:10],
            "seo_score": 75,
            "keyword_density": "subject_heuristic"
        },
        "facebook": {
            "title": clean_title[:255],
            "description": f"Check out {clean_title}! What do you think? 👇",
            "hashtags": tags[:5],
            "seo_score": 75,
            "keyword_density": "subject_heuristic"
        },
        "telegram": {
            "title": clean_title[:255],
            "description": f"{clean_title}\n\nJoin our channel for more updates! 🔗",
            "hashtags": tags[:5],
            "seo_score": 75,
            "keyword_density": "subject_heuristic"
        }
    }

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "platforms": platforms,
        "global_keywords": [main_subject] + desc_tags[:4],
        "content_category": extracted.get("intent", "general"),
        "target_audience": "general",
        "engagement_prediction": "medium (subject_heuristic)",
        "_source": "heuristic_fallback"
    }

def generate_platform_seo(
    video_context: str,
    user_title: str = "",
    brand_info: str = "",
    platforms: Optional[List[str]] = None,
    cache: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Generate platform-specific SEO content using Gemini AI with Subject Extraction & Cache Injection.

    Args:
        video_context: Description of video content, scene analysis, or transcript
        user_title: User-provided title (optional, will be optimized)
        brand_info: Brand/channel information for personalization
        platforms: List of platforms to generate for (default: all)
        cache: Cached data from previous Gemini calls (Forensic, Audio, Editing Plan)
        metadata: Raw video metadata (titles, captions, hashtags, source info)

    Returns:
        Dict with platform-specific titles, descriptions, hashtags, and SEO scores
    """
    if platforms is None:
        platforms = ["youtube", "instagram", "facebook", "telegram"]

    extracted = extract_main_subject_and_context(video_context, metadata, cache)
    main_subject = extracted["main_subject"]
    applicable_context = extracted["applicable_context"]

    if not _HAS_ROUTER or _router is None:
        logger.warning("⚠️ [PlatformSEO] Gemini router unavailable — using heuristic fallback.")
        result = _heuristic_fallback(video_context, user_title, brand_info, metadata, cache)
        if platforms:
            result["platforms"] = {k: v for k, v in result["platforms"].items() if k in platforms}
        return result

    # Format cache & metadata context for prompt
    cache_context = json.dumps(cache, indent=2) if cache else "No cached context available"
    raw_metadata = json.dumps(metadata, indent=2) if metadata else f"Caption: {extracted['raw_caption']}"

    prompt = _SEO_GENERATION_PROMPT.format(
        main_subject=main_subject,
        applicable_context=applicable_context,
        raw_metadata=raw_metadata,
        video_context=video_context or "Video content not provided",
        user_title=user_title or "No user title provided",
        brand_info=brand_info or "No brand info provided",
        cache_context=cache_context,
        platforms=", ".join(platforms)
    )

    try:
        logger.info(f"🎯 [PlatformSEO] Generating SEO content for subject='{main_subject}' platforms: {', '.join(platforms)}")
        raw_resp = _router.generate(
            task_type="seo_generation",
            prompt=prompt,
            module_name="platform_seo_generator",
            gen_config={"temperature": 0.3},
        )

        if not raw_resp:
            logger.warning("[PlatformSEO] Empty Gemini response — using heuristic fallback.")
            result = _heuristic_fallback(video_context, user_title, brand_info, metadata, cache)
            if platforms:
                result["platforms"] = {k: v for k, v in result["platforms"].items() if k in platforms}
            return result

        clean = _clean_json(raw_resp)
        seo_data = json.loads(clean)

        # Validate and adjust each platform's content
        for platform in platforms:
            if platform in seo_data.get("platforms", {}):
                seo_data["platforms"][platform] = _validate_platform_content(
                    platform,
                    seo_data["platforms"][platform]
                )

        # Filter to requested platforms
        if platforms:
            seo_data["platforms"] = {
                k: v for k, v in seo_data.get("platforms", {}).items() if k in platforms
            }

        # Add metadata
        seo_data.setdefault("generated_at", datetime.utcnow().isoformat())
        seo_data.setdefault("global_keywords", [main_subject])
        seo_data.setdefault("content_category", extracted.get("intent", "general"))
        seo_data.setdefault("target_audience", "general")
        seo_data.setdefault("engagement_prediction", "high")
        seo_data["main_subject"] = main_subject
        seo_data["applicable_context"] = applicable_context
        seo_data["_source"] = "gemini_semantic"

        # Sanitize output to strip raw handle / ID text if present
        raw_h = (metadata or {}).get("creator_handle") or ""
        if not raw_h:
            m_h = re.search(r"(?:creator_handle|raw_handle|Handle):\s*([A-Za-z0-9._]+)", video_context)
            if m_h:
                raw_h = m_h.group(1)
        if raw_h:
            clean_name = extract_celebrity_human_name(raw_h)
            seo_data = sanitize_raw_handles_out(seo_data, raw_h, clean_name or main_subject)

        logger.info(f"✅ [PlatformSEO] Generated SEO content for subject='{main_subject}' across {len(seo_data['platforms'])} platforms")
        return seo_data

    except Exception as e:
        logger.warning(f"[PlatformSEO] Gemini call failed ({e}) — using heuristic fallback.")
        result = _heuristic_fallback(video_context, user_title, brand_info, metadata, cache)
        if platforms:
            result["platforms"] = {k: v for k, v in result["platforms"].items() if k in platforms}
        return result

def approve_and_finalize(
    seo_data: Dict[str, Any],
    approved_title: str,
    platform: str,
    custom_edits: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Finalize SEO content after user approval.
    
    Args:
        seo_data: Original generated SEO data
        approved_title: User-approved title (will be used as base)
        platform: Platform to finalize for
        custom_edits: Optional custom edits to description/hashtags
    
    Returns:
        Finalized platform-specific content ready for publishing
    """
    if platform not in seo_data.get("platforms", {}):
        logger.error(f"❌ [PlatformSEO] Platform {platform} not found in generated data")
        return {}
    
    platform_data = seo_data["platforms"][platform].copy()
    
    # Update with approved title
    platform_data["title"] = approved_title
    
    # Apply custom edits if provided
    if custom_edits:
        if "description" in custom_edits:
            platform_data["description"] = custom_edits["description"]
        if "hashtags" in custom_edits:
            platform_data["hashtags"] = custom_edits["hashtags"]
    
    # Re-validate after edits
    platform_data = _validate_platform_content(platform, platform_data)
    
    # Add approval metadata
    platform_data["approved_at"] = datetime.utcnow().isoformat()
    platform_data["status"] = "approved"
    
    logger.info(f"✅ [PlatformSEO] Finalized {platform} content with title: '{approved_title[:50]}...'")
    return platform_data

def format_for_telegram_preview(seo_data: Dict[str, Any]) -> str:
    """
    Format SEO data for Telegram preview message.
    """
    lines = ["🎯 *SEO Content Preview*\n"]
    
    for platform, data in seo_data.get("platforms", {}).items():
        lines.append(f"\n📱 *{platform.upper()}*")
        lines.append(f"📝 Title: {data.get('title', 'N/A')}")
        lines.append(f"📊 SEO Score: {data.get('seo_score', 'N/A')}/100")
        lines.append(f"🏷️ Hashtags: {' '.join(data.get('hashtags', [])[:5])}")
        if data.get('description'):
            desc_preview = data['description'][:100] + "..." if len(data['description']) > 100 else data['description']
            lines.append(f"📄 Description: {desc_preview}")
    
    if seo_data.get("global_keywords"):
        lines.append(f"\n🔑 Keywords: {', '.join(seo_data['global_keywords'])}")
    
    lines.append(f"\n📈 Engagement Prediction: {seo_data.get('engagement_prediction', 'N/A')}")
    
    return "\n".join(lines)

def extract_cache_for_regeneration(seo_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract relevant data to cache for future regeneration.
    This allows maintaining context across edits.
    """
    return {
        "global_keywords": seo_data.get("global_keywords", []),
        "content_category": seo_data.get("content_category", ""),
        "target_audience": seo_data.get("target_audience", ""),
        "previous_titles": {
            platform: data.get("title", "") 
            for platform, data in seo_data.get("platforms", {}).items()
        },
        "previous_hashtags": {
            platform: data.get("hashtags", []) 
            for platform, data in seo_data.get("platforms", {}).items()
        }
    }
