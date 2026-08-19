"""
Phase_2 / phase2_orchestrator.py
================================
Master Orchestrator for Phase 2 (AI Perception, BGM Selection & Master FFmpeg Rendering).
Coordinates sequential steps 01 -> 07 and supports real-time event callbacks for the live web tracker.
"""

import os
import sys
import time
import logging
import importlib
import tempfile
import shutil
from typing import Dict, List, Any, Optional, Callable

logger = logging.getLogger("Phase2.Orchestrator")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Dynamic import of indexed step modules
step01 = importlib.import_module("Phase_2.01_folder_scanner")
step02 = importlib.import_module("Phase_2.02_forensic_perception")
step03 = importlib.import_module("Phase_2.03_vector_frame_extractor")
step04 = importlib.import_module("Phase_2.04_bgm_selector")
step05 = importlib.import_module("Phase_2.05_rhythm_timeline")
step06 = importlib.import_module("Phase_2.06_ffmpeg_synthesis")
step07 = importlib.import_module("Phase_2.07_master_render")

try:
    from Import_Modules.tracker_notifier import notify_tracker
except ImportError:
    notify_tracker = None


def run_phase2_pipeline(
    input_path: Optional[str] = None,
    downloads_dir: Optional[str] = None,
    master_edits_dir: Optional[str] = None,
    limit: Optional[int] = None,
    target_dirs: Optional[List[str]] = None,
    skip_existing: bool = False,
    on_rendered_callback: Optional[Callable[[str], None]] = None,
    event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    user_edit_directive: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes Phase 2 Pipeline through indexed steps 01 -> 07.
    """
    if event_callback is None and notify_tracker is not None:
        event_callback = notify_tracker

    def _emit(step_id: str, status: str, payload: Dict[str, Any]):
        if event_callback:
            try:
                event_callback(step_id, status, payload)
            except Exception as _cb_err:
                logger.debug(f"Event callback warning: {_cb_err}")

    if master_edits_dir is None:
        master_edits_dir = os.path.join(_REPO_ROOT, "Processed Shorts")
    os.makedirs(master_edits_dir, exist_ok=True)

    logger.info(f"🚀 [PHASE 2 ORCHESTRATOR] Starting Master AI Editing Pipeline -> Output: {master_edits_dir}")
    _emit("step_01", "running", {"message": "Scanning downloads/ clip targets..."})

    # Step 1: Scan Targets
    targets = step01.scan_clip_targets(
        input_path=input_path,
        downloads_dir=downloads_dir,
        target_dirs=target_dirs,
        limit=limit,
    )
    _emit("step_01", "success", {"message": f"Verified {len(targets)} clip target(s)."})

    rendered_files = []
    skipped_count = 0
    failed_count = 0

    for idx, target in enumerate(targets, start=1):
        folder_name = target["folder_name"]
        video_path = target["video_path"]
        clip_dir = target["dir"]

        target_output = os.path.join(master_edits_dir, f"{folder_name}_master.mp4")

        if skip_existing and os.path.isfile(target_output) and os.path.getsize(target_output) > 1024:
            logger.info(f"♻️ [SKIP EXPLICITLY REQUESTED] '{folder_name}_master.mp4' already exists.")
            rendered_files.append(os.path.abspath(target_output))
            continue

        logger.info(f"\n🚀 [PHASE 2 CLIP {idx}/{len(targets)}] Ingesting folder: {folder_name}")
        tmp_dir = tempfile.mkdtemp(prefix="phase2_frames_")

        try:
            # Step 2: Gemini Call 1 Forensic Perception
            _emit("step_02", "running", {"message": f"Running Gemini Call 1 Forensic Perception for {folder_name}..."})
            creator_hint = folder_name.split("_")[0] if "_" in folder_name else "unknown"
            forensic_res = step02.run_forensic_perception(
                video_path=video_path,
                creator_name=creator_hint,
            )

            # ─────────────────────────────────────────────────────────────────
            # WATERMARK BOUNDING BOX PRE-SCAN (required for single-pass delogo
            # + drawtext inside gemini_ffmpeg_synthesis.py filtergraph)
            #
            # forensic_res["watermarks"] is an INTEGER COUNT returned by
            # Gemini forensic call — it is NOT pixel coordinates.
            # We need actual {x, y, w, h} dicts in "watermark_boxes" to pass
            # to build_single_pass_filtergraph() as delogo + drawtext targets.
            #
            # Always run detect_watermark_from_video when either:
            #  - watermark_boxes is missing / empty
            #  - watermarks integer count > 0 (Gemini forensic confirmed presence)
            # ─────────────────────────────────────────────────────────────────
            _wm_boxes = forensic_res.get("watermark_boxes")
            _has_coord_boxes = isinstance(_wm_boxes, list) and len(_wm_boxes) > 0
            _wm_count = forensic_res.get("watermarks", 0)
            _wm_count_int = int(_wm_count) if isinstance(_wm_count, (int, float)) else 0

            if not _has_coord_boxes:
                try:
                    from Watermark_and_Inpainting.gemini_enhance_for_watermark import detect_watermark_from_video
                    items, _ = detect_watermark_from_video(video_path=video_path)
                    if items:
                        forensic_res["watermark_boxes"] = items
                        _has_coord_boxes = True
                        logger.info(
                            f"💎 [STEP 02 WATERMARK DETECTED] Found {len(items)} watermark bounding box vector(s) "
                            f"(forensic watermark count={_wm_count_int})."
                        )
                except Exception as _wm_det_err:
                    logger.debug(f"Watermark pre-scan notice: {_wm_det_err}")

            # ─────────────────────────────────────────────────────────────────
            # UPFRONT WATERMARK INPAINTING (Step 2.5)
            # Run OpenCV inpainting FIRST on the raw input video so that all
            # foreign watermarks are erased BEFORE FFmpeg single-pass synthesis.
            # Then Step 6 FFmpeg single-pass burns Montserrat brand text on clean
            # video, and Phase 2.1 is SKIPPED (0 re-encodes, brand text protected).
            # ─────────────────────────────────────────────────────────────────
            clean_raw_path = os.path.join(clip_dir, "video_inpainted_clean.mp4")
            working_video_path = video_path

            # If cleaned inpainted video already exists on disk from run 1, reuse it instantly (guarantees inpainting on re-edits)
            if os.path.exists(clean_raw_path) and os.path.getsize(clean_raw_path) > 1024:
                working_video_path = clean_raw_path
                forensic_res["inpainted_upfront"] = True
                logger.info(f"⚡ [UPFRONT INPAINTING CACHE] Reusing clean inpainted raw video: {os.path.basename(working_video_path)}")
                
                # Load coordinates from sidecar file if available
                coords_sidecar = clean_raw_path + ".coords.json"
                if os.path.exists(coords_sidecar):
                    try:
                        with open(coords_sidecar, "r", encoding="utf-8") as _csf:
                            _cdata = json.load(_csf)
                        _wm_b = _cdata.get("watermark_boxes")
                        if _wm_b:
                            forensic_res["watermark_boxes"] = _wm_b
                            logger.info(f"📍 [WATERMARK ALIGNMENT] Loaded {len(_wm_b)} inpaint coordinate box(es) from sidecar.")
                    except Exception as _ce:
                        logger.debug(f"Coords sidecar read notice: {_ce}")

            elif _has_coord_boxes or _wm_count_int > 0:
                try:
                    from Watermark_and_Inpainting.watermark_main import run_watermark_removal
                    logger.info(f"🧼 [UPFRONT INPAINTING] Running OpenCV watermark inpainting on raw input BEFORE synthesis: {os.path.basename(video_path)}")
                    inpainted_path, _ = run_watermark_removal(
                        input_path=video_path,
                        output_path=clean_raw_path,
                        retry_level=0
                    )
                    if inpainted_path and os.path.exists(inpainted_path) and os.path.getsize(inpainted_path) > 1024:
                        working_video_path = inpainted_path
                        forensic_res["inpainted_upfront"] = True
                        logger.info(f"✅ [UPFRONT INPAINTING SUCCESS] Raw video cleaned: {os.path.basename(working_video_path)}")

                        # Load coordinates from sidecar file saved by run_watermark_removal
                        coords_sidecar = clean_raw_path + ".coords.json"
                        if os.path.exists(coords_sidecar):
                            try:
                                with open(coords_sidecar, "r", encoding="utf-8") as _csf:
                                    _cdata = json.load(_csf)
                                _wm_b = _cdata.get("watermark_boxes")
                                if _wm_b:
                                    forensic_res["watermark_boxes"] = _wm_b
                                    logger.info(f"📍 [WATERMARK ALIGNMENT] Loaded {len(_wm_b)} inpaint coordinate box(es) for brand overlay masking.")
                            except Exception as _ce:
                                logger.debug(f"Coords sidecar read notice: {_ce}")
                except Exception as _inp_err:
                    logger.warning(f"⚠️ [UPFRONT INPAINTING] Notice: {_inp_err}")


            forensic_res["working_video_path"] = working_video_path
            _emit("step_02", "success", {"message": f"Perception complete: intent='{forensic_res.get('intent')}'."})


            # Step 3: OpenCV Vector-Guided Frame Extraction
            _emit("step_03", "running", {"message": "Extracting vector-guided keyframes..."})
            visual_vectors = forensic_res.get("visual_vectors", {})
            frame_paths = step03.extract_targeted_frames(
                video_path=video_path,
                tmp_dir=tmp_dir,
                visual_vectors=visual_vectors,
            )
            _emit("step_03", "success", {"message": f"Extracted {len(frame_paths)} targeted keyframes."})

            # Step 4: Gemini Call 2 BGM Selector
            _emit("step_04", "running", {"message": f"Selecting optimal BGM track for clip '{folder_name}'..."})
            bgm_res = step04.select_clip_bgm(
                clip_id=folder_name,
                clip_folder=clip_dir,
            )
            selected_bgm_path = bgm_res.get("physical_path")
            _emit("step_04", "success", {"message": f"BGM selected: '{bgm_res.get('selected_audio_track')}'."})

            # Step 5: Rhythm & Micro-Shot Timeline Builder
            _emit("step_05", "running", {"message": "Building rhythm & micro-shot timeline..."})
            forensic_res["route_params"] = forensic_res.get("route_params", {})
            timeline_res = step05.build_rhythm_timeline(
                video_path=video_path,
                selected_bgm_path=selected_bgm_path,
                forensic_context=forensic_res,
            )
            micro_shots = timeline_res.get("micro_shots", [])
            forensic_res["lyric_intel"] = timeline_res.get("lyric_intel", {})
            if timeline_res.get("route_params"):
                forensic_res["route_params"] = timeline_res["route_params"]
            if timeline_res.get("selected_bgm_path"):
                selected_bgm_path = timeline_res["selected_bgm_path"]
            _emit("step_05", "success", {"message": f"Built {len(micro_shots)} micro-shot takes (2.0s-3.5s)."})

            # Step 6: Gemini Call 3 FFmpeg Synthesis
            _emit("step_06", "running", {"message": "Synthesizing FFmpeg Master Filtergraph Editing Plan..."})
            synthesis_res = step06.synthesize_editing_plan(
                video_path=working_video_path,
                output_path=target_output,
                selected_bgm_path=selected_bgm_path,
                forensic_context=forensic_res,
                micro_shots=micro_shots,
                target_duration=15.0,
                user_edit_directive=user_edit_directive,
            )
            _emit("step_06", "success", {"message": f"FFmpeg synthesis status: {synthesis_res.get('status')}."})

            # Step 7: Master Render Verification & QA Gate
            _emit("step_07", "running", {"message": f"Verifying master render for {folder_name}..."})
            render_res = step07.verify_master_render(
                output_path=target_output,
                synthesis_result=synthesis_res,
            )

            if render_res.get("success"):
                out_path = render_res["output_video"]
                rendered_files.append(out_path)
                _emit("step_07", "success", {"message": f"Master reel ready: {os.path.basename(out_path)}."})

                if on_rendered_callback:
                    try:
                        on_rendered_callback(out_path)
                    except Exception as cb_err:
                        logger.warning(f"Render callback notice: {cb_err}")
            else:
                failed_count += 1
                _emit("step_07", "failed", {"message": f"Master render verification failed: {render_res.get('error')}."})

        except Exception as clip_err:
            failed_count += 1
            logger.error(f"❌ Exception editing '{folder_name}': {clip_err}", exc_info=True)
            _emit("step_07", "failed", {"message": f"Clip edit exception: {clip_err}"})
        finally:
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(
        f"\n🎉 PHASE 2 COMPLETE: {len(rendered_files)} master reel(s) rendered | "
        f"{skipped_count} skipped | {failed_count} failed."
    )
    return {
        "success": True,
        "rendered_count": len(rendered_files),
        "rendered_files": rendered_files,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "master_edits_dir": master_edits_dir,
    }
