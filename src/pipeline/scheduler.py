"""Background scheduler for autonomous pipeline operation.

Runs periodic tasks:
- Scan channels for new videos (every 4 hours)
- Process discovered videos through the pipeline (every 30 minutes)
- Synthesize positions from approved claims (every hour)
"""

import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_scheduler_running = False
_scheduler_thread = None

# Intervals in seconds
SCAN_INTERVAL = 4 * 60 * 60       # 4 hours
PROCESS_INTERVAL = 30 * 60         # 30 minutes
POSITIONS_INTERVAL = 60 * 60       # 1 hour

_last_scan = 0.0
_last_process = 0.0
_last_positions = 0.0
_scheduler_status = {
    "running": False,
    "last_scan": None,
    "last_process": None,
    "last_positions": None,
    "errors": [],
}


def _run_scheduler():
    """Main scheduler loop."""
    global _last_scan, _last_process, _last_positions, _scheduler_running

    logger.info("Background scheduler started")
    _scheduler_status["running"] = True

    # Wait 60 seconds after startup before first run
    time.sleep(60)

    while _scheduler_running:
        now = time.time()

        try:
            # Task 1: Scan channels
            if now - _last_scan >= SCAN_INTERVAL:
                _scan_channels()
                _last_scan = now

            # Task 2: Process discovered videos
            if now - _last_process >= PROCESS_INTERVAL:
                _process_videos()
                _last_process = now

            # Task 3: Synthesize positions
            if now - _last_positions >= POSITIONS_INTERVAL:
                _synthesize_positions()
                _last_positions = now

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            _scheduler_status["errors"].append(
                f"{datetime.now(timezone.utc).isoformat()}: {str(e)[:200]}"
            )
            # Keep only last 20 errors
            _scheduler_status["errors"] = _scheduler_status["errors"][-20:]

        # Sleep 60 seconds between checks
        time.sleep(60)

    _scheduler_status["running"] = False
    logger.info("Background scheduler stopped")


def _scan_channels():
    """Scan all channels for new videos."""
    try:
        from src.db.session import get_session
        from src.pipeline.discovery import scan_all_channels

        session = get_session()
        try:
            result = scan_all_channels(session, limit=20)
            _scheduler_status["last_scan"] = {
                "time": datetime.now(timezone.utc).isoformat(),
                "result": str(result)[:200],
            }
            logger.info(f"Scheduler: scanned channels — {result}")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Scheduler scan failed: {e}")
        _scheduler_status["errors"].append(f"scan: {str(e)[:100]}")


def _process_videos():
    """Process discovered/identified videos through the pipeline."""
    try:
        from src.db.session import get_session
        from src.db.models import Videos
        from src.pipeline.transcription import transcribe_video
        from src.pipeline.identification import identify_video
        from src.pipeline.enrichment import enrich_video
        from src.pipeline.summaries import generate_episode_summary

        session = get_session()
        try:
            # Get up to 3 videos to process (small batches to avoid timeouts)
            videos = (
                session.query(Videos)
                .filter(Videos.status.in_(["discovered", "transcribed", "identified"]))
                .order_by(Videos.created_at)
                .limit(3)
                .all()
            )

            processed = 0
            for video in videos:
                try:
                    if video.status == "discovered":
                        transcribe_video(session, video)
                        session.refresh(video)

                    if video.status == "transcribed":
                        identify_video(session, video)
                        session.refresh(video)

                    if video.status == "identified":
                        enrich_video(session, video)
                        session.refresh(video)

                    if video.status == "enriched":
                        generate_episode_summary(video.id, "full_episode", session)

                    processed += 1
                except Exception as e:
                    logger.error(f"Scheduler: failed to process {video.title}: {e}")

            _scheduler_status["last_process"] = {
                "time": datetime.now(timezone.utc).isoformat(),
                "videos_processed": processed,
                "videos_found": len(videos),
            }
            if processed:
                logger.info(f"Scheduler: processed {processed}/{len(videos)} videos")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Scheduler process failed: {e}")
        _scheduler_status["errors"].append(f"process: {str(e)[:100]}")


def _synthesize_positions():
    """Synthesize positions from approved claims."""
    try:
        from src.db.session import get_session
        from src.db.models import Videos
        from src.pipeline.positions import update_positions_for_video

        session = get_session()
        try:
            videos = (
                session.query(Videos)
                .filter(Videos.status.in_(["enriched", "summarized"]))
                .limit(20)
                .all()
            )

            total_positions = 0
            for v in videos:
                try:
                    ps = update_positions_for_video(session, v.id)
                    total_positions += ps.get("positions_updated", 0)
                except Exception:
                    pass

            _scheduler_status["last_positions"] = {
                "time": datetime.now(timezone.utc).isoformat(),
                "videos_checked": len(videos),
                "positions_updated": total_positions,
            }
            if total_positions:
                logger.info(f"Scheduler: synthesized {total_positions} positions")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Scheduler positions failed: {e}")
        _scheduler_status["errors"].append(f"positions: {str(e)[:100]}")


def start_scheduler():
    """Start the background scheduler thread."""
    global _scheduler_running, _scheduler_thread

    if _scheduler_running:
        logger.info("Scheduler already running")
        return

    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_run_scheduler, daemon=True)
    _scheduler_thread.start()
    logger.info("Background scheduler thread started")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_running
    _scheduler_running = False
    logger.info("Scheduler stop requested")


def get_scheduler_status():
    """Get current scheduler status."""
    return _scheduler_status
