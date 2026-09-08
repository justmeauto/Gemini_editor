"""
Main_Modules/approval_account_selection.py
=========================================
Account Selection Integration for Approval Workflow

Integrates multi-account selection into the approval workflow:
- After user approves content
- Before asking for title/SEO generation
- Shows account selection buttons with account names
- Stores selected accounts for publishing

Author: AMTCE Approval Account Selection v1.0
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("approval_account_selection")


async def show_account_selection_after_approval(
    update,
    context,
    platforms: List[str] = None
) -> Dict[str, Any]:
    """
    Show account selection buttons after user approval.
    
    Args:
        update: Telegram update
        context: Telegram context
        platforms: List of platforms to show (default: all)
    
    Returns:
        Dict with selection status
    """
    if platforms is None:
        platforms = ["youtube", "instagram", "facebook", "telegram"]
    
    chat_id = update.effective_chat.id
    user_id = get_user_id_from_chat(chat_id)
    
    if not user_id:
        await update.message.reply_text(
            "❌ User not authenticated. Please login first.",
            parse_mode="Markdown"
        )
        return {"success": False, "error": "Not authenticated"}
    
    from Core_Modules.user_credential_manager import get_user_social_accounts
    
    # Get user's accounts
    result = get_user_social_accounts(user_id)
    
    if not result.get("success"):
        await update.message.reply_text(
            f"❌ Error loading accounts: {result.get('error')}",
            parse_mode="Markdown"
        )
        return {"success": False, "error": result.get("error")}
    
    accounts = result.get("accounts", {})
    
    # Check which platforms have accounts
    available_platforms = []
    for platform in platforms:
        if accounts.get(platform):
            available_platforms.append(platform)
    
    if not available_platforms:
        await update.message.reply_text(
            "📱 No social media accounts found.\n\n"
            "Use /addaccount to add accounts first.",
            parse_mode="Markdown"
        )
        return {"success": False, "error": "No accounts"}
    
    # Create inline keyboard with account options
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = []
    
    for platform in available_platforms:
        platform_accounts = accounts[platform]
        for account_name in platform_accounts.keys():
            keyboard.append([
                InlineKeyboardButton(
                    f"📱 {platform.capitalize()}: {account_name}",
                    callback_data=f"approve_select_{platform}_{account_name}"
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton("⏭️ Skip Account Selection", callback_data="approve_select_skip")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Store approval state
    if not hasattr(context, "user_data"):
        context.user_data = {}
    
    context.user_data["approval_state"] = {
        "stage": "account_selection",
        "platforms": platforms,
        "selected_accounts": {}
    }
    
    await update.message.reply_text(
        "✅ Content approved!\n\n"
        "📱 Select accounts for publishing:\n"
        "(You can select multiple accounts)",
        reply_markup=reply_markup
    )
    
    return {
        "success": True,
        "available_platforms": available_platforms,
        "accounts": accounts
    }


async def handle_account_selection_callback(update, context):
    """
    Handle account selection callback during approval workflow.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    callback_data = query.data
    
    if not hasattr(context, "user_data"):
        context.user_data = {}
    
    approval_state = context.user_data.get("approval_state", {})
    
    if callback_data == "approve_select_skip":
        # Skip account selection, use default
        context.user_data["approval_state"] = {
            "stage": "account_selection_complete",
            "selected_accounts": {},
            "skipped": True
        }
        
        await query.edit_message_text(
            "⏭️ Account selection skipped.\n\n"
            "Proceeding to title generation...",
            parse_mode="Markdown"
        )
        await query.answer()
        
        # Trigger title generation
        await trigger_title_generation(update, context)
        return
    
    # Parse: approve_select_{platform}_{account_name}
    parts = callback_data.split("_")
    if len(parts) < 4:
        await query.answer("Invalid selection")
        return
    
    platform = parts[2]
    account_name = "_".join(parts[3:])
    
    # Store selected account
    if "selected_accounts" not in approval_state:
        approval_state["selected_accounts"] = {}
    
    approval_state["selected_accounts"][platform] = account_name
    context.user_data["approval_state"] = approval_state
    
    # Update message to show selection
    selected_text = "\n".join([
        f"📱 {p.capitalize()}: {name}"
        for p, name in approval_state["selected_accounts"].items()
    ])
    
    await query.edit_message_text(
        f"✅ Selected accounts:\n{selected_text}\n\n"
        f"Select more accounts or proceed to title generation.",
        parse_mode="Markdown"
    )
    await query.answer()


async def trigger_title_generation(update, context):
    """
    Trigger title/SEO generation after account selection.
    """
    approval_state = context.user_data.get("approval_state", {})
    selected_accounts = approval_state.get("selected_accounts", {})
    
    # Import and call platform SEO generator
    try:
        from Gemini_Modules.platform_seo_generator import generate_platform_seo
        
        # Get video context from session
        video_context = context.user_data.get("video_context", "")
        user_title = context.user_data.get("user_title", "")
        
        # Generate SEO for selected platforms
        platforms = list(selected_accounts.keys()) if selected_accounts else ["youtube", "instagram", "facebook", "telegram"]
        
        result = generate_platform_seo(
            video_context=video_context,
            user_title=user_title,
            platforms=platforms,
            cache=context.user_data.get("seo_cache")
        )
        
        # Store result
        context.user_data["seo_result"] = result
        context.user_data["approval_state"]["stage"] = "title_generation_complete"
        
        # Show SEO results
        await show_seo_results(update, context, result, selected_accounts)
        
    except Exception as e:
        logger.error(f"❌ Error generating SEO: {e}")
        await update.message.reply_text(
            f"❌ Error generating SEO: {str(e)}",
            parse_mode="Markdown"
        )


async def show_seo_results(
    update,
    context,
    seo_result: Dict[str, Any],
    selected_accounts: Dict[str, str]
):
    """
    Show SEO generation results to user.
    """
    platforms_data = seo_result.get("platforms", {})
    
    lines = ["🎯 *SEO Content Generated*\n"]
    
    for platform, data in platforms_data.items():
        account_name = selected_accounts.get(platform, "Default")
        lines.append(f"\n📱 {platform.capitalize()} ({account_name}):")
        lines.append(f"Title: {data.get('title', 'N/A')}")
        lines.append(f"Hashtags: {data.get('hashtags', 'N/A')}")
        lines.append(f"Description: {data.get('description', 'N/A')[:100]}...")
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve & Publish", callback_data="seo_approve_publish"),
            InlineKeyboardButton("✏️ Edit", callback_data="seo_edit")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="seo_cancel")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


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


# ── Integration Helper ───────────────────────────────────────────────────────

def integrate_with_approval_workflow(application):
    """
    Integrate account selection into existing approval workflow.
    This should be called after user approves content.
    """
    from telegram.ext import CallbackQueryHandler
    
    # Register callback handlers
    application.add_handler(
        CallbackQueryHandler(
            handle_account_selection_callback,
            pattern="^approve_select_"
        )
    )
    
    logger.info("✅ [APPROVAL ACCOUNT SELECTION] Integrated with approval workflow")
