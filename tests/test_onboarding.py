from conftest import signup


def test_a_new_workspace_is_not_onboarded_and_an_admin_can_finish_it(client):
    owner = signup(client, "o@x.com")
    assert owner.get("/api/settings").json()["onboarded"] is False
    assert owner.put("/api/settings", json={"onboarded": True}).json()["onboarded"] is True
    assert owner.get("/api/settings").json()["onboarded"] is True


def test_members_and_viewers_cannot_change_setup_but_can_read_it(client):
    owner, member = signup(client, "o@x.com"), signup(client, "m@x.com")
    tok = owner.post("/api/team/invites", json={"email": "m@x.com", "role": "member"}).json()["link"].rsplit("/", 1)[1]
    member.post("/api/auth/invites/accept", json={"token": tok})
    assert member.get("/api/settings").json()["onboarded"] is False
    assert member.put("/api/settings", json={"onboarded": True}).status_code == 403


def test_setting_one_thing_does_not_reset_another(client):
    owner = signup(client, "o@x.com")
    owner.put("/api/settings", json={"auto_analyze": False})
    s = owner.put("/api/settings", json={"onboarded": True}).json()
    assert s == {"auto_analyze": False, "onboarded": True}
