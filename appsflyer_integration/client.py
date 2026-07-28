from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests


BASE_URL = "https://hq1.appsflyer.com"
RAW_REPORTS = {
    "installs": "installs_report/v5",
    "in_app_events": "in_app_events_report/v5",
    "organic_installs": "organic_installs_report/v5",
    "organic_in_app_events": "organic_in_app_events_report/v5",
    "retargeting_conversions": "installs-retarget/v5",
    "retargeting_events": "in-app-events-retarget/v5",
}


class AppsFlyerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _redacted_error(response: requests.Response) -> AppsFlyerError:
    messages = {
        400: "请求参数不符合 AppsFlyer API 要求",
        401: "AppsFlyer API token 无效、已撤销或格式错误",
        403: "AppsFlyer 账号、应用或报表权限不足",
        404: "应用不存在，或当前套餐不包含该报表接口",
        429: "AppsFlyer API 请求频率已达到限制",
    }
    return AppsFlyerError(messages.get(response.status_code, "AppsFlyer API 请求失败"), status_code=response.status_code)


def _validate_dates(from_date: str, to_date: str, max_days: int = 31) -> None:
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if start > end:
        raise ValueError("from_date 不能晚于 to_date")
    if (end - start).days + 1 > max_days:
        raise ValueError(f"单次日期范围不能超过 {max_days} 天")


@dataclass(frozen=True)
class PullResult:
    app_id: str
    report: str
    from_date: str
    to_date: str
    rows: list[dict[str, str]]


class AppsFlyerClient:
    """Minimal read-only client using an AppsFlyer API V2 bearer token."""

    def __init__(self, token: str, *, timeout: int = 120, session: requests.Session | None = None) -> None:
        if not token.strip():
            raise ValueError("APPSFLYER_API_TOKEN 未配置")
        self._token = token.strip()
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "User-Agent": "MOVA-AppsFlyer/1.0"}

    def _get(self, url: str, *, params: dict[str, Any] | None = None, accept: str = "application/json") -> requests.Response:
        headers = {**self.headers, "Accept": accept}
        for attempt in range(3):
            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                break
            time.sleep(2**attempt)
        if not response.ok:
            raise _redacted_error(response)
        return response

    def list_apps(self) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/api/mng/apps"
        apps: list[dict[str, Any]] = []
        params: dict[str, Any] | None = {"limit": 1000, "offset": 0}
        while url:
            response = self._get(url, params=params)
            payload = response.json()
            apps.extend(payload.get("data") or [])
            next_url = (payload.get("links") or {}).get("next")
            if next_url:
                parsed = urlparse(next_url)
                if parsed.scheme != "https" or parsed.netloc != "hq1.appsflyer.com":
                    raise AppsFlyerError("AppsFlyer 返回了不可信的分页地址")
            url, params = next_url, None
        return apps

    def pull_raw(self, app_id: str, report: str, from_date: str, to_date: str, **filters: str) -> PullResult:
        if report not in RAW_REPORTS:
            raise ValueError(f"不支持的报表类型：{report}")
        _validate_dates(from_date, to_date, max_days=31)
        params: dict[str, str] = {"from": from_date, "to": to_date}
        params.update({key: value for key, value in filters.items() if value})
        url = f"{BASE_URL}/api/raw-data/export/app/{app_id}/{RAW_REPORTS[report]}"
        response = self._get(url, params=params, accept="text/csv")
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
        return PullResult(app_id, report, from_date, to_date, rows)

    def master_report(
        self,
        app_id: str,
        from_date: str,
        to_date: str,
        *,
        groupings: Iterable[str] = ("pid", "c"),
        kpis: Iterable[str] = ("impressions", "clicks", "installs", "cost", "revenue"),
    ) -> list[dict[str, Any]]:
        _validate_dates(from_date, to_date, max_days=31)
        grouping_values = [value for value in groupings if value]
        kpi_values = [value for value in kpis if value]
        if not grouping_values or not kpi_values:
            raise ValueError("groupings 和 kpis 均不能为空")
        params = {
            "from": from_date,
            "to": to_date,
            "groupings": ",".join(grouping_values),
            "kpis": ",".join(kpi_values),
            "format": "json",
        }
        response = self._get(f"{BASE_URL}/api/master-agg-data/v4/app/{app_id}", params=params)
        payload = response.json()
        return payload if isinstance(payload, list) else payload.get("data", [])


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def utc_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
