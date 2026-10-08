"""
Agentic_Core / director.py
==========================
Autonomous Director & ReAct Orchestration Loop for Gemini Editor.
Directs ingestion, perception rendering, audit, and publishing using Gemini 2.5/1.5 Flash.
"""

import os
import sys
import json
import time
import logging
import traceback
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger("AgenticCore.Director")

# Ensure repository root is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from google import genai
from google.genai import types

from .tool_adapters import get_agent_tools, TOOL_DISPATCH_MAP


DIRECTOR_SYSTEM_INSTRUCTION = """
You are the Autonomous Creative Director for Gemini Editor — a professional automated video production system.
Your job is to achieve the user's video editing and publishing goal by autonomously executing the available tools in sequence:

Available Tools:
1. `tool_ingest_source(source, limit, platform)`: Ingests target URL or creator handle, downloads streams, and prepares clip directories.
2. `tool_render_edit(clip_dir, edit_directive, skip_existing)`: Renders AI perception, watermark inpainting, BGM selection, and FFmpeg synthesis.
3. `tool_audit_clip(video_path, clip_dir, niche)`: Audits rendered video duration, quality, dopamine retention, and generates viral SEO.
4. `tool_publish_clip(video_path, clip_dir, platforms)`: Ingests to monetization gate and publishes to YouTube Shorts, Meta, and TikTok.
5. `tool_get_active_sessions(limit)`: Lists recent active video editing sessions from Telegram session memory.
6. `tool_reedit_session(session_id, edit_directive)`: Re-edits an existing video session based on creative directives (e.g. faster cuts, change music, adjust pacing).

Execution Rules:
1. Brand New Creation: Always start by ingesting the requested source using `tool_ingest_source`, render with `tool_render_edit`, and audit with `tool_audit_clip`.
2. Re-Editing / Revision / Retries:
   - If the user goal is to re-edit, retry, or adjust an existing session (or user clicked a retry button or entered a custom prompt):
     * If session_id is given (e.g. 'sess_...'), call `tool_reedit_session(session_id=..., edit_directive=...)`.
     * If session_id is not given (e.g. 're-edit the last reel'), call `tool_get_active_sessions` first to get the most recent session, then call `tool_reedit_session`.
   - Always follow up `tool_reedit_session` with `tool_audit_clip`.
3. Quality Gate: If audit passes (`audit_passed: true`), you may publish with `tool_publish_clip` if publication was requested.
4. Conclude with a clear, concise summary of all actions taken, files generated, and publishing status.
"""


def _resolve_gemini_api_key() -> str:
    """Finds Gemini API key across environment and standard project .env files."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()

    candidate_files = [
        os.path.join(_REPO_ROOT, ".env"),
        os.path.join(_REPO_ROOT, "Credentials", ".env"),
        os.path.join(os.path.dirname(_REPO_ROOT), ".env"),
    ]

    for env_path in candidate_files:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line_clean = line.strip()
                        if line_clean.startswith("GEMINI_API_KEY=") or line_clean.startswith("GOOGLE_API_KEY="):
                            val = line_clean.split("=", 1)[1].strip().strip("'").strip('"')
                            if val:
                                return val
            except Exception:
                pass
    return ""


class AutonomousDirector:
    """
    Autonomous ReAct Loop Controller for Gemini Editor.
    """

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        api_key: Optional[str] = None
    ):
        self.model_name = model_name
        self.api_key = api_key or _resolve_gemini_api_key()
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not configured. Please set GEMINI_API_KEY in Credentials/.env or system environment."
            )

        self.client = genai.Client(api_key=self.api_key)
        self.tools = get_agent_tools()

    def run_goal(
        self,
        goal: str,
        max_turns: int = 10,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes an autonomous goal from start to finish.
        """
        start_time = time.time()
        logger.info(f"\n{'='*70}\n🤖 [AUTONOMOUS DIRECTOR] Commencing Goal: '{goal}'\n{'='*70}")

        def _notify(event: str, data: Dict[str, Any]):
            if progress_callback:
                try:
                    progress_callback(event, data)
                except Exception as _cb_err:
                    logger.debug(f"Progress callback error: {_cb_err}")

        _notify("director_started", {"goal": goal, "model": self.model_name})

        # Configure Chat with Function Calling
        config = types.GenerateContentConfig(
            system_instruction=DIRECTOR_SYSTEM_INSTRUCTION,
            tools=self.tools,
            temperature=0.2,
        )

        chat = self.client.chats.create(
            model=self.model_name,
            config=config
        )

        turn = 0
        execution_log: List[Dict[str, Any]] = []
        last_call_signature: Optional[str] = None

        # First prompt
        current_input: Any = goal

        while turn < max_turns:
            turn += 1
            logger.info(f"🔄 [DIRECTOR TURN {turn}/{max_turns}] Waiting for model decision...")
            _notify("turn_start", {"turn": turn, "max_turns": max_turns})

            try:
                response = chat.send_message(current_input)
            except Exception as api_err:
                logger.error(f"❌ [DIRECTOR API ERROR] Gemini call failed: {api_err}")
                return {
                    "status": "error",
                    "message": f"Gemini API error during turn {turn}: {str(api_err)}",
                    "turns_executed": turn,
                    "execution_log": execution_log
                }

            # Check if model wants to call tools
            function_calls = response.function_calls
            if not function_calls:
                final_text = response.text or "Goal execution completed."
                logger.info(f"🏁 [DIRECTOR FINISHED] Final response reached in {turn} turn(s).")
                _notify("director_completed", {"final_text": final_text, "turns": turn})

                # Extract any rendered video paths and session info generated during execution
                latest_video = None
                latest_session = None
                latest_retry_count = None
                audit_passed = None

                for item in execution_log:
                    res_data = item.get("result") or {}
                    if isinstance(res_data, dict):
                        if res_data.get("new_master_video") or res_data.get("rendered_video_path"):
                            latest_video = res_data.get("new_master_video") or res_data.get("rendered_video_path")
                        if res_data.get("session_id"):
                            latest_session = res_data.get("session_id")
                        if res_data.get("retry_count") is not None:
                            latest_retry_count = res_data.get("retry_count")
                        if res_data.get("audit_passed") is not None:
                            audit_passed = res_data.get("audit_passed")

                return {
                    "status": "success",
                    "final_summary": final_text,
                    "turns_executed": turn,
                    "execution_time_sec": round(time.time() - start_time, 2),
                    "execution_log": execution_log,
                    "rendered_video_path": latest_video,
                    "session_id": latest_session,
                    "retry_count": latest_retry_count,
                    "audit_passed": audit_passed
                }

            # Dispatch tool calls
            tool_response_parts = []
            for call in function_calls:
                tool_name = call.name
                tool_args = dict(call.args or {})

                # Duplicate call guardrail (prevent infinite loops)
                call_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                if call_sig == last_call_signature:
                    logger.warning(f"⚠️ [GUARDRAIL HIT] Model requested identical call twice in a row: {call_sig}")
                    tool_result = {
                        "status": "aborted",
                        "error": "Duplicate tool call detected. You already called this tool with identical arguments. Change your approach or finish."
                    }
                else:
                    last_call_signature = call_sig
                    handler = TOOL_DISPATCH_MAP.get(tool_name)
                    if not handler:
                        tool_result = {"status": "error", "message": f"Unknown tool: '{tool_name}'"}
                    else:
                        logger.info(f"⚙️ [DISPATCHING TOOL] {tool_name}({tool_args})")
                        _notify("tool_executing", {"tool": tool_name, "args": tool_args})
                        try:
                            tool_result = handler(**tool_args)
                        except Exception as tool_ex:
                            tool_result = {
                                "status": "error",
                                "message": f"Tool execution crashed: {str(tool_ex)}"
                            }

                execution_log.append({
                    "turn": turn,
                    "tool": tool_name,
                    "args": tool_args,
                    "result_status": tool_result.get("status"),
                    "result": tool_result
                })

                _notify("tool_completed", {"tool": tool_name, "status": tool_result.get("status")})

                # Format part response for Gemini
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": tool_result}
                    )
                )

            # Feed tool results back into the chat
            current_input = tool_response_parts

        # Exhausted turns
        logger.warning(f"⚠️ [DIRECTOR TIMEOUT] Reached max turns ({max_turns}) without completion.")
        return {
            "status": "turn_limit_reached",
            "message": f"Director reached maximum turn limit ({max_turns}).",
            "turns_executed": turn,
            "execution_log": execution_log,
            "execution_time_sec": round(time.time() - start_time, 2)
        }
