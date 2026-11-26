from forge_worker.brand import brand, user_agent


def test_brand_has_name_and_id() -> None:
    b = brand()
    assert b["name"] and b["id"].isidentifier()


def test_user_agent_is_app_name_only() -> None:
    ua = user_agent()
    assert ua.startswith(brand()["name"] + "/")
    assert "@" not in ua
