"""
tests/test_agentic_core.py
==========================
Unit & Integration tests for the Unified Agentic Core and Re-edit Tooling.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Agentic_Core.tool_adapters import (
    get_agent_tools,
    TOOL_DISPATCH_MAP,
    tool_get_active_sessions,
    tool_reedit_session,
)
from Agentic_Core.director import AutonomousDirector, DIRECTOR_SYSTEM_INSTRUCTION


def test_agent_tools_registry():
    """Verify all 6 tools are registered in TOOL_DISPATCH_MAP and Google GenAI schemas."""
    tools = get_agent_tools()
    assert len(tools) == 1
    decls = tools[0].function_declarations
    tool_names = [d.name for d in decls]

    expected = [
        "tool_ingest_source",
        "tool_render_edit",
        "tool_audit_clip",
        "tool_publish_clip",
        "tool_get_active_sessions",
        "tool_reedit_session",
    ]
    for exp in expected:
        assert exp in tool_names, f"Missing declaration for {exp}"
        assert exp in TOOL_DISPATCH_MAP, f"Missing dispatch handler for {exp}"


def test_tool_get_active_sessions():
    """Verify tool_get_active_sessions returns list of sessions cleanly."""
    res = tool_get_active_sessions(limit=3)
    assert res.get("status") == "success"
    assert "sessions" in res
    assert isinstance(res["sessions"], list)


def test_tool_reedit_session_missing_session():
    """Verify tool_reedit_session cleanly handles non-existent sessions."""
    res = tool_reedit_session("sess_non_existent_12345", edit_directive="faster cuts")
    assert res.get("status") == "error"
    assert "not found" in res.get("message", "").lower()


def test_director_system_instruction():
    """Verify director system instruction covers session re-editing."""
    assert "tool_reedit_session" in DIRECTOR_SYSTEM_INSTRUCTION
    assert "tool_get_active_sessions" in DIRECTOR_SYSTEM_INSTRUCTION
    assert "Re-Editing / Revision / Retries" in DIRECTOR_SYSTEM_INSTRUCTION


def test_dispatch_agent_goal_routing():
    """Verify _dispatch_agent_goal accepts callback query updates without crashing."""
    import asyncio
    from main import _dispatch_agent_goal

    mock_msg = MagicMock()
    mock_msg.chat_id = 123456789
    mock_msg.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

    mock_query = MagicMock()
    mock_query.message = mock_msg
    mock_query.from_user.id = 123456789
    mock_query.answer = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = None
    mock_update.callback_query = mock_query

    mock_context = MagicMock()
    mock_context.bot.send_message = AsyncMock()

    with patch("Agentic_Core.run_agent.run_agentic_goal_async", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "status": "success",
            "final_summary": "Test re-edit complete",
            "turns_executed": 2,
            "execution_time_sec": 1.5,
            "rendered_video_path": None,
            "session_id": "sess_test_1"
        }

        async def _test_coro():
            await _dispatch_agent_goal(mock_update, "Re-edit session 'sess_test_1' with faster cuts", context=mock_context)
            mock_query.answer.assert_called_once()

        asyncio.run(_test_coro())
