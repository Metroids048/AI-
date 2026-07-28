from __future__ import annotations

from apps.api.celery_app import celery_app


def test_celery_beat_schedule_contains_7x24_paper_loop_tasks() -> None:
    schedule = celery_app.conf.beat_schedule

    assert (
        schedule["paper-runtime-cycle-every-5-minutes"]["task"]
        == "services.execution.tasks.run_all_paper_runtime_cycles"
    )
    assert (
        schedule["paper-observation-cycle-every-5-minutes"]["task"]
        == "services.execution.tasks.run_observation_paper_runtime_cycles"
    )
    assert schedule["market-data-heartbeat-every-minute"]["task"] == "services.data.tasks.market_data_heartbeat"
    assert schedule["risk-profile-sweep-every-minute"]["task"] == "services.execution.tasks.risk_profile_sweep"
    assert schedule["daily-review-generation"]["task"] == "services.review.tasks.generate_daily_review"
    assert (
        schedule["notification-dispatch-every-minute"]["task"]
        == "services.notifications_tasks.dispatch_notification_outbox"
    )
    assert schedule["poll-news-feeds-every-3-minutes"]["task"] == "services.data.tasks.poll_news_feeds"
    assert (
        schedule["volatility-asset-risk-tiers-weekly"]["task"]
        == "services.execution.tasks.refresh_volatility_asset_risk_tiers"
    )
    assert schedule["signal-edge-stats-weekly"]["task"] == "services.execution.tasks.refresh_signal_edge_stats"


def test_celery_worker_requeues_unacknowledged_tasks_after_worker_loss() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.worker_prefetch_count == 1
