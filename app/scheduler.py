from apscheduler.schedulers.background import BackgroundScheduler
from app.state import get_db
from app.ingestion.universal_engine import ingest_source
from app.source_policy import is_roche_approved_url

_scheduler = None

def _scheduled_scrape_job(source_id: int):
    try:
        ingest_source(source_id)
    except Exception as exc:
        print(f"Scheduled ingestion notice for source {source_id}: {exc}")

def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
        _scheduler.start()
    return _scheduler

def sync_in_memory_jobs() -> int:
    """Syncs scheduler jobs with in-memory sources in DuckDB :memory:."""
    scheduler = get_scheduler()
    con = get_db()
    rows = con.execute(
        """
        SELECT source_id, scrape_interval_hours, url 
        FROM sources 
        WHERE url != '' 
          AND verification_status NOT IN ('DEMO_SYNTHETIC', 'NO_ROCHE_APPROVED_SOURCE')
          AND last_status != 'NO_ROCHE_APPROVED_SOURCE'
        """
    ).fetchall()
    
    count = 0
    for source_id, hours, url in rows:
        # Strict security gate: only approved Roche URLs are scheduled
        if not is_roche_approved_url(url):
            continue
        job_id = f"source_job_{source_id}"
        interval_h = max(1, int(hours or 24))
        scheduler.add_job(
            _scheduled_scrape_job,
            "interval",
            hours=interval_h,
            args=[source_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )
        count += 1
    return count
