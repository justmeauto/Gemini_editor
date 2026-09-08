"""
Phase_2 / phase2_orchestrator.py
================================
Master Orchestrator for Phase 2 (AI Perception, BGM Selection & Master FFmpeg Rendering).
Coordinates sequential steps 01 -> 07 and supports real-time event callbacks for the live web tracker.
"""

import json
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
    batch_used_bgms = set()

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
            # ─────────────────────────────────────────────────────────────────
            # STEP 2A: UPFRONT WATERMARK DETECTION & INPAINTING
            # Erase foreign watermarks first so Scene Intel, Forensic Perception,
            # and FFmpeg single-pass synthesis operate on clean video.
            # ─────────────────────────────────────────────────────────────────
            _emit("step_02_watermark", "running", {"message": f"Checking/inpainting watermarks for {folder_name}..."})
            clean_raw_path = os.path.join(clip_dir, "video_inpainted_clean.mp4")
            working_video_path = video_path
            watermark_boxes = []

            # 1. Check Telegram Storage Vault for pre-cleaned video (wm_clean_file_id) if not on local disk
            if not os.path.exists(clean_raw_path) or os.path.getsize(clean_raw_path) <= 1024:
                try:
                    from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
                    _v_indexer = TelegramVaultIndexer()
                    _clean_hydrated = _v_indexer.hydrate_clean_video_from_vault(clip_id, clip_dir)
                    if _clean_hydrated and os.path.exists(_clean_hydrated) and os.path.getsize(_clean_hydrated) > 1024:
                        clean_raw_path = _clean_hydrated
                        logger.info(f"⚡ [VAULT INPAINTING CACHE HIT] Hydrated pre-cleaned video from Telegram Vault: {os.path.basename(clean_raw_path)} — skipping OpenCV inpainting!")
                except Exception as _vi_err:
                    logger.debug(f"[UPFRONT INPAINTING] Vault clean check notice: {_vi_err}")

            # 2. If clean video already exists, reuse it and load sidecar coordinates
            if os.path.exists(clean_raw_path) and os.path.getsize(clean_raw_path) > 1024:
                working_video_path = clean_raw_path
                logger.info(f"⚡ [UPFRONT INPAINTING CACHE] Reusing clean inpainted raw video: {os.path.basename(working_video_path)}")
                coords_sidecar = clean_raw_path + ".coords.json"
                if os.path.exists(coords_sidecar):
                    try:
                        with open(coords_sidecar, "r", encoding="utf-8") as _csf:
                            _cdata = json.load(_csf)
                        watermark_boxes = _cdata.get("watermark_boxes") or []
                        if watermark_boxes:
                            logger.info(f"📍 [WATERMARK ALIGNMENT] Loaded {len(watermark_boxes)} inpaint coordinate box(es) from sidecar.")
                    except Exception as _ce:
                        logger.warning(f"⚠️ [WATERMARK ALIGNMENT] Failed to load coords sidecar: {_ce}")
            else:
                # 3. Detect and Inpaint Watermarks Upfront
                try:
                    from Watermark_and_Inpainting.gemini_enhance_for_watermark import detect_watermark_from_video
                    from Watermark_and_Inpainting.watermark_main import run_watermark_removal

                    logger.info(f"🧼 [UPFRONT INPAINTING] Running watermark detection & inpainting on: {os.path.basename(video_path)}")
                    items, _ = detect_watermark_from_video(video_path=video_path)
                    if items:
                        watermark_boxes = items
                        logger.info(f"💎 [STEP 02 WATERMARK DETECTED] Found {len(items)} watermark bounding box vector(s).")

                    inpainted_path, _ = run_watermark_removal(
                        input_path=video_path,
                        output_path=clean_raw_path,
                        retry_level=0
                    )
                    if inpainted_path and os.path.exists(inpainted_path) and os.path.getsize(inpainted_path) > 1024:
                        working_video_path = inpainted_path
                        logger.info(f"✅ [UPFRONT INPAINTING SUCCESS] Raw video cleaned: {os.path.basename(working_video_path)}")

                        # Load sidecar coordinates
                        coords_sidecar = clean_raw_path + ".coords.json"
                        if os.path.exists(coords_sidecar):
                            try:
                                with open(coords_sidecar, "r", encoding="utf-8") as _csf:
                                    _cdata = json.load(_csf)
                                _wm_b = _cdata.get("watermark_boxes")
                                if _wm_b:
                                    watermark_boxes = _wm_b
                                    logger.info(f"📍 [WATERMARK ALIGNMENT] Loaded {len(watermark_boxes)} inpaint coordinate box(es) for brand overlay masking.")
                            except Exception as _ce:
                                logger.debug(f"Coords sidecar read notice: {_ce}")

                        # Upload clean inpainted source to Telegram Vault
                        try:
                            from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
                            vault_idx = TelegramVaultIndexer()
                            clean_fid = vault_idx.update_inpainted_clean_source_in_vault(
                                clean_video_path=clean_raw_path,
                                clip_folder_name=folder_name
                            )
                            if clean_fid:
                                logger.info(f"☁️ [CLEAN VAULT UPLOAD] Uploaded clean video to vault (file_id: {clean_fid[:15]}...)")
                        except Exception as _clean_up_err:
                            logger.warning(f"⚠️ [UPFRONT INPAINTING] Clean vault upload notice: {_clean_up_err}")
                except Exception as _inp_err:
                    logger.warning(f"⚠️ [UPFRONT INPAINTING] Notice: {_inp_err}")

            # ─────────────────────────────────────────────────────────────────
            # STEP 2B: SCENE INTEL & GEMINI CALL 1 FORENSIC PERCEPTION
            # Analyzes the CLEAN video (working_video_path) to determine visual
            # intent, tone, style, narrative, faces/subjects, and speech mode.
            # ─────────────────────────────────────────────────────────────────
            _emit("step_02", "running", {"message": f"Running Gemini Call 1 Forensic Perception on clean video for {folder_name}..."})
            creator_hint = folder_name.split("_")[0] if "_" in folder_name else "unknown"
            forensic_res = step02.run_forensic_perception(
                video_path=working_video_path,
                creator_name=creator_hint,
            )
            forensic_res["working_video_path"] = working_video_path
            forensic_res["watermark_boxes"] = watermark_boxes
            forensic_res["inpainted_upfront"] = (working_video_path == clean_raw_path)
            _emit("step_02", "success", {"message": f"Perception complete: intent='{forensic_res.get('intent')}'."})


            # Step 3: OpenCV Vector-Guided Frame Extraction
            _emit("step_03", "running", {"message": "Extracting vector-guided keyframes..."})
            visual_vectors = forensic_res.get("visual_vectors", {})
            frame_paths = step03.extract_targeted_frames(
                video_path=working_video_path,
                tmp_dir=tmp_dir,
                visual_vectors=visual_vectors,
            )
            _emit("step_03", "success", {"message": f"Extracted {len(frame_paths)} targeted keyframes."})

            # Step 4: Gemini Call 2 BGM Selector
            _emit("step_04", "running", {"message": f"Selecting optimal BGM track for clip '{folder_name}'..."})

            exclude_bgm = set(batch_used_bgms)
            force_new_music = False
            if user_edit_directive:
                d_lower = user_edit_directive.lower()
                if any(kw in d_lower for kw in ["music", "bgm", "song", "track", "rhythm", "soundtrack", "audio"]):
                    force_new_music = True
                    try:
                        from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
                        from Audio_Modules.audio_pool_manager import AudioPoolManager
                        _st = ClipIntelligenceStore(clip_id=folder_name, clip_folder=clip_dir)
                        _prev_aud = _st.get("audio_data") or {}
                        _prev_track = _prev_aud.get("selected_bgm_track") or _prev_aud.get("selected_audio_track")
                        if _prev_track:
                            exclude_bgm.add(_prev_track)
                            exclude_bgm.add(os.path.basename(_prev_track))
                            logger.info(f"🚫 [STEP 04 RE-EDIT] User requested music change! Excluding previous BGM: '{_prev_track}'")

                        # ── POOL EXHAUSTION GUARD ──────────────────────────────────────────────
                        # If excluding the previous track empties the entire pool, warn clearly
                        # and fall back to the least-recently-used track instead of re-using the
                        # same one silently. This happens when the pool has only 1-2 tracks.
                        try:
                            _pm = AudioPoolManager()
                            _all_pool = [
                                f for f in _pm.get_files_index().keys()
                                if f.lower().endswith((".mp3", ".wav", ".m4a"))
                            ]
                            _fresh_pool = [f for f in _all_pool if f.lower() not in {e.lower() for e in exclude_bgm}]
                            if not _fresh_pool and _all_pool:
                                logger.warning(
                                    f"⚠️ [STEP 04 RE-EDIT] BGM pool exhausted after exclusion "
                                    f"(pool_size={len(_all_pool)}, excluded={len(exclude_bgm)}). "
                                    f"Clearing exclusions and picking least-recently-used track "
                                    f"to avoid silent same-song repeat."
                                )
                                exclude_bgm.clear()
                                # Don't exclude the previous track from the batch-level set —
                                # just allow Gemini to pick again with full pool (LRU ordering).
                        except Exception as _pgd_err:
                            logger.debug(f"Pool guard check notice: {_pgd_err}")
                        # ─────────────────────────────────────────────────────────────────────
                    except Exception as _ex_err:
                        logger.debug(f"Exclusion lookup notice: {_ex_err}")

            bgm_res = step04.select_clip_bgm(
                clip_id=folder_name,
                clip_folder=clip_dir,
                intent_vector={"preserve_music": not force_new_music},
                exclude_filenames=exclude_bgm if exclude_bgm else None,
            )
            selected_bgm_path = bgm_res.get("physical_path")
            selected_track_name = bgm_res.get("selected_audio_track")
            if selected_track_name:
                batch_used_bgms.add(selected_track_name)
                batch_used_bgms.add(os.path.basename(selected_track_name))
            if selected_bgm_path:
                batch_used_bgms.add(os.path.basename(selected_bgm_path))
            _emit("step_04", "success", {"message": f"BGM selected: '{selected_track_name}'."})

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
