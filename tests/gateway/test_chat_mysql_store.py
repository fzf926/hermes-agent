from decimal import Decimal

from gateway.chat_mysql_store import validate_sql_favorite_eligibility


def test_sql_favorite_allows_successful_queries_under_five_seconds():
    error = validate_sql_favorite_eligibility(
        [
            {"id": 1, "status": "success", "query_time_ms": Decimal("1200.500")},
            {"id": 2, "status": "success", "query_time_ms": 5000},
        ]
    )

    assert error is None


def test_sql_favorite_rejects_missing_sql_executions():
    error = validate_sql_favorite_eligibility([])

    assert error == "No SQL executions found for this turn"


def test_sql_favorite_rejects_failed_sql_execution():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "error", "query_time_ms": 100}]
    )

    assert "Only successful SQL executions can be favorited" in error


def test_sql_favorite_rejects_null_query_time():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "success", "query_time_ms": None}]
    )

    assert "missing query_time_ms" in error


def test_sql_favorite_rejects_query_over_five_seconds():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "success", "query_time_ms": Decimal("5000.001")}]
    )

    assert "within 5 seconds" in error
