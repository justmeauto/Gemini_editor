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

try:
    from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router, GeminiGovernor
    from Gemini_Modules.gemini_router_module.list_models import (
        get_active_models_and_ratings,
        refresh_gemini_models_cache,
        get_models_by_capability,
    )
    _HAS_GOVERNOR = True
except ImportError:
    gemini_router = None
    GeminiGovernor = None
    get_active_models_and_ratings = None
    refresh_gemini_models_cache = None
    get_models_by_capability = None
    _HAS_GOVERNOR = False

# RPM guard: Minimum delay between director turns to prevent hitting 15 RPM burst limit
_MIN_TURN_INTERVAL_SEC = 1.0


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
        model_name: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        self.api_key = api_key or _resolve_gemini_api_key()
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not configured. Please set GEMINI_API_KEY in Credentials/.env or system environment."
            )

        # Resolve initial model dynamically from governor if not explicitly specified
        if not model_name:
            if _HAS_GOVERNOR and gemini_router is not None:
                model_name = gemini_router.get_available_model(task_type="reasoning_tools")
            if not model_name:
                model_name = "gemini-2.5-flash"

        self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key)
        self.tools = get_agent_tools()

        # Activate video session budget tracking in governor
        if _HAS_GOVERNOR and gemini_router is not None:
            try:
                gemini_router.begin_video_session(
                    video_id=f"agent_run_{int(time.time())}",
                    video_duration=60.0
                )
            except Exception:
                pass

    def run_goal(
        self,
        goal: str,
        max_turns: int = 10,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes an autonomous goal from start to finish with dynamic model routing,
        quota exhaustion recovery, and rate-limit guardrails.
        """
        start_time = time.time()
        logger.info(f"\n{'='*70}\n🤖 [AUTONOMOUS DIRECTOR] Commencing Goal: '{goal}'\n{'='*70}")

        def _notify(event: str, data: Dict[str, Any]):
            if progress_callback:
                try:
                    progress_callback(event, data)
                except Exception as _cb_err:
                    logger.debug(f"Progress callback error: {_cb_err}")

        # Resolve best available model from governor for this session
        if _HAS_GOVERNOR and gemini_router is not None:
            active_model = gemini_router.get_available_model(
                task_type="reasoning_tools",
                session_id="director_session"
            )
            if active_model:
                self.model_name = active_model

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
        session_tried_models: set = set()

        # First prompt
        current_input: Any = goal

        while turn < max_turns:
            turn += 1
            turn_start = time.time()
            logger.info(f"🔄 [DIRECTOR TURN {turn}/{max_turns}] Model={self.model_name} | Waiting for decision...")
            _notify("turn_start", {"turn": turn, "max_turns": max_turns, "model": self.model_name})

            turn_response = None
            turn_tried_models: set = set()

            # Reliable model execution with quota rotation
            while True:
                try:
                    response = chat.send_message(current_input)
                    turn_response = response

                    # Report success to governor to decay penalties
                    if _HAS_GOVERNOR and gemini_router is not None:
                        with gemini_router.state_lock:
                            st = gemini_router.model_states.get(self.model_name)
                            if st:
                                st["success_count"] += 1
                                st["total_calls"] += 1
                                st["last_used_at"] = time.monotonic()
                    break

                except Exception as api_err:
                    err_str = str(api_err).lower()
                    is_quota = any(k in err_str for k in ("429", "quota", "resource_exhausted", "rate_limit"))
                    is_auth = any(k in err_str for k in ("api key", "unauthorized", "permission_denied"))

                    if is_auth and not is_quota:
                        logger.error(f"❌ [DIRECTOR] Fatal API authentication error: {api_err}")
                        return {
                            "status": "error",
                            "message": f"API authentication error during turn {turn}: {str(api_err)}",
                            "turns_executed": turn,
                            "execution_log": execution_log
                        }

                    if is_quota:
                        logger.warning(f"⚠️ [DIRECTOR] Quota exhausted on {self.model_name} (turn {turn}). Rotating...")
                        turn_tried_models.add(self.model_name)
                        session_tried_models.add(self.model_name)

                        # Ban in global governor with 429 renewal cooldown (45-90s)
                        if _HAS_GOVERNOR and gemini_router is not None:
                            gemini_router.mark_model_banned(self.model_name, error_type="429")
                            next_model = gemini_router.get_available_model(
                                task_type="reasoning_tools",
                                session_id="director_session",
                                exclude_models=turn_tried_models
                            )
                        else:
                            next_model = None

                        # If all known models exhausted, refresh live models from Google API
                        if not next_model and refresh_gemini_models_cache is not None:
                            logger.info("🔄 [DIRECTOR] All active models exhausted. Refreshing live models from API...")
                            try:
                                refreshed = refresh_gemini_models_cache(force=True)
                                disc = refreshed.get("models", [])
                                for m in disc:
                                    if m not in turn_tried_models and ("flash" in m.lower() or "pro" in m.lower()):
                                        next_model = m
                                        break
                            except Exception:
                                pass

                        if not next_model:
                            logger.error("🛑 [DIRECTOR] All Gemini models currently rate-limited (429).")
                            return {
                                "status": "quota_exhausted",
                                "message": "All Gemini models rate-limited (429). Please wait for the quota renewal window (60s).",
                                "turns_executed": turn,
                                "execution_log": execution_log
                            }

                        logger.info(f"🔀 [DIRECTOR ROTATION] {self.model_name} ➔ {next_model}")
                        self.model_name = next_model

                        # SAFE CHAT HISTORY MIGRATION:
                        # In google.genai, chat.get_history() contains only previously completed turns.
                        # Preserving this exact history ensures preceding FunctionCalls match the pending FunctionResponse.
                        try:
                            raw_history = chat.get_history() or []
                            chat = self.client.chats.create(
                                model=self.model_name,
                                config=config,
                                history=raw_history
                            )
                            logger.info(f"✅ History migrated ({len(raw_history)} turns) to {self.model_name}")
                        except Exception as hist_err:
                            logger.warning(f"⚠️ History migration failed ({hist_err}). Starting fresh session on {self.model_name}")
                            chat = self.client.chats.create(
                                model=self.model_name,
                                config=config
                            )

                        time.sleep(1.0)  # Graceful backoff
                        continue

                    # Model incompatibility error (e.g. experimental preview requiring thought_signature, or audio/bidi streaming)
                    is_incompatible = any(k in err_str for k in (
                        "thought_signature", "thought signature", "not supported for this model",
                        "unsupported", "bidigeneratecontent", "only supports", "websocket",
                        "live api"
                    )) or ("invalid_argument" in err_str and ("400" in err_str or "supports" in err_str))

                    if is_incompatible:
                        logger.warning(f"⚠️ [DIRECTOR] Model {self.model_name} is incompatible with generateContent ({api_err}). Banning and rotating...")
                        turn_tried_models.add(self.model_name)
                        session_tried_models.add(self.model_name)
                        if _HAS_GOVERNOR and gemini_router is not None:
                            gemini_router.mark_model_banned(self.model_name, error_type="model_deprecated")
                            next_model = gemini_router.get_available_model(
                                task_type="reasoning_tools",
                                session_id="director_session",
                                exclude_models=turn_tried_models
                            )
                        else:
                            next_model = None

                        if not next_model and refresh_gemini_models_cache is not None:
                            try:
                                refreshed = refresh_gemini_models_cache(force=True)
                                for m in refreshed.get("models", []):
                                    if m not in turn_tried_models and ("flash" in m.lower() or "pro" in m.lower()):
                                        next_model = m
                                        break
                            except Exception:
                                pass

                        if next_model:
                            logger.info(f"🔀 [DIRECTOR INCOMPATIBILITY ROTATION] {self.model_name} ➔ {next_model}")
                            self.model_name = next_model
                            try:
                                raw_history = chat.get_history() or []
                                chat = self.client.chats.create(
                                    model=self.model_name,
                                    config=config,
                                    history=raw_history
                                )
                                logger.info(f"✅ History migrated ({len(raw_history)} turns) to {self.model_name}")
                            except Exception as hist_err:
                                logger.warning(f"⚠️ History migration failed ({hist_err}). Fresh session on {self.model_name}")
                                chat = self.client.chats.create(model=self.model_name, config=config)
                            time.sleep(1.0)
                            continue


                    # Server temporary failure (500, 503, overloaded, timeout)
                    is_server_error = any(k in err_str for k in ("503", "500", "504", "overloaded", "service unavailable", "unavailable", "server error", "deadline_exceeded", "timed out"))
                    if is_server_error:
                        logger.warning(f"⚠️ [DIRECTOR] Model {self.model_name} hit temporary server error ({api_err}). Rotating...")
                        turn_tried_models.add(self.model_name)
                        session_tried_models.add(self.model_name)
                        if _HAS_GOVERNOR and gemini_router is not None:
                            gemini_router.mark_model_banned(self.model_name, error_type="5xx")
                            next_model = gemini_router.get_available_model(
                                task_type="reasoning_tools",
                                session_id="director_session",
                                exclude_models=turn_tried_models
                            )
                        else:
                            next_model = None

                        if next_model:
                            logger.info(f"🔀 [DIRECTOR SERVER ERROR ROTATION] {self.model_name} ➔ {next_model}")
                            self.model_name = next_model
                            try:
                                raw_history = chat.get_history() or []
                                chat = self.client.chats.create(
                                    model=self.model_name,
                                    config=config,
                                    history=raw_history
                                )
                                logger.info(f"✅ History migrated ({len(raw_history)} turns) to {self.model_name}")
                            except Exception as hist_err:
                                logger.warning(f"⚠️ History migration failed ({hist_err}). Fresh session on {self.model_name}")
                                chat = self.client.chats.create(model=self.model_name, config=config)
                            time.sleep(1.0)
                            continue

                    # Other non-quota fatal error
                    logger.error(f"❌ [DIRECTOR API ERROR] Gemini call failed: {api_err}")
                    return {
                        "status": "error",
                        "message": f"Gemini API error during turn {turn}: {str(api_err)}",
                        "turns_executed": turn,
                        "execution_log": execution_log
                    }

            if turn_response is None:
                return {
                    "status": "error",
                    "message": "Turn loop completed without valid response.",
                    "turns_executed": turn,
                    "execution_log": execution_log
                }

            response = turn_response


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

            # RPM Guard: Pace turns to stay within 15 RPM rate limit
            elapsed_turn = time.time() - turn_start
            if elapsed_turn < _MIN_TURN_INTERVAL_SEC:
                time.sleep(_MIN_TURN_INTERVAL_SEC - elapsed_turn)

        # Exhausted turns
        logger.warning(f"⚠️ [DIRECTOR TIMEOUT] Reached max turns ({max_turns}) without completion.")
        return {
            "status": "turn_limit_reached",
            "message": f"Director reached maximum turn limit ({max_turns}).",
            "turns_executed": turn,
            "execution_log": execution_log,
            "execution_time_sec": round(time.time() - start_time, 2)
        }
