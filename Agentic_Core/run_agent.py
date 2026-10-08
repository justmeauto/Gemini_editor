"""
Agentic_Core / run_agent.py
===========================
CLI & Programmatic Entrypoint for the Autonomous Gemini Editor Agent.
Supports CLI execution, async invocation for Telegram workers, and dry-run testing.
"""

import os
import sys
import json
import asyncio
import logging
import argparse
from typing import Dict, Any, Optional, Callable

# Ensure repository root is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from Agentic_Core.director import AutonomousDirector
from Agentic_Core.tool_adapters import get_agent_tools, TOOL_DISPATCH_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("AgenticCore.Runner")


def run_agentic_goal(
    goal: str,
    max_turns: int = 10,
    model_name: str = "gemini-2.5-flash",
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Synchronous entrypoint for executing an agentic goal.
    """
    director = AutonomousDirector(model_name=model_name)
    return director.run_goal(goal, max_turns=max_turns, progress_callback=progress_callback)


async def run_agentic_goal_async(
    goal: str,
    max_turns: int = 10,
    model_name: str = "gemini-2.5-flash",
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Asynchronous non-blocking wrapper for Telegram bot handlers and web sockets.
    Runs the pipeline inside a worker thread so the async event loop never blocks.
    """
    return await asyncio.to_thread(
        run_agentic_goal,
        goal=goal,
        max_turns=max_turns,
        model_name=model_name,
        progress_callback=progress_callback
    )


def main():
    parser = argparse.ArgumentParser(description="Gemini Editor — Autonomous Agent Runner")
    parser.add_argument("--goal", type=str, default="", help="Natural language goal for the agent.")
    parser.add_argument("--source", type=str, default="", help="Optional source URL or account handle.")
    parser.add_argument("--directive", type=str, default="", help="Optional creative editing directive.")
    parser.add_argument("--turns", type=int, default=10, help="Maximum turns allowed for the goal (default: 10).")
    parser.add_argument("--model", type=str, default="gemini-2.5-flash", help="Gemini model name.")
    parser.add_argument("--dry-run", action="store_true", help="Validate tool declarations and client without executing pipeline.")

    args = parser.parse_args()

    if args.dry_run:
        print("\n🔍 Running Agentic Core Dry-Run & Tool Validation...")
        try:
            tools = get_agent_tools()
            decls = tools[0].function_declarations if tools else []
            print(f"✅ Google GenAI SDK Tools Loaded: {len(decls)} function declaration(s):")
            for d in decls:
                print(f"   - {d.name}: {d.description[:60]}...")
            print(f"✅ Tool Dispatch Registry: {list(TOOL_DISPATCH_MAP.keys())}")
            print("\n✨ Dry-run validation PASSED completely.\n")
            return
        except Exception as e:
            print(f"❌ Dry-run validation FAILED: {e}")
            sys.exit(1)

    # Compose goal if not provided directly
    goal = args.goal.strip()
    if not goal and args.source:
        goal = f"Ingest video from '{args.source}', edit it"
        if args.directive:
            goal += f" with directive '{args.directive}'"
        goal += ", audit the quality and publish to platforms."

    if not goal:
        parser.print_help()
        print("\n❌ Error: Please specify a --goal or --source to run.\n")
        sys.exit(1)

    print(f"\n🚀 Launching Autonomous Goal: '{goal}'")
    result = run_agentic_goal(goal, max_turns=args.turns, model_name=args.model)
    print("\n🏁 Execution Summary:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
