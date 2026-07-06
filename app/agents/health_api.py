from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


@dataclass
class HealthData:
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None


_FIT_API_ENDPOINT = "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"

_NUTRIENT_DATA_SOURCE_MAP = {
    "calories": "derived:com.google.calories.expended:com.google.android.gms:merge_calories_expended",
    "protein_g": "derived:com.google.nutrition:com.google.android.gms:merged",
    "fat_g": "derived:com.google.nutrition:com.google.android.gms:merged",
    "carbs_g": "derived:com.google.nutrition:com.google.android.gms:merged",
}

_NUTRITION_DATA_TYPE = "com.google.nutrition"
_CALORIES_DATA_TYPE = "com.google.calories.consumed"


class HealthDataClient:
    """
    Google Fit REST API から前日の栄養データを取得するクライアント。
    GOOGLE_FIT_ACCESS_TOKEN が未設定の場合はスキップして None を返す（オプション扱い）。
    """

    def __init__(self, access_token: Optional[str] = None):
        self._access_token = access_token or os.environ.get("GOOGLE_FIT_ACCESS_TOKEN")

    def _is_configured(self) -> bool:
        return bool(self._access_token)

    def _build_request_body(self, start_ms: int, end_ms: int) -> dict:
        return {
            "aggregateBy": [
                {"dataTypeName": _CALORIES_DATA_TYPE},
                {"dataTypeName": _NUTRITION_DATA_TYPE},
            ],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": start_ms,
            "endTimeMillis": end_ms,
        }

    def _parse_response(self, data: dict) -> HealthData:
        health_data = HealthData()
        buckets = data.get("bucket", [])
        if not buckets:
            return health_data

        for bucket in buckets:
            for dataset in bucket.get("dataset", []):
                data_source_id = dataset.get("dataSourceId", "")
                for point in dataset.get("point", []):
                    data_type_name = point.get("dataTypeName", "")
                    values = point.get("value", [])

                    if data_type_name == _CALORIES_DATA_TYPE and values:
                        health_data.calories = float(values[0].get("fpVal", 0))

                    elif data_type_name == _NUTRITION_DATA_TYPE:
                        for val in values:
                            map_val = val.get("mapVal", [])
                            for entry in map_val:
                                key = entry.get("key", "")
                                fp_val = entry.get("value", {}).get("fpVal", 0)
                                if key == "protein.total.g":
                                    health_data.protein_g = float(fp_val)
                                elif key == "fat.total.g":
                                    health_data.fat_g = float(fp_val)
                                elif key == "carbs.total.g":
                                    health_data.carbs_g = float(fp_val)

        return health_data

    async def get_yesterday_health_data(self) -> Optional[HealthData]:
        if not self._is_configured():
            return None

        try:
            now = datetime.now(timezone.utc)
            yesterday_start = (now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            yesterday_end = yesterday_start + timedelta(days=1)

            start_ms = int(yesterday_start.timestamp() * 1000)
            end_ms = int(yesterday_end.timestamp() * 1000)

            headers = {"Authorization": f"Bearer {self._access_token}"}
            body = self._build_request_body(start_ms, end_ms)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    _FIT_API_ENDPOINT,
                    headers=headers,
                    json=body,
                    timeout=10.0,
                )
                response.raise_for_status()
                return self._parse_response(response.json())

        except Exception:
            return None
