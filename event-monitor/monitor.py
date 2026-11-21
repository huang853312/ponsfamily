import logging
from typing import Any

import requests


class EventSource:
    """Example JSON API adapter. Replace/extend this for the target website or chain."""

    def __init__(self, url: str, bearer_token: str = "", timeout: int = 15):
        self.url = url
        self.timeout = timeout
        self.headers = {"Accept": "application/json"}
        if bearer_token:
            self.headers["Authorization"] = f"Bearer {bearer_token}"

    def fetch_events(self) -> list[dict[str, Any]]:
        response = requests.get(
            self.url, headers=self.headers, timeout=self.timeout
        )
        response.raise_for_status()
        body = response.json()

        if isinstance(body, list):
            events = body
        elif isinstance(body, dict) and isinstance(body.get("events"), list):
            events = body["events"]
        else:
            raise ValueError(
                "Unsupported API response: expected a JSON array or {'events': [...]}"
            )

        valid_events = []
        for event in events:
            if not isinstance(event, dict):
                logging.warning("Skipping non-object event: %r", event)
                continue
            if event.get("id") is None:
                logging.warning("Skipping event without stable 'id': %r", event)
                continue
            valid_events.append(event)
        return valid_events


def matches_condition(event: dict[str, Any], field: str, minimum: float) -> bool:
    value = event.get(field)
    if value is None:
        return False
    try:
        return float(value) >= minimum
    except (TypeError, ValueError):
        logging.warning("Event %s has non-numeric %s=%r", event.get("id"), field, value)
        return False
