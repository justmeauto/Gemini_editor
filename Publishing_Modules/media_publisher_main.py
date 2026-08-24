"""
media_publisher_main.py — Phase 4 Standalone Multi-Platform Publishing Orchestrator
=====================================================================================
Orchestrates sequential 4-platform broadcasting for approved master video reels:
  1. YouTube Shorts   (via uploader.py)
  2. Instagram Reels  (via meta_uploader.py)
  3. TikTok Creator   (via tiktok_uploader.py)
  4. Telegram Channel (via Bot API)

Called directly after a user approves a reel title or taps 'Post Immediately' in Telegram.
"""

import os
import sys
import time
import json
import logging
import asyncio
from typing import Dict, Any, Optional

logger = logging.getLogger("media_publisher_main")
logger.setLevel(logging.INFO)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# If we're in "simpler update", go up one more level to get to actual AMTCE root
if os.path.basename(_REPO_ROOT) == "simpler update":
    _REPO_ROOT = os.path.dirname(_REPO_ROOT)
    
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)
    load_dotenv(os.path.join(_REPO_ROOT, "Credentials", ".env"), override=False)
except ImportError:
    pass


def _resolve_user_credentials(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolves per-user social media credentials.
    If user_id is passed and user is NOT admin and has NOT set their credentials,
    returns None to PREVENT non-admin users from uploading to admin's socials.
    """
    cred_file = os.getenv("CLIENT_SECRET_FILE", os.path.join(_REPO_ROOT, "Credentials", "youtube", "client_secret.json"))
    if not os.path.exists(cred_file):
        fallback_cred = os.path.join(_REPO_ROOT, "Credentials", "client_secret.json")
        if os.path.exists(fallback_cred):
            cred_file = fallback_cred

    token_file = os.getenv("YOUTUBE_TOKEN_FILE", os.path.join(_REPO_ROOT, "Credentials", "youtube", "token.json"))
    if not os.path.exists(token_file):
        fallback_token = os.path.join(_REPO_ROOT, "Credentials", "token.json")
        if os.path.exists(fallback_token):
            token_file = fallback_token

    has_yt = os.path.exists(cred_file) or os.path.exists(token_file) or bool(os.getenv("YOUTUBE_TOKEN_JSON"))

    if not user_id:
        return {
            "meta_token": os.getenv("IG_BUSINESS_TOKEN") or os.getenv("META_PAGE_TOKEN"),
            "meta_id": os.getenv("IG_BUSINESS_ACCOUNT_ID") or os.getenv("IG_BUSINESS_ID") or os.getenv("META_PAGE_ID"),
            "yt_token": token_file if has_yt else None,
            "tiktok_token": os.getenv("TIKTOK_ACCESS_TOKEN"),
            "is_admin": True
        }
    try:
        from Publishing_Modules.telegram_user_manager import load_all_users
        users = load_all_users()
        u_rec = users.get(str(user_id), {})
        admin_id_env = os.getenv("TELEGRAM_ADMIN_ID")
        is_admin = u_rec.get("role") == "admin" or str(user_id) == str(admin_id_env)
        
        meta_tok = u_rec.get("ig_business_token") or u_rec.get("meta_page_token") or (os.getenv("IG_BUSINESS_TOKEN") if is_admin else None)
        meta_id = u_rec.get("ig_business_id") or u_rec.get("meta_page_id") or (os.getenv("IG_BUSINESS_ID") if is_admin else None)
        yt_tok = u_rec.get("youtube_token_json") or (token_file if (has_yt and is_admin) else None)
        tt_tok = u_rec.get("tiktok_access_token") or (os.getenv("TIKTOK_ACCESS_TOKEN") if is_admin else None)
        
        return {
            "meta_token": meta_tok,
            "meta_id": meta_id,
            "yt_token": yt_tok,
            "tiktok_token": tt_tok,
            "is_admin": is_admin
        }
    except Exception:
        return {
            "meta_token": os.getenv("IG_BUSINESS_TOKEN"),
            "meta_id": os.getenv("IG_BUSINESS_ID"),
            "yt_token": token_file if has_yt else None,
            "tiktok_token": os.getenv("TIKTOK_ACCESS_TOKEN"),
            "is_admin": True
        }


def publish_to_youtube(video_path: str, title: str, description: str = "", tags: str = "", niche: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Uploads video reel to YouTube Shorts via YouTube Data API v3."""
    logger.info("🔴 [PUBLISHER 1/4] Uploading to YouTube Shorts...")
    try:
        user_creds = _resolve_user_credentials(user_id)
        if user_id and not user_creds.get("is_admin") and not user_creds.get("yt_token"):
            logger.warning("⚠️ YouTube API credentials missing for non-admin User %s. Skipping YouTube upload.", user_id)
            return {"status": "skipped", "message": "Personal YouTube credentials not configured for non-admin user"}

        from Publishing_Modules.uploader import _upload_sync
        video_id = _upload_sync(
            file_path=video_path,
            title=title,
            description=description or f"{title}\n\n#shorts #viral #trending",
            hashtags=tags or "#shorts #viral #trending",
            privacy="public",
            niche=niche
        )
        if video_id:
            logger.info("✅ [YOUTUBE SUCCESS] Video ID: %s", video_id)
            clean_url = video_id if str(video_id).startswith("http") else f"https://youtu.be/{video_id}"
            return {"status": "success", "video_id": video_id, "url": clean_url}
        else:
            logger.warning("⚠️ YouTube upload completed without returning Video ID.")
            return {"status": "failed", "message": "No video ID returned (possible rate limit or lock)"}
    except Exception as e:
        logger.error("❌ [YOUTUBE ERROR] %s", e)
        return {"status": "failed", "error": str(e)}


async def publish_to_meta(video_path: str, caption: str, niche: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Uploads video reel to Instagram & Facebook via Meta Graph API."""
    logger.info("📸 [PUBLISHER 2/4] Uploading to Meta (Instagram & Facebook Reels)...")
    try:
        from Publishing_Modules.meta_uploader import AsyncMetaUploader
        user_creds = _resolve_user_credentials(user_id)
        token = user_creds.get("meta_token")
        ig_user_id = user_creds.get("meta_id")

        if not token or not ig_user_id:
            logger.warning("⚠️ Meta Graph API credentials missing for User %s. Skipping Meta upload.", user_id or "default")
            return {"instagram": {"status": "skipped", "message": "Personal Instagram credentials not configured for user"}, "facebook": {"status": "skipped", "message": "Personal Facebook credentials not configured for user"}}

        uploader = AsyncMetaUploader()
        res = await uploader.upload_to_meta(
            video_path=video_path,
            caption=caption,
            upload_type="Reels",
            niche=niche or "General_Fallback"
        )
        return res
    except Exception as e:
        logger.error("❌ [META ERROR] %s", e)
        return {"instagram": {"status": "failed", "error": str(e)}, "facebook": {"status": "failed", "error": str(e)}}


async def publish_to_instagram(video_path: str, caption: str, niche: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Uploads video reel to Instagram via Meta Graph API."""
    meta_res = await publish_to_meta(video_path, caption, niche, user_id=user_id)
    ig_result = meta_res.get("instagram", {})
    if ig_result.get("status") == "success":
        media_id = ig_result.get("id")
        link = ig_result.get("link") or (f"https://www.instagram.com/p/{media_id}/" if media_id else "Uploaded successfully")
        logger.info("✅ [INSTAGRAM SUCCESS] Media ID: %s, Link: %s", media_id, link)
        return {"status": "success", "media_id": media_id, "link": link, "url": link}
    else:
        logger.warning("⚠️ Instagram upload failed or skipped: %s", ig_result.get("status"))
        return ig_result


async def publish_to_tiktok(video_path: str, title: str, tags: str = "", niche: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Uploads video reel to TikTok Creator account via TikTok Direct Post API."""
    logger.info("🎵 [PUBLISHER 3/4] Uploading to TikTok...")
    try:
        user_creds = _resolve_user_credentials(user_id)
        if not user_creds.get("tiktok_token"):
            logger.warning("⚠️ TikTok credentials missing for User %s. Skipping TikTok upload.", user_id or "default")
            return {"status": "skipped", "message": "Personal TikTok credentials not configured for user"}

        from Publishing_Modules.tiktok_uploader import upload_to_tiktok
        res = await upload_to_tiktok(
            file_path=video_path,
            title=title,
            hashtags=tags or "#viral #shorts #trending",
            niche=niche
        )
        if res.get("status") == "success":
            logger.info("✅ [TIKTOK SUCCESS] Publish ID: %s", res.get("id"))
            return {"status": "success", "publish_id": res.get("id")}
        else:
            logger.warning("⚠️ TikTok upload skipped or failed: %s", res.get("error"))
            return {"status": res.get("status", "failed"), "message": res.get("error")}
    except Exception as e:
        logger.error("❌ [TIKTOK ERROR] %s", e)
        return {"status": "failed", "error": str(e)}


async def publish_to_telegram(video_path: str, title: str, caption: str = "") -> Dict[str, Any]:
    """Dispatches published master reel to Telegram Public Group & Vault Storage Group."""
    logger.info("✈️ [PUBLISHER 4/4] Publishing to Telegram Public Group / Channel...")
    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest
        from telegram.error import TimedOut, NetworkError, RetryAfter
        import asyncio

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        public_group = os.getenv("TELEGRAM_PUBLIC_GROUP_ID")
        storage_group = os.getenv("TELEGRAM_STORAGE_GROUP_ID") or os.getenv("TELEGRAM_STORAGE_CHAT_ID")

        if not bot_token:
            logger.warning("⚠️ Telegram Bot Token missing. Skipping Telegram broadcast.")
            return {"status": "skipped", "message": "Bot token missing"}

        if not public_group:
            logger.warning("⚠️ TELEGRAM_PUBLIC_GROUP_ID not configured in .env / GitHub Secrets. Skipping Public Telegram upload.")
            return {"status": "skipped", "message": "TELEGRAM_PUBLIC_GROUP_ID not configured"}

        # Format public group target ID (@channel_name, -100xxxxxxxxxx, or channel id)
        raw_pub = str(public_group).strip()
        if raw_pub.startswith("-"):
            target_public = int(raw_pub)
        elif raw_pub.isdigit():
            target_public = int(f"-100{raw_pub}") if not raw_pub.startswith("100") else int(f"-{raw_pub}")
        else:
            target_public = raw_pub if raw_pub.startswith("@") else f"@{raw_pub}"

        req = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=60.0,
            pool_timeout=60.0,
            read_timeout=300.0,
            write_timeout=300.0
        )
        bot = Bot(token=bot_token, request=req)
        full_caption = f"🔥 **{title}**\n\n{caption or '#viral #shorts #reels'}"

        async def _send_video_safe(target_cid, file_or_id, text_caption, max_retries=3):
            for attempt in range(1, max_retries + 1):
                try:
                    if isinstance(file_or_id, str):
                        return await bot.send_video(
                            chat_id=target_cid,
                            video=file_or_id,
                            caption=text_caption
                        )
                    else:
                        with open(video_path, "rb") as vf:
                            return await bot.send_video(
                                chat_id=target_cid,
                                video=vf,
                                caption=text_caption
                            )
                except RetryAfter as ra:
                    logger.warning(f"⏳ Rate-limited by Telegram. Sleeping {ra.retry_after}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(ra.retry_after + 1)
                except (TimedOut, NetworkError, TimeoutError) as te:
                    logger.warning(f"🔄 Telegram upload transient timeout/network error (attempt {attempt}/{max_retries}): {te}")
                    if attempt >= max_retries:
                        raise
                    await asyncio.sleep(2 * attempt)
                except Exception as ex:
                    err_txt = str(ex)
                    if "Forbidden" in err_txt or "can't initiate conversation" in err_txt.lower():
                        logger.warning(f"⚠️ Cannot send to chat ID {target_cid}: Bot cannot initiate conversation with user.")
                    raise

        # 1. Upload reel to Public Telegram Group / Channel
        sent_msg = None
        try:
            sent_msg = await _send_video_safe(target_public, None, full_caption)
            logger.info("📢 ✅ [TELEGRAM SUCCESS] Dispatched approved reel to Public Telegram Group (%s)", public_group)
        except Exception as pg_e:
            err_s = str(pg_e)
            if "Chat not found" in err_s or "chat not found" in err_s.lower():
                logger.error("❌ Public Telegram Group upload failed: Chat not found (%s). Make sure the Bot is added as Admin in your public group/channel!", public_group)
                return {"status": "failed", "error": f"Chat not found for {public_group}. Add bot to group as admin!"}
            else:
                logger.error("❌ Public Telegram Group upload failed (%s): %s", public_group, pg_e)
                return {"status": "failed", "error": str(pg_e)}

        cached_file_id = sent_msg.video.file_id if (sent_msg and sent_msg.video) else None

        # 2. Backup to Vault Storage Group if configured and distinct
        if storage_group and str(storage_group).strip() != str(public_group).strip():
            try:
                raw_sg = str(storage_group).strip()
                target_sg = int(raw_sg) if (raw_sg.startswith("-") or raw_sg.isdigit()) else raw_sg
                video_payload = cached_file_id if cached_file_id else None
                await _send_video_safe(
                    target_sg,
                    video_payload,
                    f"📦 **[VAULT PUBLISHED BACKUP]**\n📌 `{title}`\n📁 `{os.path.basename(video_path)}`"
                )
            except Exception as sg_e:
                logger.warning("⚠️ Vault storage group backup warning: %s", sg_e)

        msg_id = sent_msg.message_id if sent_msg else None
        return {"status": "success", "message_id": msg_id, "url": f"Public Group ({public_group})", "link": f"Public Group ({public_group})"}
    except Exception as e:
        logger.error("❌ [TELEGRAM ERROR] %s", e)
        return {"status": "failed", "error": str(e)}


async def run_phase4_publishing_async(
    video_path: str,
    title: str,
    description: str = "",
    tags: str = "#viral #shorts #trending",
    niche: Optional[str] = None
) -> Dict[str, Any]:
    """Async implementation of Phase 4 Multi-Platform Publishing."""
    logger.info("==================================================================")
    logger.info("🚀 [PHASE 4 MEDIA PUBLISHER] Starting Multi-Platform Publishing Workflow")
    logger.info("📌 Reel: %s", os.path.basename(video_path))
    logger.info("📌 Title: '%s'", title)
    logger.info("==================================================================")

    if not os.path.exists(video_path):
        logger.error("❌ [PUBLISHER FAILED] Video file not found: %s", video_path)
        return {"success": False, "error": f"File not found: {video_path}"}

    results = {
        "video_path": video_path,
        "title": title,
        "platforms": {}
    }

    # 1. YouTube Shorts (sync function)
    yt_res = publish_to_youtube(video_path=video_path, title=title, description=description, tags=tags, niche=niche)
    results["platforms"]["youtube"] = yt_res

    # 2. Meta (Instagram Reels & Facebook Reels)
    caption_text = f"{title}\n\n{tags}"
    try:
        meta_res = await publish_to_meta(video_path=video_path, caption=caption_text, niche=niche)
        
        # Instagram
        ig_result = meta_res.get("instagram", {})
        if ig_result.get("status") == "success":
            media_id = ig_result.get("id")
            link = ig_result.get("link") or (f"https://www.instagram.com/p/{media_id}/" if media_id else "Uploaded successfully")
            results["platforms"]["instagram"] = {"status": "success", "media_id": media_id, "link": link, "url": link}
        elif ig_result.get("status") not in ("skipped", None):
            results["platforms"]["instagram"] = {"status": "failed", "response": ig_result}

        # Facebook
        fb_result = meta_res.get("facebook", {})
        if fb_result.get("status") == "success":
            fb_link = fb_result.get("link") or fb_result.get("url") or "Uploaded successfully"
            results["platforms"]["facebook"] = {"status": "success", "link": fb_link, "url": fb_link}
        elif fb_result.get("status") not in ("skipped", "disabled/skipped", None):
            results["platforms"]["facebook"] = {"status": "failed", "response": fb_result}
    except Exception as e:
        logger.error("❌ Meta publish error: %s", e)
        results["platforms"]["instagram"] = {"status": "failed", "error": str(e)}

    # 3. TikTok Creator (Modular Feature Flag: ENABLE_TIKTOK=yes)
    enable_tiktok = os.getenv("ENABLE_TIKTOK", "no").lower() in ("yes", "true", "1")
    if enable_tiktok:
        try:
            tt_res = await publish_to_tiktok(video_path=video_path, title=title, tags=tags, niche=niche)
            results["platforms"]["tiktok"] = tt_res
        except Exception as e:
            logger.error("❌ TikTok publish error: %s", e)
            results["platforms"]["tiktok"] = {"status": "failed", "error": str(e)}

    # 4. Telegram Channel
    try:
        tg_res = await publish_to_telegram(video_path=video_path, title=title, caption=caption_text)
        results["platforms"]["telegram"] = tg_res
    except Exception as e:
        logger.error("❌ Telegram publish error: %s", e)
        results["platforms"]["telegram"] = {"status": "failed", "error": str(e)}

    # Determine overall success
    success_count = sum(1 for p in results["platforms"].values() if p.get("status") == "success")
    total_active_platforms = len(results["platforms"])
    results["success"] = success_count > 0
    results["published_count"] = success_count

    logger.info("==================================================================")
    logger.info("🎉 [PHASE 4 COMPLETE] Published across %d/%d platforms!", success_count, total_active_platforms)
    for p_name, p_data in results["platforms"].items():
        logger.info("   • %s: %s (%s)", p_name.upper(), p_data.get("status"), p_data.get("url") or p_data.get("message") or "ok")
    logger.info("==================================================================")

    return results


def run_phase4_publishing(
    video_path: str,
    title: str,
    description: str = "",
    tags: str = "#viral #shorts #trending",
    niche: Optional[str] = None
) -> Dict[str, Any]:
    """
    Master Orchestration Entry Point for Phase 4 Multi-Platform Publishing.
    Executes sequential 4-platform broadcasting: YouTube -> Instagram -> TikTok -> Telegram.
    Handles running event loops safely.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Event loop is running — execute async coroutine in existing loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                lambda: asyncio.run(
                    run_phase4_publishing_async(
                        video_path=video_path, title=title, description=description, tags=tags, niche=niche
                    )
                )
            )
            return future.result()
    else:
        return asyncio.run(
            run_phase4_publishing_async(
                video_path=video_path, title=title, description=description, tags=tags, niche=niche
            )
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4 Multi-Platform Video Publisher")
    parser.add_argument("video", help="Path to .mp4 video file to publish")
    parser.add_argument("--title", "-t", required=True, help="Video title/caption")
    parser.add_argument("--tags", default="#viral #shorts #trending", help="Hashtags")
    args = parser.parse_args()

    res = run_phase4_publishing(video_path=args.video, title=args.title, tags=args.tags)
    print(json.dumps(res, indent=2))
