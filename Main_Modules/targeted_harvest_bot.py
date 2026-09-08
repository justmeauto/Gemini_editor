"""
Main_Modules/targeted_harvest_bot.py
=====================================
Telegram Bot Integration for Targeted Harvest

Provides Telegram bot commands and inline keyboard buttons for:
- Inputting target account IDs
- Selecting time range (today/week/month/year)
- Triggering targeted harvest pipeline
- Reporting progress and results

Author: AMTCE Targeted Harvest Bot v1.0
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("targeted_harvest_bot")

# ── User Session State ───────────────────────────────────────────────────────
_user_sessions = {}  # chat_id -> {"target_ids": [], "time_range": None, "stage": "input"}


def get_user_session(chat_id: int) -> Dict[str, Any]:
    """Get or create user session."""
    if chat_id not in _user_sessions:
        _user_sessions[chat_id] = {
            "target_ids": [],
            "time_range": None,
            "stage": "idle"
        }
    return _user_sessions[chat_id]


def clear_user_session(chat_id: int):
    """Clear user session."""
    if chat_id in _user_sessions:
        del _user_sessions[chat_id]


# ── Telegram Message Handlers ─────────────────────────────────────────────────

async def handle_targeted_harvest_start(update, context):
    """
    Handle /targeted command to start targeted harvest.
    """
    chat_id = update.effective_chat.id
    session = get_user_session(chat_id)
    
    session["stage"] = "input_ids"
    session["target_ids"] = []
    session["time_range"] = None
    
    await update.message.reply_text(
        "🎯 *Targeted Harvest*\n\n"
        "Please send the list of account IDs/handles to harvest.\n"
        "Format: `id1, id2, id3`\n\n"
        "Example: `actress1, actress2`\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )


async def handle_targeted_harvest_input(update, context):
    """
    Handle user input for target IDs.
    """
    chat_id = update.effective_chat.id
    session = get_user_session(chat_id)
    
    if session["stage"] != "input_ids":
        return
    
    text = update.message.text.strip()
    
    # Parse IDs
    ids = [id.strip() for id in text.split(",")]
    ids = [id for id in ids if id]  # Remove empty
    
    if not ids:
        await update.message.reply_text(
            "❌ No valid IDs found. Please try again.\n"
            "Format: `id1, id2, id3`",
            parse_mode="Markdown"
        )
        return
    
    session["target_ids"] = ids
    session["stage"] = "select_range"
    
    # Show time range selection buttons
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [
            InlineKeyboardButton("📅 Today", callback_data="range_today"),
            InlineKeyboardButton("📆 Week", callback_data="range_week")
        ],
        [
            InlineKeyboardButton("🗓️ Month", callback_data="range_month"),
            InlineKeyboardButton("📊 Year", callback_data="range_year")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="range_cancel")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Selected {len(ids)} IDs:\n"
        f"{', '.join(ids)}\n\n"
        "📅 Select time range for scheduling:",
        reply_markup=reply_markup
    )


async def handle_time_range_selection(update, context):
    """
    Handle time range button selection.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    session = get_user_session(chat_id)
    
    if session["stage"] != "select_range":
        await query.answer("Invalid session state")
        return
    
    callback_data = query.data
    
    if callback_data == "range_cancel":
        clear_user_session(chat_id)
        await query.edit_message_text("❌ Targeted harvest cancelled.")
        await query.answer()
        return
    
    time_range = callback_data.replace("range_", "")
    session["time_range"] = time_range
    session["stage"] = "running"
    
    await query.edit_message_text(
        f"🎯 Starting targeted harvest...\n"
        f"IDs: {', '.join(session['target_ids'])}\n"
        f"Range: {time_range}\n\n"
        f"⏳ This may take several minutes..."
    )
    await query.answer()
    
    # Run harvest in background
    asyncio.create_task(run_targeted_harvest_background(chat_id, session))


async def run_targeted_harvest_background(chat_id: int, session: Dict[str, Any]):
    """
    Run targeted harvest in background and report results.
    """
    try:
        from Content_Scraper_Modules.targeted_harvest import TargetedHarvest, format_pipeline_summary
        from telegram import Bot
        
        bot_token = context.bot_data.get("bot_token") if hasattr(context, "bot_data") else None
        if not bot_token:
            import os
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        
        if not bot_token:
            logger.error("❌ No bot token available")
            return
        
        bot = Bot(token=bot_token)
        
        # Create harvester
        harvester = TargetedHarvest(
            target_ids=session["target_ids"],
            time_range=session["time_range"]
        )
        
        # Run pipeline
        results = await harvester.run_full_pipeline()
        
        # Send results
        summary = format_pipeline_summary(results)
        
        await bot.send_message(
            chat_id=chat_id,
            text=summary,
            parse_mode="Markdown"
        )
        
        # Clear session
        clear_user_session(chat_id)
        
    except Exception as e:
        logger.error(f"❌ Error in targeted harvest background: {e}")
        
        try:
            from telegram import Bot
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            bot = Bot(token=bot_token)
            
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Targeted harvest failed: {str(e)}"
            )
        except:
            pass


async def handle_cancel(update, context):
    """
    Handle /cancel command.
    """
    chat_id = update.effective_chat.id
    clear_user_session(chat_id)
    
    await update.message.reply_text("❌ Operation cancelled.")


# ── Command Registration ───────────────────────────────────────────────────────

def register_handlers(application):
    """
    Register targeted harvest handlers with Telegram bot application.
    """
    from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
    
    application.add_handler(CommandHandler("targeted", handle_targeted_harvest_start))
    application.add_handler(CommandHandler("cancel", handle_cancel))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_targeted_harvest_input
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_time_range_selection,
            pattern="^range_"
        )
    )
    
    logger.info("✅ [TARGETED HARVEST BOT] Handlers registered")


# ── Help Text ─────────────────────────────────────────────────────────────────

TARGETED_HARVEST_HELP = """
🎯 *Targeted Harvest Commands*

/targeted - Start targeted harvest workflow
  1. Send list of account IDs (comma-separated)
  2. Select time range (today/week/month/year)
  3. System harvests, processes, and schedules content

/cancel - Cancel current operation

*Example:*
/targeted
actress1, actress2
[Select "Today" button]
"""
