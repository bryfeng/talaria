"""Contract tests to prevent watcher↔API endpoint drift."""

from talaria.server import app


def _route_methods() -> dict[str, set[str]]:
    routes: dict[str, set[str]] = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/static"):
            continue
        methods = set(rule.methods or set())
        routes.setdefault(rule.rule, set()).update(methods)
    return routes


def test_watcher_api_contract_routes_exist():
    routes = _route_methods()

    required = {
        "/api/board": {"GET"},
        "/api/card": {"POST"},
        "/api/card/<card_id>": {"GET", "PATCH", "DELETE"},
        "/api/card/<card_id>/note": {"POST"},
        "/api/arch/meta": {"GET"},
        "/api/agent_queue/compact": {"POST"},
    }

    missing = []
    for path, methods in required.items():
        actual = routes.get(path)
        if not actual:
            missing.append(f"{path} missing")
            continue
        if not methods.issubset(actual):
            missing.append(f"{path} missing methods {sorted(methods - actual)}")

    assert not missing, "Watcher/API contract drift detected: " + "; ".join(missing)
