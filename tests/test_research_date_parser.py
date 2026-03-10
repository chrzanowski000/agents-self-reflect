"""Unit tests for parse_dates node in research_agent."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub config before importing agent
_mock_cfg = MagicMock()
_mock_cfg.duckling_url = "http://localhost:8000"

sys.modules.setdefault("config", MagicMock(
    Config=MagicMock(from_env=MagicMock(return_value=_mock_cfg)),
    ConfigError=Exception,
))

from langchain_core.messages import HumanMessage  # noqa: E402

import agents.research_agent as ra  # noqa: E402
ra.cfg = _mock_cfg

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _duckling_interval_response():
    """Simulate duckling returning a time interval."""
    return [
        {
            "dim": "time",
            "value": {
                "type": "interval",
                "from": {"value": "2026-01-01T00:00:00.000Z", "grain": "day"},
                "to": {"value": "2026-01-16T00:00:00.000Z", "grain": "day"},
            },
        }
    ]


def _duckling_point_year_response():
    return [
        {
            "dim": "time",
            "value": {
                "type": "value",
                "value": "2024-01-01T00:00:00.000Z",
                "grain": "year",
            },
        }
    ]


def _duckling_interval_years_response():
    # Duckling returns "2027-01-01" as the EXCLUSIVE upper bound for "2024-2026"
    return [
        {
            "dim": "time",
            "value": {
                "type": "interval",
                "from": {"value": "2024-01-01T00:00:00.000Z", "grain": "year"},
                "to": {"value": "2027-01-01T00:00:00.000Z", "grain": "year"},
            },
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_dates_interval():
    state = {
        "messages": [HumanMessage(content="nuclear energy from 1 january 2026 to 15 january 2026")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_interval_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"]["start_date"] == "2026-01-01"
    assert result["date_filter"]["end_date"] == "2026-01-15"


def test_parse_dates_single_year():
    state = {
        "messages": [HumanMessage(content="nuclear energy in 2024")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_point_year_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"]["start_date"] == "2024-01-01"
    assert result["date_filter"]["end_date"] == "2024-12-31"


def test_parse_dates_year_range():
    state = {
        "messages": [HumanMessage(content="nuclear energy publications in 2024-2026")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_interval_years_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    # exclusive 2027-01-01 → subtract 1 day → 2026-12-31 → year-grain end → 2026-12-31
    assert result["date_filter"]["start_date"] == "2024-01-01"
    assert result["date_filter"]["end_date"] == "2026-12-31"


def test_parse_dates_no_result():
    state = {
        "messages": [HumanMessage(content="tell me about nuclear energy")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"] == {}


def test_parse_dates_http_error():
    state = {
        "messages": [HumanMessage(content="nuclear energy in 2025")],
    }
    with patch("httpx.post", side_effect=Exception("connection refused")):
        result = ra.parse_dates(state)

    assert result["date_filter"] == {}


def test_parse_dates_no_messages():
    state = {"messages": []}
    result = ra.parse_dates(state)
    assert result["date_filter"] == {}


# ---------------------------------------------------------------------------
# apply_date_filter tests
# ---------------------------------------------------------------------------


def test_apply_date_filter_embeds_date_clause():
    state = {
        "arxiv_queries": ["nuclear reactor safety", "fusion energy"],
        "date_filter": {"start_date": "2026-01-01", "end_date": "2026-01-15"},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    plan = result["search_plan"]
    assert len(plan) == 2
    for task in plan:
        assert task["source"] == "arxiv"
        assert "+AND+submittedDate:[202601010000+TO+202601152359]" in task["query"]


def test_apply_date_filter_no_date_filter():
    state = {
        "arxiv_queries": ["neural networks", "deep learning"],
        "date_filter": {},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    plan = result["search_plan"]
    assert len(plan) == 2
    assert plan[0]["query"] == "neural networks"
    assert plan[1]["query"] == "deep learning"
    for task in plan:
        assert "submittedDate" not in task["query"]


def test_apply_date_filter_respects_max_searches():
    state = {
        "arxiv_queries": ["a", "b", "c", "d", "e"],
        "date_filter": {},
        "max_searches": 3,
    }
    result = ra.apply_date_filter(state)
    assert len(result["search_plan"]) == 3


def test_apply_date_filter_no_queries_blocks():
    state = {
        "arxiv_queries": [],
        "topic": "",
        "date_filter": {},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    assert result["blocked"] is True
    assert result["search_plan"] == []


# ---------------------------------------------------------------------------
# validate_date_range tests
# ---------------------------------------------------------------------------


def test_validate_date_range_keeps_in_range():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Paper A", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Paper A"


def test_validate_date_range_removes_out_of_range():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Old Paper", "url": "https://arxiv.org/abs/2312.99999", "snippet": ""},
            {"source": "arxiv", "title": "In Range", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert len(result["search_results"]) == 1
    assert result["search_results"][0]["title"] == "In Range"


def test_validate_date_range_no_date_filter_returns_empty():
    state = {
        "date_filter": {},
        "search_results": [
            {"source": "arxiv", "title": "Any Paper", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result == {}


def test_validate_date_range_all_removed_returns_originals():
    state = {
        "date_filter": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "search_results": [
            {"source": "arxiv", "title": "Old", "url": "https://arxiv.org/abs/2101.00001", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    # Returns {} so state is left unchanged (originals preserved)
    assert result == {}


def test_validate_date_range_keeps_non_arxiv():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "web", "title": "Web Result", "url": "https://example.com/page", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Web Result"


def test_validate_date_range_unparseable_url_kept():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Old Format", "url": "https://arxiv.org/abs/math/0501234", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Old Format"
