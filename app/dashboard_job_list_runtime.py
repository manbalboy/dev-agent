"""Job-list read helper runtime for dashboard routes."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.models import JobRecord, JobStatus
from app.store import JobStore


class DashboardJobListRuntime:
    """Encapsulate job-list payload assembly and filtering for dashboard APIs."""

    def __init__(
        self,
        *,
        store: JobStore,
        track_choices: List[str],
        build_job_runtime_signals: Callable[[JobRecord], Dict[str, Any]],
        build_failure_classification_summary: Callable[[JobRecord], Dict[str, Any]],
    ) -> None:
        self.store = store
        self.track_choices = list(track_choices)
        self.build_job_runtime_signals = build_job_runtime_signals
        self.build_failure_classification_summary = build_failure_classification_summary

    def list_dashboard_jobs(self) -> List[Dict[str, Any]]:
        """Return dashboard jobs sorted by latest activity first."""

        jobs: List[Dict[str, Any]] = []
        for job in self.store.list_jobs():
            payload = job.to_dict()
            runtime_signals = self.build_job_runtime_signals(job)
            payload["runtime_signals"] = runtime_signals
            payload["strategy"] = runtime_signals.get("strategy", "")
            payload["resume_mode"] = runtime_signals.get("resume_mode", "none")
            payload["review_overall"] = runtime_signals.get("review_overall")
            failure_classification = self.build_failure_classification_summary(job)
            payload["failure_classification"] = failure_classification
            payload["failure_class"] = str(failure_classification.get("failure_class", "")).strip()
            payload["failure_provider_hint"] = str(failure_classification.get("provider_hint", "")).strip()
            payload["failure_stage_family"] = str(failure_classification.get("stage_family", "")).strip()
            jobs.append(payload)
        jobs.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        return jobs

    @staticmethod
    def build_job_summary(jobs: List[Dict[str, Any]]) -> Dict[str, int]:
        """Compute status counters for one job collection."""

        return {
            "total": len(jobs),
            "queued": sum(1 for item in jobs if item.get("status") == JobStatus.QUEUED.value),
            "running": sum(1 for item in jobs if item.get("status") == JobStatus.RUNNING.value),
            "done": sum(1 for item in jobs if item.get("status") == JobStatus.DONE.value),
            "failed": sum(1 for item in jobs if item.get("status") == JobStatus.FAILED.value),
        }

    @staticmethod
    def filter_dashboard_jobs(
        jobs: List[Dict[str, Any]],
        *,
        status: str,
        track: str,
        app_code: str,
        stage: str,
        recovery_status: str,
        strategy: str,
        query: str,
    ) -> List[Dict[str, Any]]:
        """Filter jobs for dashboard search and paging."""

        normalized_status = status.strip().lower()
        normalized_track = track.strip().lower()
        normalized_app_code = app_code.strip().lower()
        normalized_stage = stage.strip().lower()
        normalized_recovery_status = recovery_status.strip().lower()
        normalized_strategy = strategy.strip().lower()
        normalized_query = query.strip().lower()

        filtered: List[Dict[str, Any]] = []
        for job in jobs:
            job_status = str(job.get("status", "")).strip().lower()
            job_track = str(job.get("track", "")).strip().lower()
            job_app_code = str(job.get("app_code", "")).strip().lower()
            job_stage = str(job.get("stage", "")).strip().lower()
            job_recovery_status = str(job.get("recovery_status", "")).strip().lower()
            job_strategy = str(job.get("strategy", "")).strip().lower()

            if normalized_status and job_status != normalized_status:
                continue
            if normalized_track and job_track != normalized_track:
                continue
            if normalized_app_code and job_app_code != normalized_app_code:
                continue
            if normalized_stage and job_stage != normalized_stage:
                continue
            if normalized_recovery_status and job_recovery_status != normalized_recovery_status:
                continue
            if normalized_strategy and job_strategy != normalized_strategy:
                continue
            if normalized_query:
                runtime_signals = job.get("runtime_signals", {})
                haystack = " ".join(
                    [
                        str(job.get("job_id", "")),
                        str(job.get("issue_title", "")),
                        str(job.get("issue_number", "")),
                        str(job.get("issue_url", "")),
                        str(job.get("app_code", "")),
                        str(job.get("track", "")),
                        str(job.get("status", "")),
                        str(job.get("stage", "")),
                        str(job.get("branch_name", "")),
                        str(job.get("pr_url", "")),
                        str(job.get("workflow_id", "")),
                        str(job.get("error_message", "")),
                        str(job.get("failure_class", "")),
                        str(job.get("failure_provider_hint", "")),
                        str(job.get("failure_stage_family", "")),
                        str(job.get("recovery_status", "")),
                        str(job.get("strategy", "")),
                        str(job.get("resume_mode", "")),
                        str(job.get("review_overall", "")),
                        str((runtime_signals if isinstance(runtime_signals, dict) else {}).get("maturity_level", "")),
                        str((runtime_signals if isinstance(runtime_signals, dict) else {}).get("quality_trend_direction", "")),
                        str((runtime_signals if isinstance(runtime_signals, dict) else {}).get("shadow_strategy", "")),
                        str((runtime_signals if isinstance(runtime_signals, dict) else {}).get("shadow_decision_mode", "")),
                        " ".join(
                            str(item)
                            for item in (
                                (runtime_signals if isinstance(runtime_signals, dict) else {}).get(
                                    "persistent_low_categories",
                                    [],
                                )
                                or []
                            )
                        ),
                        " ".join(
                            str(item)
                            for item in (
                                (runtime_signals if isinstance(runtime_signals, dict) else {}).get(
                                    "quality_gate_categories",
                                    [],
                                )
                                or []
                            )
                        ),
                    ]
                ).lower()
                if normalized_query not in haystack:
                    continue
            filtered.append(job)
        return filtered

    @staticmethod
    def paginate_dashboard_jobs(
        jobs: List[Dict[str, Any]],
        *,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        """Slice job list for current page and return pagination metadata."""

        total_items = len(jobs)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        safe_page = min(max(page, 1), total_pages)
        start_index = (safe_page - 1) * page_size
        end_index = start_index + page_size
        page_items = jobs[start_index:end_index]
        visible_start = start_index + 1 if total_items else 0
        visible_end = min(end_index, total_items)
        return {
            "items": page_items,
            "pagination": {
                "page": safe_page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
                "has_prev": safe_page > 1,
                "has_next": safe_page < total_pages,
                "start_index": visible_start,
                "end_index": visible_end,
            },
        }

    @staticmethod
    def dashboard_filter_options(
        jobs: List[Dict[str, Any]],
        *,
        track_choices: List[str],
    ) -> Dict[str, List[str]]:
        """Return available filter values derived from current jobs."""

        stages = sorted(
            {
                str(item.get("stage", "")).strip()
                for item in jobs
                if str(item.get("stage", "")).strip()
            }
        )
        recovery_statuses = sorted(
            {
                str(item.get("recovery_status", "")).strip()
                for item in jobs
                if str(item.get("recovery_status", "")).strip()
            }
        )
        strategies = sorted(
            {
                str(item.get("strategy", "")).strip()
                for item in jobs
                if str(item.get("strategy", "")).strip()
            }
        )
        return {
            "statuses": [status.value for status in JobStatus],
            "tracks": sorted({str(item).strip() for item in track_choices if str(item).strip()}),
            "stages": stages,
            "recovery_statuses": recovery_statuses,
            "strategies": strategies,
        }

    def list_jobs_payload(
        self,
        *,
        page: int,
        page_size: int,
        status: str,
        track: str,
        app_code: str,
        stage: str,
        recovery_status: str,
        strategy: str,
        q: str,
    ) -> Dict[str, Any]:
        """Return filtered and paginated dashboard job payload."""

        jobs = self.list_dashboard_jobs()
        filtered_jobs = self.filter_dashboard_jobs(
            jobs,
            status=status,
            track=track,
            app_code=app_code,
            stage=stage,
            recovery_status=recovery_status,
            strategy=strategy,
            query=q,
        )
        paged = self.paginate_dashboard_jobs(filtered_jobs, page=page, page_size=page_size)
        return {
            "jobs": paged["items"],
            "summary": self.build_job_summary(jobs),
            "filtered_summary": self.build_job_summary(filtered_jobs),
            "pagination": paged["pagination"],
            "filters": {
                "status": status.strip().lower(),
                "track": track.strip().lower(),
                "app_code": app_code.strip().lower(),
                "stage": stage.strip().lower(),
                "recovery_status": recovery_status.strip().lower(),
                "strategy": strategy.strip().lower(),
                "q": q.strip(),
                "applied": any(
                    [
                        status.strip(),
                        track.strip(),
                        app_code.strip(),
                        stage.strip(),
                        recovery_status.strip(),
                        strategy.strip(),
                        q.strip(),
                    ]
                ),
            },
            "filter_options": self.dashboard_filter_options(jobs, track_choices=self.track_choices),
        }

    def get_job_options_payload(self, *, q: str, limit: int) -> Dict[str, Any]:
        """Return compact job options for combobox-style selectors."""

        jobs = self.list_dashboard_jobs()
        filtered_jobs = self.filter_dashboard_jobs(
            jobs,
            status="",
            track="",
            app_code="",
            stage="",
            recovery_status="",
            strategy="",
            query=q,
        )
        items: List[Dict[str, str]] = []
        for job in filtered_jobs[:limit]:
            issue_title = str(job.get("issue_title", "")).strip()
            truncated_title = issue_title[:72] + ("..." if len(issue_title) > 72 else "")
            items.append(
                {
                    "job_id": str(job.get("job_id", "")),
                    "label": (
                        f"{str(job.get('job_id', ''))[:8]} | "
                        f"{str(job.get('status', '-'))} | "
                        f"#{str(job.get('issue_number', '-'))} {truncated_title}"
                    ),
                    "status": str(job.get("status", "")),
                    "stage": str(job.get("stage", "")),
                    "app_code": str(job.get("app_code", "")),
                    "track": str(job.get("track", "")),
                    "issue_title": issue_title,
                }
            )
        return {"items": items, "query": q.strip(), "limit": limit}
