"""
Main_Modules/account_selection_bot.py
=====================================
Telegram Bot Integration for Multi-Account Selection

Provides Telegram bot commands and inline keyboard buttons for:
- Adding social media accounts
- Selecting accounts for publishing
- Managing multiple accounts per platform

Author: AMTCE Account Selection Bot v1.0
"""

import logging
import json
from typing import Dict, Any, Optional, List

logger = logging.getLogger("account_selection_bot")

# ── User Session State ───────────────────────────────────────────────────────
_account_sessions = {}  # chat_id -> {"stage": "add_account", "platform": None, "account_name": None}


def get_account_session(chat_id: int) -> Dict[str, Any]:
    """Get or create account session."""
    if chat_id not in _account_sessions:
        _account_sessions[chat_id] = {
            "stage": "idle",
            "platform": None,
            "account_name": None,
            "credentials": {}
        }
    return _account_sessions[chat_id]


def clear_account_session(chat_id: int):
    """Clear account session."""
    if chat_id in _account_sessions:
        del _account_sessions[chat_id]


# ── Telegram Message Handlers ─────────────────────────────────────────────────

async def handle_add_account_start(update, context):
    """
    Handle /addaccount command to start adding a social media account.
    """
    chat_id = update.effective_chat.id
    session = get_account_session(chat_id)
    
    session["stage"] = "select_platform"
    session["platform"] = None
    session["account_name"] = None
    session["credentials"] = {}
    
    # Show platform selection buttons
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [
            InlineKeyboardButton("📺 YouTube", callback_data="add_platform_youtube"),
            InlineKeyboardButton("📷 Instagram", callback_data="add_platform_instagram")
        ],
        [
            InlineKeyboardButton("📘 Facebook", callback_data="add_platform_facebook"),
            InlineKeyboardButton("✈️ Telegram", callback_data="add_platform_telegram")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="add_platform_cancel")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📱 *Add Social Media Account*\n\n"
        "Select platform:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def handle_platform_selection(update, context):
    """
    Handle platform selection button.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    session = get_account_session(chat_id)
    
    if session["stage"] != "select_platform":
        await query.answer("Invalid session state")
        return
    
    callback_data = query.data
    
    if callback_data == "add_platform_cancel":
        clear_account_session(chat_id)
        await query.edit_message_text("❌ Account addition cancelled.")
        await query.answer()
        return
    
    platform = callback_data.replace("add_platform_", "")
    session["platform"] = platform
    session["stage"] = "input_account_name"
    
    await query.edit_message_text(
        f"📱 Selected: {platform.capitalize()}\n\n"
        "Please send the account name (display name):\n"
        "Example: `My Main Channel`",
        parse_mode="Markdown"
    )
    await query.answer()


async def handle_account_name_input(update, context):
    """
    Handle account name input.
    """
    chat_id = update.effective_chat.id
    session = get_account_session(chat_id)
    
    if session["stage"] != "input_account_name":
        return
    
    account_name = update.message.text.strip()
    
    if not account_name:
        await update.message.reply_text("❌ Account name cannot be empty. Please try again.")
        return
    
    session["account_name"] = account_name
    session["stage"] = "input_credentials"
    
    platform = session["platform"]
    
    # Get required credentials for this platform
    from Core_Modules.user_credential_manager import PLATFORM_ACCOUNT_CREDENTIALS
    required_creds = PLATFORM_ACCOUNT_CREDENTIALS.get(platform, [])
    
    await update.message.reply_text(
        f"✅ Account name: {account_name}\n\n"
        f"📋 Required credentials for {platform.capitalize()}:\n"
        f"{', '.join(required_creds)}\n\n"
        f"Please send credentials in format:\n"
        f"`credential_type:value`\n\n"
        f"Example:\n"
        f"`youtube_client_secret:your_secret`\n"
        f"`youtube_token_json:your_token`\n\n"
        f"Send /done when finished.",
        parse_mode="Markdown"
    )


async def handle_credential_input(update, context):
    """
    Handle credential input.
    """
    chat_id = update.effective_chat.id
    session = get_account_session(chat_id)
    
    if session["stage"] != "input_credentials":
        return
    
    text = update.message.text.strip()
    
    if text == "/done":
        # Save the account
        await save_account(update, context)
        return
    
    # Parse credential input
    if ":" not in text:
        await update.message.reply_text(
            "❌ Invalid format. Use: `credential_type:value`",
            parse_mode="Markdown"
        )
        return
    
    cred_type, cred_value = text.split(":", 1)
    cred_type = cred_type.strip()
    cred_value = cred_value.strip()
    
    session["credentials"][cred_type] = cred_value
    
    await update.message.reply_text(
        f"✅ Added: {cred_type}\n\n"
        f"Current credentials: {list(session['credentials'].keys())}\n\n"
        f"Send more credentials or /done to finish.",
        parse_mode="Markdown"
    )


async def save_account(update, context):
    """
    Save the account to user credentials.
    """
    chat_id = update.effective_chat.id
    session = get_account_session(chat_id)
    
    user_id = get_user_id_from_chat(chat_id)
    if not user_id:
        await update.message.reply_text("❌ User not authenticated. Please login first.")
        clear_account_session(chat_id)
        return
    
    platform = session["platform"]
    account_name = session["account_name"]
    credentials = session["credentials"]
    
    from Core_Modules.user_credential_manager import add_user_social_account
    
    result = add_user_social_account(user_id, platform, account_name, credentials)
    
    if result.get("success"):
        await update.message.reply_text(
            f"✅ Successfully added {platform.capitalize()} account '{account_name}'!\n\n"
            f"You can now select this account when publishing.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ Failed to add account: {result.get('error')}",
            parse_mode="Markdown"
        )
    
    clear_account_session(chat_id)


async def handle_list_accounts(update, context):
    """
    Handle /listaccounts command to list user's social media accounts.
    """
    chat_id = update.effective_chat.id
    
    user_id = get_user_id_from_chat(chat_id)
    if not user_id:
        await update.message.reply_text("❌ User not authenticated. Please login first.")
        return
    
    from Core_Modules.user_credential_manager import get_user_social_accounts
    
    result = get_user_social_accounts(user_id)
    
    if not result.get("success"):
        await update.message.reply_text(f"❌ Error: {result.get('error')}")
        return
    
    accounts = result.get("accounts", {})
    
    if not accounts:
        await update.message.reply_text("📱 No social media accounts added yet.\n\nUse /addaccount to add one.")
        return
    
    lines = ["📱 *Your Social Media Accounts*\n"]
    
    for platform, platform_accounts in accounts.items():
        lines.append(f"\n📺 {platform.capitalize()}:")
        for account_name in platform_accounts.keys():
            lines.append(f"  • {account_name}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_select_account_for_publish(update, context, platform: str):
    """
    Show account selection keyboard for publishing.
    """
    chat_id = update.effective_chat.id
    
    user_id = get_user_id_from_chat(chat_id)
    if not user_id:
        await update.message.reply_text("❌ User not authenticated. Please login first.")
        return
    
    from Core_Modules.user_credential_manager import get_user_social_accounts, format_account_selection_keyboard
    
    result = get_user_social_accounts(user_id, platform)
    
    if not result.get("success"):
        await update.message.reply_text(f"❌ Error: {result.get('error')}")
        return
    
    platform_accounts = result.get("accounts", {}).get(platform, {})
    
    if not platform_accounts:
        await update.message.reply_text(
            f"❌ No {platform.capitalize()} accounts found.\n\n"
            f"Use /addaccount to add one first.",
            parse_mode="Markdown"
        )
        return
    
    keyboard_json = format_account_selection_keyboard(user_id, platform)
    
    if not keyboard_json:
        await update.message.reply_text("❌ Error creating account selection.")
        return
    
    from telegram import InlineKeyboardMarkup
    
    reply_markup = InlineKeyboardMarkup.from_json(keyboard_json)
    
    await update.message.reply_text(
        f"📱 Select {platform.capitalize()} account for publishing:",
        reply_markup=reply_markup
    )


async def handle_account_selection_callback(update, context):
    """
    Handle account selection callback.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    callback_data = query.data
    
    if callback_data == "select_account_cancel":
        await query.edit_message_text("❌ Account selection cancelled.")
        await query.answer()
        return
    
    # Parse: select_account_{platform}_{account_name}
    parts = callback_data.split("_")
    if len(parts) < 4:
        await query.answer("Invalid selection")
        return
    
    platform = parts[2]
    account_name = "_".join(parts[3:])  # Handle account names with underscores
    
    user_id = get_user_id_from_chat(chat_id)
    
    # Store selected account for publishing
    if hasattr(context, "user_data"):
        if not context.user_data.get("selected_accounts"):
            context.user_data["selected_accounts"] = {}
        context.user_data["selected_accounts"][platform] = account_name
    
    await query.edit_message_text(
        f"✅ Selected {platform.capitalize()} account: {account_name}\n\n"
        f"This account will be used for publishing.",
        parse_mode="Markdown"
    )
    await query.answer()


def get_user_id_from_chat(chat_id: int) -> Optional[str]:
    """
    Get user_id from Telegram chat_id.
    """
    try:
        from Core_Modules.user_credential_manager import get_user_by_telegram_chat
        user_data = get_user_by_telegram_chat(str(chat_id))
        return user_data.get("user_id") if user_data else None
    except:
        return None


# ── Command Registration ───────────────────────────────────────────────────────

def register_handlers(application):
    """
    Register account selection handlers with Telegram bot application.
    """
    from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
    
    application.add_handler(CommandHandler("addaccount", handle_add_account_start))
    application.add_handler(CommandHandler("listaccounts", handle_list_accounts))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_account_name_input
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_platform_selection,
            pattern="^add_platform_"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_account_selection_callback,
            pattern="^select_account_"
        )
    )
    
    logger.info("✅ [ACCOUNT SELECTION BOT] Handlers registered")


# ── Help Text ─────────────────────────────────────────────────────────────────

ACCOUNT_SELECTION_HELP = """
📱 *Multi-Account Commands*

/addaccount - Add a social media account
  1. Select platform (YouTube, Instagram, Facebook, Telegram)
  2. Enter account name (display name)
  3. Provide required credentials
  4. Account saved for future use

/listaccounts - List all your social media accounts

*Example:*
/addaccount
[Select "YouTube"]
My Main Channel
youtube_client_secret:xxx
youtube_token_json:yyy
/done
"""
