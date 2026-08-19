"""
Phase 3 — Step 03: Viral Metadata & Caption Generator
======================================================
Transforms visual context and content_director intelligence into
high-engagement titles, captions, and platform hashtags.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("phase3.step03_metadata_generator")

# Default category hashtag maps
CATEGORY_HASHTAGS = {
    "educational_explainer": ["#learn", "#education", "#tutorial", "#w3schools", "#coding", "#tech", "#shorts"],
    "kids_animation":        ["#cartoon", "#animation", "#3d", "#kids", "#fun", "#viral", "#reels"],
    "tech_review":           ["#tech", "#gadgets", "#review", "#innovation", "#future", "#shorts"],
    "fitness_action":        ["#fitness", "#workout", "#sports", "#energy", "#motivation", "#reels"],
    "nature_travel":         ["#travel", "#nature", "#scenery", "#adventure", "#explore", "#shorts"],
    "fashion_lifestyle":     ["#fashion", "#style", "#lifestyle", "#outfit", "#trend", "#reels"],
    "meme_viral":            ["#funny", "#memes", "#viral", "#comedy", "#lol", "#shorts"],
    "food_cooking":          ["#food", "#recipe", "#cooking", "#delicious", "#chef", "#shorts"],
    "talking_head":          ["#podcast", "#interview", "#talk", "#perspective", "#reels"],
    "music_performance":     ["#music", "#performance", "#live", "#song", "#vibes", "#shorts"],
    "general_content":       ["#viral", "#trending", "#content", "#shorts", "#reels"]
}

def generate_publishing_metadata(intelligence: Dict[str, Any], fallback_title: str = "Viral Short") -> Dict[str, Any]:
    """
    Generate platform-optimized titles, descriptions, and hashtags from intelligence.

    Args:
        intelligence: Full clip intelligence dict.
        fallback_title: Backup title if intelligence lacks hook.

    Returns:
        Dict with title, caption, hashtags, and per-platform payloads.
    """
    cd = intelligence.get("content_director", {})
    intent = intelligence.get("intent", "general_content")
    
    hook = cd.get("engagement_hook", "").strip()
    narrative = cd.get("recommended_narrative", "").replace("_", " ").title()
    visual_event = cd.get("visual_event", "").strip()
    tone = cd.get("tone", "engaging")

    # Title strategy: hook first, fallback to narrative
    if hook and len(hook) > 5:
        title = hook.rstrip(".")
    elif narrative:
        title = f"{narrative} — {fallback_title}"
    else:
        title = fallback_title

    # Capitalize title cleanly
    if len(title) > 80:
        title = title[:77] + "..."

    # Extract hashtags
    base_tags = CATEGORY_HASHTAGS.get(intent, CATEGORY_HASHTAGS["general_content"])
    hashtag_str = " ".join(base_tags)

    # Caption construction
    body_desc = visual_event if visual_event else f"Check out this {intent.replace('_', ' ')} short!"
    caption = f"{title}\n\n{body_desc}\n\n{hashtag_str}"

    logger.info(f"✨ Step 03 Metadata: generated title='{title[:40]}...' intent={intent}")

    return {
        "title": title,
        "caption": caption,
        "hashtags": base_tags,
        "intent": intent,
        "tone": tone,
        "platforms": {
            "youtube_shorts": {
                "title": f"{title} {hashtag_str[:30]}",
                "description": caption,
                "tags": [t.strip("#") for t in base_tags]
            },
            "instagram_reels": {
                "caption": caption
            },
            "tiktok": {
                "title": title,
                "caption": f"{title} {hashtag_str}"
            }
        }
    }
