from __future__ import annotations

from app.label_filters import LabelFilters


def test_parse_none_returns_empty() -> None:
    filters = LabelFilters.parse(None)
    assert filters.shared == {}
    assert filters.per_metric == {}
    assert filters.is_empty() is True


def test_parse_flat_json_string_becomes_shared() -> None:
    filters = LabelFilters.parse('{"job": "api", "env": "prod"}')
    assert filters.shared == {"job": "api", "env": "prod"}
    assert filters.per_metric == {}


def test_parse_nested_per_metric_form() -> None:
    raw = '{"up": {"job": "api"}, "node_cpu_seconds_total": {"instance": "a"}}'
    filters = LabelFilters.parse(raw)
    assert filters.shared == {}
    assert filters.per_metric == {
        "up": {"job": "api"},
        "node_cpu_seconds_total": {"instance": "a"},
    }


def test_parse_star_sentinel_lifts_to_shared() -> None:
    raw = '{"*": {"env": "prod"}, "up": {"job": "api"}}'
    filters = LabelFilters.parse(raw)
    assert filters.shared == {"env": "prod"}
    assert filters.per_metric == {"up": {"job": "api"}}


def test_parse_accepts_pre_decoded_dict() -> None:
    filters = LabelFilters.parse({"up": {"job": "api"}})
    assert filters.per_metric == {"up": {"job": "api"}}


def test_parse_invalid_json_returns_empty() -> None:
    assert LabelFilters.parse("not json").is_empty()


def test_parse_non_dict_json_returns_empty() -> None:
    assert LabelFilters.parse("[1,2,3]").is_empty()
    assert LabelFilters.parse('"plain string"').is_empty()


def test_parse_drops_empty_keys_and_values() -> None:
    raw = '{"": "skip", "job": "", "env": "prod"}'
    filters = LabelFilters.parse(raw)
    assert filters.shared == {"env": "prod"}


def test_parse_drops_non_string_per_metric_values() -> None:
    raw = '{"up": {"job": 42, "env": "prod"}}'
    filters = LabelFilters.parse(raw)
    assert filters.per_metric == {"up": {"env": "prod"}}


def test_to_json_empty() -> None:
    assert LabelFilters().to_json() == "{}"


def test_to_json_shared_only_keeps_flat_wire_format() -> None:
    filters = LabelFilters(shared={"env": "prod"})
    assert filters.to_json() == '{"env":"prod"}'


def test_to_json_per_metric_only() -> None:
    filters = LabelFilters(per_metric={"up": {"job": "api"}})
    assert filters.to_json() == '{"up":{"job":"api"}}'


def test_to_json_mixed_uses_star_sentinel() -> None:
    filters = LabelFilters(
        shared={"env": "prod"},
        per_metric={"up": {"job": "api"}},
    )
    assert filters.to_json() == '{"*":{"env":"prod"},"up":{"job":"api"}}'


def test_parse_to_json_roundtrip_stable() -> None:
    original = '{"*":{"env":"prod"},"up":{"job":"api"}}'
    assert LabelFilters.parse(original).to_json() == original


def test_for_metric_returns_shared_when_no_per_metric() -> None:
    filters = LabelFilters(shared={"env": "prod"})
    assert filters.for_metric("up") == {"env": "prod"}


def test_for_metric_returns_per_metric_when_no_shared() -> None:
    filters = LabelFilters(per_metric={"up": {"job": "api"}})
    assert filters.for_metric("up") == {"job": "api"}
    assert filters.for_metric("not_present") == {}


def test_for_metric_merges_shared_with_per_metric() -> None:
    filters = LabelFilters(
        shared={"env": "prod"},
        per_metric={"up": {"job": "api"}},
    )
    assert filters.for_metric("up") == {"env": "prod", "job": "api"}


def test_for_metric_per_metric_overrides_shared() -> None:
    filters = LabelFilters(
        shared={"env": "prod"},
        per_metric={"up": {"env": "staging"}},
    )
    assert filters.for_metric("up") == {"env": "staging"}


def test_resolve_bulk() -> None:
    filters = LabelFilters(
        shared={"env": "prod"},
        per_metric={"up": {"job": "api"}, "ignore_me": {"job": "x"}},
    )
    assert filters.resolve(["up", "node_cpu_seconds_total"]) == {
        "up": {"env": "prod", "job": "api"},
        "node_cpu_seconds_total": {"env": "prod"},
    }


def test_resolve_empty_metric_list() -> None:
    assert LabelFilters(shared={"env": "prod"}).resolve([]) == {}
