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
platform-optimized titles, hashtags, and descriptions for a video.

VIDEO CONTEXT & RAW CAPTION:
{video_context}

USER PROVIDED TITLE (if any):
{user_title}

BRAND/CHANNEL INFO:
{brand_info}

CACHED CONTEXT (from previous generations - use this to maintain consistency):
{cache_context}

CRITICAL RULES (STRICTLY ENFORCED):
1. DISCOVER THE REAL SHINING STAR / MAIN SUBJECT: Analyze the video context, caption text, and hashtags to identify who or what is the TRUE focal subject of the video (e.g., 'Kiara Advani', 'Disha Patani', 'Shah Rukh Khan', 'Vintage Ferrari', 'Mumbai Street Food').
2. IGNORE AGGREGATOR HANDLES: Source account handles (e.g., 'channel_id', 'aggregator_handle', 'viralreels') are just aggregator IDs — NEVER use them as the person's name or title!
3. NO RAW ACCOUNT HANDLES OR IDS: NEVER include raw account handles or IDs (e.g. 'handle123', 'channel_id', '@username') in any title, description, or hashtag.
4. USE REAL SUBJECT IN TITLES & HASHTAGS: Write high-converting titles, descriptions, and hashtags centered around the REAL discovered subject/star (e.g., 'Kiara Advani Red Saree Look of the Day 🌟' or 'Stunning Look of the Day 🌟').

Generate SEO-optimized content for the following platforms: {platforms}

For EACH platform, output:
1. **Title**: Optimized for character limits, keyword placement, and click-through rate
2. **Description**: SEO-optimized with relevant keywords, engaging hooks, and platform-appropriate CTAs
3. **Hashtags**: Mix of high-volume, medium-volume, and niche hashtags relevant to the content
4. **SEO Score**: 0-100 rating based on optimization quality

PLATFORM-SPECIFIC GUIDELINES:

**YouTube**:
- Title: 60-100 chars, include main keyword at start, use power words
- Description: First 150 chars are crucial for SEO, include keywords naturally
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
- Title: 60-100 chars, curiosity-inducing but not clickbait
- Description: Conversational tone, ask questions to drive comments
- Hashtags: 3-5 relevant tags (Facebook doesn't rely heavily on hashtags)
- CTA: Share-focused to boost algorithm reach
- Emojis: Moderate usage

**Telegram**:
- Title: 50-100 chars, direct and informative
- Description: Concise, value-focused, easy to scan
- Hashtags: 3-5 relevant tags for discoverability
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
  "content_category": "<category: fashion, fitness, lifestyle, etc>",
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

def _heuristic_fallback(video_context: str, user_title: str = "", brand_info: str = "") -> Dict[str, Any]:
    """
    Heuristic fallback when Gemini router is unavailable.
    Generates basic SEO content using keyword extraction and templates.
    """
    # Extract keywords from context
    context_lower = video_context.lower()
    keywords = []
    
    # Common fashion/fitness/lifestyle keywords
    keyword_bank = [
        "fashion", "style", "outfit", "trend", "look", "ootd",
        "fitness", "workout", "gym", "exercise", "health",
        "lifestyle", "daily", "routine", "vlog", "aesthetic",
        "beautiful", "amazing", "stunning", "gorgeous", "inspiration"
    ]
    
    for kw in keyword_bank:
        if kw in context_lower:
            keywords.append(kw)
    
    # Use user title if provided, otherwise generate from keywords
    base_title = user_title if user_title else f"{' '.join(keywords[:3]).title() if keywords else 'Amazing Video'}"
    
    # Generate platform-specific content
    platforms = {}
    
    # YouTube
    platforms["youtube"] = {
        "title": base_title[:100],
        "description": f"Check out this amazing video! {base_title}\n\nSubscribe for more content!",
        "hashtags": [f"#{kw}" for kw in keywords[:5]] if keywords else ["#viral", "#trending", "#fyp"],
        "seo_score": 70,
        "keyword_density": "heuristic"
    }
    
    # Instagram
    platforms["instagram"] = {
        "title": f"{base_title} ✨\n\nDouble tap if you love this! ❤️",
        "description": f"{' '.join(keywords[:2]).title() if keywords else 'Lifestyle'} content you don't want to miss! 🔥",
        "hashtags": [f"#{kw}" for kw in keywords[:10]] if keywords else ["#reels", "#viral", "#explore", "#fyp", "#trending"],
        "seo_score": 70,
        "keyword_density": "heuristic"
    }
    
    # Facebook
    platforms["facebook"] = {
        "title": base_title[:255],
        "description": f"What do you think about this? {base_title}\n\nShare your thoughts in the comments! 👇",
        "hashtags": [f"#{kw}" for kw in keywords[:5]] if keywords else ["#viral", "#trending"],
        "seo_score": 70,
        "keyword_density": "heuristic"
    }
    
    # Telegram
    platforms["telegram"] = {
        "title": base_title[:255],
        "description": f"{base_title}\n\nJoin our channel for more! 🔗",
        "hashtags": [f"#{kw}" for kw in keywords[:5]] if keywords else ["#video", "#content"],
        "seo_score": 70,
        "keyword_density": "heuristic"
    }
    
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "platforms": platforms,
        "global_keywords": keywords[:5] if keywords else ["viral", "content"],
        "content_category": "general",
        "target_audience": "general",
        "engagement_prediction": "medium (heuristic)",
        "_source": "heuristic_fallback"
    }

def generate_platform_seo(
    video_context: str,
    user_title: str = "",
    brand_info: str = "",
    platforms: Optional[List[str]] = None,
    cache: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Generate platform-specific SEO content using Gemini AI.
    
    Args:
        video_context: Description of video content, scene analysis, or transcript
        user_title: User-provided title (optional, will be optimized)
        brand_info: Brand/channel information for personalization
        platforms: List of platforms to generate for (default: all)
        cache: Cached data from previous generations for context
    
    Returns:
        Dict with platform-specific titles, descriptions, hashtags, and SEO scores
    """
    if platforms is None:
        platforms = ["youtube", "instagram", "facebook", "telegram"]
    
    if not _HAS_ROUTER or _router is None:
        logger.warning("⚠️ [PlatformSEO] Gemini router unavailable — using heuristic fallback.")
        result = _heuristic_fallback(video_context, user_title, brand_info)
        # Filter to requested platforms
        if platforms:
            result["platforms"] = {k: v for k, v in result["platforms"].items() if k in platforms}
        return result
    
    # Format cache context for prompt
    cache_context = ""
    if cache:
        cache_context = json.dumps(cache, indent=2)
    
    prompt = _SEO_GENERATION_PROMPT.format(
        video_context=video_context or "Video content not provided",
        user_title=user_title or "No user title provided",
        brand_info=brand_info or "No brand info provided",
        cache_context=cache_context or "No cached context available",
        platforms=", ".join(platforms)
    )
    
    try:
        logger.info(f"🎯 [PlatformSEO] Generating SEO content for: {', '.join(platforms)}")
        raw_resp = _router.generate(
            task_type="seo_generation",
            prompt=prompt,
            module_name="platform_seo_generator",
            gen_config={"temperature": 0.3},  # Low temp for consistent SEO
        )
        
        if not raw_resp:
            logger.warning("[PlatformSEO] Empty Gemini response — using heuristic fallback.")
            result = _heuristic_fallback(video_context, user_title, brand_info)
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
        seo_data.setdefault("global_keywords", [])
        seo_data.setdefault("content_category", "general")
        seo_data.setdefault("target_audience", "general")
        seo_data.setdefault("engagement_prediction", "medium")
        seo_data["_source"] = "gemini_semantic"
        
        # Sanitize output to strip any raw handle / ID text
        if "creator_handle" in video_context or "raw_handle" in video_context:
            m_h = re.search(r"(?:creator_handle|raw_handle|Handle):\s*([A-Za-z0-9._]+)", video_context)
            if m_h:
                raw_h = m_h.group(1)
                clean_name = extract_celebrity_human_name(raw_h)
                seo_data = sanitize_raw_handles_out(seo_data, raw_h, clean_name)

        logger.info(f"✅ [PlatformSEO] Generated SEO content for {len(seo_data['platforms'])} platforms")
        return seo_data
        
    except Exception as e:
        logger.warning(f"[PlatformSEO] Gemini call failed ({e}) — using heuristic fallback.")
        result = _heuristic_fallback(video_context, user_title, brand_info)
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
