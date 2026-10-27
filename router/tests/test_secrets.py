"""Secrets: Credential Manager first, the old store as the fallback, and nothing stale wins.

What these pin: a secret saved where Credential Manager works lands there and its plain copy
goes (user environment variables reach every new program first, so a leftover would win);
when Credential Manager doesn't keep it, the old store does, and runs still find it; the live
environment still beats both; our namespaced X login beats a plain AUTH_TOKEN an older setup
left (it goes stale the first time the login is redone); moving deletes a plain copy only
after the keychain copy reads back, and never a legacy name other X tools read; a test install
shares one entry per secret with every other install. Credential Manager and the registry are faked throughout.
"""

import pytest

from jev_router import app

NAMES = list(app.SECRETS.values()) + list(app.LEGACY_X)


class FakeKeychain:
    priority = 5

    def __init__(self, broken=False):
        self.store, self.broken = {}, broken

    def set_password(self, service, var, value):
        if not self.broken:
            self.store[(service, var)] = value

    def get_password(self, service, var):
        return self.store.get((service, var))

    def delete_password(self, service, var):
        del self.store[(service, var)]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A Windows user with a fake registry and Credential Manager, and a clean environment."""
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(app.sys, "platform", "win32")
    for n in NAMES:
        monkeypatch.delenv(n, raising=False)
    reg, kc = {}, FakeKeychain()
    monkeypatch.setattr(app, "_keychain", lambda: kc)
    monkeypatch.setattr(app, "_user_var", lambda v: reg.get(v))
    monkeypatch.setattr(app, "_set_user_var", lambda v, val: reg.__setitem__(v, val))
    monkeypatch.setattr(app, "delete_user_var", lambda v: (reg.pop(v), app.os.environ.pop(v, None)))
    return {"reg": reg, "kc": kc, "mp": monkeypatch}


def fresh_process(m):
    """What a new program (the scheduled run) starts with: the user variables, nothing else."""
    for n in NAMES:
        m["mp"].delenv(n, raising=False)
    for k, v in m["reg"].items():
        m["mp"].setenv(k, v)
    app.load_secrets()


def test_saved_to_credential_manager_and_the_plain_copy_goes(machine):
    machine["reg"]["TYPESAFE_API_KEY"] = "old"
    assert app.save_secret("TYPESAFE_API_KEY", "new") == app.KEYCHAIN
    assert machine["reg"] == {}
    fresh_process(machine)
    assert app.os.environ["TYPESAFE_API_KEY"] == "new"
    assert app.secret_home("TYPESAFE_API_KEY") == app.KEYCHAIN


def test_when_credential_manager_does_not_keep_it_the_old_store_does(machine, monkeypatch):
    monkeypatch.setattr(app, "_keychain", lambda: FakeKeychain(broken=True))
    assert app.save_secret("TYPESAFE_API_KEY", "k") == "your Windows user environment variables"
    fresh_process(machine)
    assert app.os.environ["TYPESAFE_API_KEY"] == "k"
    monkeypatch.setattr(app, "_keychain", lambda: None)  # no keyring at all
    assert app.save_secret("TYPESAFE_API_KEY", "k2") == "your Windows user environment variables"


def test_the_live_environment_still_wins(machine):
    app.save_secret("TYPESAFE_API_KEY", "saved")
    machine["mp"].setenv("TYPESAFE_API_KEY", "typed in this terminal")
    app.load_secrets()
    assert app.os.environ["TYPESAFE_API_KEY"] == "typed in this terminal"


def test_our_x_login_beats_a_stale_plain_auth_token(machine):
    machine["reg"].update({"AUTH_TOKEN": "stale", "CT0": "stale-ct0"})
    app.save_secret("TWITTER_AUTH_TOKEN", "fresh")
    app.save_secret("TWITTER_CT0", "fresh-ct0")
    fresh_process(machine)
    assert app.os.environ["AUTH_TOKEN"] == "fresh" and app.os.environ["CT0"] == "fresh-ct0"


def test_a_plain_auth_token_alone_still_works(machine):
    machine["reg"].update({"AUTH_TOKEN": "a", "CT0": "c"})
    fresh_process(machine)
    assert app.os.environ["AUTH_TOKEN"] == "a" and app.os.environ["CT0"] == "c"


def test_moving_deletes_the_plain_copy_only_after_it_reads_back(machine, monkeypatch):
    machine["reg"].update({"TYPESAFE_API_KEY": "k", "AUTH_TOKEN": "a"})
    assert app.move_to_keychain("TYPESAFE_API_KEY") and "TYPESAFE_API_KEY" not in machine["reg"]
    assert app.move_to_keychain("AUTH_TOKEN", "TWITTER_AUTH_TOKEN")
    assert machine["reg"] == {"AUTH_TOKEN": "a"}  # bird and other X tools read that name: the user decides
    assert app.keychain_get("TWITTER_AUTH_TOKEN") == "a"
    machine["reg"]["ANTHROPIC_API_KEY"] = "x"
    monkeypatch.setattr(app, "_keychain", lambda: FakeKeychain(broken=True))
    assert not app.move_to_keychain("ANTHROPIC_API_KEY") and machine["reg"]["ANTHROPIC_API_KEY"] == "x"


def test_a_save_from_a_test_install_still_reaches_the_real_one(machine, tmp_path, monkeypatch):
    # entries per install broke this: the test install's save deleted the shared plain copy
    machine["reg"]["TYPESAFE_API_KEY"] = "k"
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path / ".scatha-test"))
    app.save_secret("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path / app.HOME_DIRNAME))
    fresh_process(machine)
    assert app.os.environ["TYPESAFE_API_KEY"] == "k"


def test_kept_plain_x_names_can_still_be_removed_later(machine, monkeypatch):
    # 2026-10-27: login copied, AUTH_TOKEN/CT0 kept; status kept saying "run setup secrets", which then had nothing to do
    from jev_router import onboard
    machine["reg"].update({"AUTH_TOKEN": "a", "CT0": "c"})
    assert app.plain_secrets() == {"move": [], "copy": ["AUTH_TOKEN", "CT0"], "leftover": []}
    monkeypatch.setattr(onboard, "yes", lambda prompt, default=True: default)  # Enter on every question
    onboard.step_secrets()
    assert machine["reg"] == {"AUTH_TOKEN": "a", "CT0": "c"} and app.keychain_get("TWITTER_CT0") == "c"
    assert app.plain_secrets() == {"move": [], "copy": [], "leftover": ["AUTH_TOKEN", "CT0"]}
    asked = []
    monkeypatch.setattr(onboard, "yes", lambda prompt, default=True: asked.append(prompt) or True)
    onboard.step_secrets()  # the second time, it offers the removal
    assert asked and "Remove AUTH_TOKEN and CT0" in asked[0] and machine["reg"] == {}
    assert not any(app.plain_secrets().values())


def test_a_broken_keyring_never_breaks_a_run(machine, monkeypatch):
    class Exploding(FakeKeychain):
        def get_password(self, service, var):
            raise RuntimeError("locked")
    monkeypatch.setattr(app, "_keychain", lambda: Exploding())
    machine["reg"]["TYPESAFE_API_KEY"] = "k"
    fresh_process(machine)
    assert app.os.environ["TYPESAFE_API_KEY"] == "k"


# 2026-10-27 pre-release review, finding 4: renewing a login tested the saved pair, not the new one,
# and a rejected API key could never be replaced. The fetcher and Haiku are faked.

def x_world(machine, monkeypatch, works):
    from types import SimpleNamespace
    from jev_router import checks, onboard
    app.save_secret("TWITTER_AUTH_TOKEN", "expired-auth")
    app.save_secret("TWITTER_CT0", "expired-ct0")
    fresh_process(machine)
    seen = []

    def fetcher(cmd, **kw):
        pair = (kw["env"]["AUTH_TOKEN"], kw["env"]["CT0"])
        seen.append(pair)
        return SimpleNamespace(stdout="user: @me" if pair in works else "", returncode=0)
    monkeypatch.setattr(checks.subprocess, "run", fetcher)
    return seen, {"tools": {"node": "node", "bird": "bird"}}


def test_new_x_cookies_are_the_ones_tested_and_then_saved(machine, monkeypatch):
    from jev_router import onboard
    seen, cfg = x_world(machine, monkeypatch, works={("fresh-auth", "fresh-ct0")})
    pastes = iter(["fresh-auth", "fresh-ct0"])
    monkeypatch.setattr(onboard, "secret", lambda prompt: next(pastes))
    assert onboard.step_x(cfg) is True
    assert seen == [("expired-auth", "expired-ct0"), ("fresh-auth", "fresh-ct0")]  # the saved pair is checked once, first
    fresh_process(machine)
    assert (app.os.environ["TWITTER_AUTH_TOKEN"], app.os.environ["AUTH_TOKEN"]) == ("fresh-auth", "fresh-auth")


def test_a_bad_paste_leaves_the_saved_cookies_alone(machine, monkeypatch):
    from jev_router import onboard
    seen, cfg = x_world(machine, monkeypatch, works=set())
    monkeypatch.setattr(onboard, "secret", lambda prompt: "typo-" + prompt)
    assert onboard.step_x(cfg) is False
    assert seen[0] == ("expired-auth", "expired-ct0") and set(seen[1:]) == {("typo-auth_token", "typo-ct0")}
    fresh_process(machine)
    assert (app.os.environ["TWITTER_AUTH_TOKEN"], app.os.environ["TWITTER_CT0"]) == ("expired-auth", "expired-ct0")


def api_world(machine, monkeypatch, good):
    from jev_router import onboard
    app.save_secret("ANTHROPIC_API_KEY", "old-key")
    fresh_process(machine)
    monkeypatch.setattr(onboard, "verify_notes",
                        lambda cfg: None if app.os.environ.get("ANTHROPIC_API_KEY") == good else "invalid API key")
    asked = []
    monkeypatch.setattr(onboard, "secret", lambda prompt: asked.append(prompt) or "new-key")
    return onboard, asked, {"notes_backend": "anthropic-api", "tools": {}}


def test_a_rejected_api_key_can_be_replaced(machine, monkeypatch):
    onboard, asked, cfg = api_world(machine, monkeypatch, good="new-key")
    assert onboard.step_notes(cfg, preset="anthropic-api") is True and asked
    fresh_process(machine)
    assert app.os.environ["ANTHROPIC_API_KEY"] == "new-key"


def test_a_replacement_that_also_fails_keeps_the_saved_key(machine, monkeypatch):
    onboard, asked, cfg = api_world(machine, monkeypatch, good="neither")
    assert onboard.step_notes(cfg, preset="anthropic-api") is False and len(asked) == 3
    fresh_process(machine)
    assert app.os.environ["ANTHROPIC_API_KEY"] == "old-key"


def test_a_working_api_key_is_kept_without_asking(machine, monkeypatch):
    onboard, asked, cfg = api_world(machine, monkeypatch, good="old-key")
    assert onboard.step_notes(cfg, preset="anthropic-api") is True and asked == []
