"""Security / TLS / governance posture helpers for operator visibility."""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse

from app.config import AppSettings


class SecurityGovernanceRuntime:
    """Summarize runtime transport and secret posture without exposing secrets."""

    _TEST_LIKE_SECRET_MARKERS = (
        "test",
        "example",
        "sample",
        "dummy",
        "changeme",
        "replace-me",
    )

    def __init__(
        self,
        *,
        settings: AppSettings,
        utc_now_iso,
    ) -> None:
        self.settings = settings
        self.utc_now_iso = utc_now_iso

    def build_status(self) -> Dict[str, Any]:
        """Return one operator-facing governance posture payload."""

        warnings: List[Dict[str, str]] = []
        next_actions: List[str] = []

        transport = self._build_transport_payload()
        cors = self._build_cors_payload()
        webhook_secret = self._build_webhook_secret_payload()
        operator_checklist = self._build_operator_checklist(
            transport=transport,
            cors=cors,
            webhook_secret=webhook_secret,
        )
        recommended_env = self._build_recommended_env(transport=transport, cors=cors)

        if not transport["configured"]:
            warnings.append(
                {
                    "code": "public_base_url_missing",
                    "severity": "high",
                    "message": "AGENTHUB_PUBLIC_BASE_URL 이 비어 있어 외부 TLS 기준 URL을 판정할 수 없습니다.",
                }
            )
            next_actions.append("운영 URL을 `AGENTHUB_PUBLIC_BASE_URL=https://...` 로 명시합니다.")
        elif not transport["https_url"]:
            warnings.append(
                {
                    "code": "public_base_url_not_https",
                    "severity": "high",
                    "message": "외부 공개 URL이 HTTPS가 아닙니다. TLS 종단 지점을 HTTPS 기준으로 맞춰야 합니다.",
                }
            )
            next_actions.append("운영 공개 URL을 HTTPS로 고정하고 reverse proxy/LB TLS 설정을 확인합니다.")

        if not transport["https_enforced"]:
            warnings.append(
                {
                    "code": "https_not_enforced",
                    "severity": "medium",
                    "message": "AGENTHUB_ENFORCE_HTTPS 가 꺼져 있어 앱이 HTTP 요청도 허용합니다.",
                }
            )
            next_actions.append("운영 환경에서는 `AGENTHUB_ENFORCE_HTTPS=true` 를 사용합니다.")

        if transport["https_enforced"] and transport["https_url"] and not transport["trust_x_forwarded_proto"]:
            warnings.append(
                {
                    "code": "forwarded_proto_not_trusted",
                    "severity": "medium",
                    "message": "HTTPS 강제가 켜졌지만 `X-Forwarded-Proto` 신뢰가 꺼져 있습니다. 프록시 TLS 환경이면 요청이 잘못 차단될 수 있습니다.",
                }
            )
            next_actions.append("TLS가 reverse proxy에서 끝나면 `AGENTHUB_TRUST_X_FORWARDED_PROTO=true` 를 켭니다.")

        if cors["allow_all"] or cors["wildcard_present"]:
            warnings.append(
                {
                    "code": "cors_too_permissive",
                    "severity": "high",
                    "message": "CORS 정책이 너무 넓습니다. 운영 origin allow-list로 줄여야 합니다.",
                }
            )
            next_actions.append("운영 환경에서는 `AGENTHUB_CORS_ALLOW_ALL=false` 와 명시적 `AGENTHUB_CORS_ORIGINS` 를 사용합니다.")

        if webhook_secret["status"] != "strong":
            warnings.append(
                {
                    "code": f"webhook_secret_{webhook_secret['status']}",
                    "severity": "high" if webhook_secret["status"] in {"missing", "test_like"} else "medium",
                    "message": webhook_secret["message"],
                }
            )
            next_actions.append("Webhook secret을 32바이트 이상 랜덤 값으로 교체하고 runbook 기준으로 로테이션합니다.")

        deduped_actions = list(dict.fromkeys(next_actions))
        severity_order = {"high": 2, "medium": 1, "low": 0}
        overall_status = "ready"
        if warnings:
            overall_status = (
                "critical"
                if max(severity_order.get(item["severity"], 0) for item in warnings) >= 2
                else "warning"
            )

        return {
            "generated_at": self.utc_now_iso(),
            "overall_status": overall_status,
            "warning_count": len(warnings),
            "warnings": warnings,
            "next_actions": deduped_actions,
            "transport": transport,
            "cors": cors,
            "webhook_secret": webhook_secret,
            "operator_checklist": operator_checklist,
            "recommended_env": recommended_env,
            "docs": {
                "security_policy": "SECURITY.md",
                "rotation_runbook": "docs/SECRET_ROTATION_AND_HISTORY_CLEANUP_RUNBOOK.md",
                "tls_runbook": "docs/REVERSE_PROXY_TLS_RUNBOOK.md",
            },
        }

    def _build_transport_payload(self) -> Dict[str, Any]:
        public_base_url = str(self.settings.public_base_url or "").strip()
        parsed = urlparse(public_base_url) if public_base_url else None
        scheme = str(parsed.scheme or "").lower() if parsed is not None else ""
        host = str(parsed.netloc or "").strip() if parsed is not None else ""
        return {
            "public_base_url": public_base_url,
            "configured": bool(public_base_url),
            "scheme": scheme,
            "host": host,
            "https_url": scheme == "https",
            "https_enforced": bool(self.settings.enforce_https),
            "trust_x_forwarded_proto": bool(self.settings.trust_x_forwarded_proto),
        }

    def _build_cors_payload(self) -> Dict[str, Any]:
        raw_origins = str(self.settings.cors_origins or "").strip()
        origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
        wildcard_present = "*" in origins or raw_origins == "*"
        status = "safe"
        if self.settings.cors_allow_all or wildcard_present:
            status = "warning"
        return {
            "allow_all": bool(self.settings.cors_allow_all),
            "origins": origins,
            "origin_count": len(origins),
            "wildcard_present": wildcard_present,
            "status": status,
        }

    def _build_webhook_secret_payload(self) -> Dict[str, Any]:
        value = str(self.settings.webhook_secret or "")
        lowered = value.lower()
        status = "strong"
        message = "Webhook secret이 길이/형식 기준을 충족합니다."
        if not value:
            status = "missing"
            message = "Webhook secret이 비어 있습니다."
        elif any(marker in lowered for marker in self._TEST_LIKE_SECRET_MARKERS):
            status = "test_like"
            message = "Webhook secret이 test/example/changeme 계열 값처럼 보입니다."
        elif len(value) < 32:
            status = "weak"
            message = "Webhook secret 길이가 짧습니다. 32바이트 이상 랜덤 값을 권장합니다."
        return {
            "configured": bool(value),
            "length": len(value),
            "status": status,
            "message": message,
        }

    def _build_operator_checklist(
        self,
        *,
        transport: Dict[str, Any],
        cors: Dict[str, Any],
        webhook_secret: Dict[str, Any],
    ) -> Dict[str, Any]:
        public_base_url_https = bool(transport.get("configured") and transport.get("https_url"))
        https_enforced = bool(transport.get("https_enforced"))
        forwarded_proto_trusted = bool(transport.get("trust_x_forwarded_proto"))
        cors_restricted = not bool(cors.get("allow_all")) and not bool(cors.get("wildcard_present"))
        webhook_secret_strong = str(webhook_secret.get("status") or "") == "strong"
        production_ready = all(
            (
                public_base_url_https,
                https_enforced,
                forwarded_proto_trusted,
                cors_restricted,
                webhook_secret_strong,
            )
        )
        return {
            "production_ready": production_ready,
            "items": [
                {
                    "code": "public_base_url_https",
                    "label": "공개 URL HTTPS 고정",
                    "ok": public_base_url_https,
                },
                {
                    "code": "https_enforced",
                    "label": "앱 레벨 HTTPS 강제",
                    "ok": https_enforced,
                },
                {
                    "code": "forwarded_proto_trusted",
                    "label": "Reverse proxy forwarded proto 신뢰",
                    "ok": forwarded_proto_trusted,
                },
                {
                    "code": "cors_restricted",
                    "label": "CORS allow-list 제한",
                    "ok": cors_restricted,
                },
                {
                    "code": "webhook_secret_strong",
                    "label": "Webhook secret 강도 충족",
                    "ok": webhook_secret_strong,
                },
            ],
        }

    @staticmethod
    def _build_recommended_env(
        *,
        transport: Dict[str, Any],
        cors: Dict[str, Any],
    ) -> Dict[str, str]:
        public_base_url = str(transport.get("public_base_url") or "").strip()
        suggested_public_base_url = public_base_url if public_base_url.startswith("https://") else "https://agenthub.example.com"
        suggested_cors_origins = str(",".join(cors.get("origins") or [])).strip()
        if not suggested_cors_origins or suggested_cors_origins == "*":
            suggested_cors_origins = suggested_public_base_url
        return {
            "AGENTHUB_PUBLIC_BASE_URL": suggested_public_base_url,
            "AGENTHUB_ENFORCE_HTTPS": "true",
            "AGENTHUB_TRUST_X_FORWARDED_PROTO": "true",
            "AGENTHUB_CORS_ALLOW_ALL": "false",
            "AGENTHUB_CORS_ORIGINS": suggested_cors_origins,
            "AGENTHUB_WEBHOOK_SECRET": "<32+ byte random secret>",
        }
