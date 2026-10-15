"""Topic discovery finds what's missing without touching what's there.

What these pin: the pool is bookmarks that fit nothing well and that the user hasn't
answered (an answer is final; "unsure" isn't one); Haiku's proposals are only kept with
enough real pool examples and a new name, never one the user turned down; the counts the
user decides on (rescued, overlap, would move, disagrees with you) follow from the scores;
a candidate too small to matter is set aside; and a discovery run never edits topics.yaml.
"""

import json

import pytest

from jev_router import Taxonomy, app, desk, discover, notes, review, topics


def tax_at(tmp_path):
    t = [{"label": n, "destination": str(tmp_path / "vault" / n), **{k: topics.PRESETS[n][k] for k in ("question", "yes_means", "no_means")}}
         for n in ("AI", "Journalism")]
    topics.write_taxonomy(t, tmp_path / "topics.yaml")
    return Taxonomy.load(tmp_path / "topics.yaml")


def item(i, ai, jo, text="a post"):
    return {"id": str(i), "author": f"a{i}", "text": f"{text} {i}", "links": [], "image_text": None,
            "scores": {"ai": ai, "journalism": jo}}


def deck_of(items):
    return {"topics": [{"key": "ai", "label": "AI"}, {"key": "journalism", "label": "Journalism"}],
            "items": items, "topics_hash": "h"}


# ---------- the pool ----------

def test_pool_is_what_fits_nothing_well_and_has_no_answer():
    items = [item(1, 0.9, 0.1), item(2, 0.2, 0.1), item(3, 0.40, 0.1), item(4, 0.5, 0.1),
             item(5, 0.1, 0.1), item(6, 0.1, 0.2), item(7, 0.1, 0.1)]
    led = {"2": {"status": "skip"}, "4": {"status": "skip"}, "3": {"status": "route"}}
    answers = {"5": {"verdict": "drop"}, "6": {"verdict": "ai"}, "7": {"verdict": "unsure"}}
    got = [it["id"] for it in discover.pool(deck_of(items), answers, led)]
    # 1 fits well; 2 in Unsorted; 3 filed on a weak fit; 4 in Unsorted though it scores better today;
    # 5 and 6 answered; 7 unsure is not an answer
    assert got == ["2", "3", "4", "7"]


def test_gist_carries_links_and_image_text():
    it = item(1, 0, 0) | {"links": [{"title": "ggplot2 cheatsheet"}], "image_text": "a bar chart"}
    g = discover.gist(it)
    assert "a post 1" in g and "ggplot2 cheatsheet" in g and "a bar chart" in g


# ---------- proposals ----------

def raw(label, examples, **kw):
    return {"label": label, "use": "learn charts", "question": f"Is this about {label}?", "yes_means": "y",
            "no_means": "n", "examples": examples, **kw}


def test_only_well_formed_proposals_survive(tmp_path):
    tax = tax_at(tmp_path)
    pool_ids = {str(i) for i in range(10)}
    five = [str(i) for i in range(5)]
    got = discover.clean({"topics": [
        raw("Data viz", five + ["999"]),            # kept; the made-up id is dropped
        raw("Humor", ["1", "2", "3", "4", "999"]),  # only 4 real examples
        raw("ai", five),                            # an existing topic
        raw("Cooking", five),                       # turned down before
        raw("data viz", five),                      # same name twice
        raw("Music", five), raw("Politics", five),  # beyond the limit of 3 proposals read
    ]}, pool_ids, tax, [{"label": "Cooking"}])
    assert [c["label"] for c in got] == ["Data viz"]
    assert got[0]["key"] == "new_data_viz" and got[0]["examples"] == five


def test_the_prompt_shows_existing_and_turned_down_topics(tmp_path):
    tax = tax_at(tmp_path)
    p = discover.prompt([item(1, 0.1, 0.1)], tax, [{"label": "Cooking", "use": "recipes"}])
    body = json.loads(p[p.index("{"):])
    assert [t["topic"] for t in body["existing_topics"]] == ["AI", "Journalism"]
    assert body["turned_down"] == [{"topic": "Cooking", "use": "recipes"}]
    assert body["unfiled"][0]["id"] == "1"


# ---------- the check ----------

def test_assess_counts_follow_from_the_scores():
    items = [item(i, 0.1, 0.1) for i in range(1, 7)] + [item(7, 0.8, 0.1), item(8, 0.5, 0.1),
                                                        item(9, 0.2, 0.9), item(10, 0.1, 0.1)]
    pool_ids = {str(i) for i in range(1, 7)}
    led = {"7": {"status": "route", "bucket": "ai"}, "8": {"status": "route", "bucket": "ai"},
           "9": {"status": "route", "bucket": "journalism"}}
    answers = {"10": {"verdict": "drop"}, "8": {"verdict": "ai"}}
    k = "new_viz"
    scores = {"1": {k: 0.9}, "2": {k: 0.8}, "3": {k: 0.7}, "4": {k: 0.6}, "5": {k: 0.2}, "6": {k: 0.35},
              "7": {k: 0.4}, "8": {k: 0.6}, "9": {k: 0.1}, "10": {k: 0.5}}
    cand = {"key": k, "label": "Viz", "examples": ["1", "2", "5"]}
    [c] = discover.assess([cand], scores, deck_of(items), answers, led, pool_ids, 0.3)
    assert c["rescued"] == ["1", "2", "3", "4", "6"] and c["kept"]
    assert c["agreed"] == 2  # Jev agreed on 1 and 2 of Haiku's examples, not 5
    assert c["overlap"] == {"AI": 2} and c["moves"] == ["8"]  # 7 fits too but AI still wins there
    assert sorted((x["id"], x["said"]) for x in c["conflicts"]) == [("10", "not worth keeping"), ("8", "AI")]


def test_a_candidate_that_rescues_too_few_is_set_aside():
    items = [item(i, 0.1, 0.1) for i in range(1, 7)]
    k = "new_x"
    scores = {str(i): {k: 0.9 if i <= discover.MIN_RESCUED - 1 else 0.1} for i in range(1, 7)}
    [c] = discover.assess([{"key": k, "label": "X", "examples": []}], scores, deck_of(items), {}, {},
                          {str(i) for i in range(1, 7)}, 0.3)
    assert not c["kept"]
    result = {"pool": [str(i) for i in range(1, 7)], "candidates": [c], "missed": []}
    out = discover.text(result, {it["id"]: it for it in items})
    assert "None held up" in out and "Set aside (too few rescued): X (4)" in out


# ---------- the whole run ----------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(app.HOME_ENV, str(tmp_path))
    tax_at(tmp_path)
    app.save_config({"taxonomy": "topics.yaml"})
    monkeypatch.setattr(app, "haiku_env", lambda cfg: None)
    return tmp_path


def fake_world(monkeypatch, items, proposal, cand_scores):
    deck = deck_of(items)
    monkeypatch.setattr(review, "build", lambda cfg, say=print, reuse=False: deck)
    monkeypatch.setattr(review, "collect_bookmarks", lambda work: {it["id"]: {"id": it["id"]} for it in items})
    calls = []

    def haiku(content, schema=None):
        calls.append(content[0]["text"])
        return proposal, {"input_tokens": 1}
    monkeypatch.setattr(notes, "haiku", haiku)
    monkeypatch.setattr(discover, "score", lambda cands, bms, model, line: (cand_scores, 123))
    return calls


def test_run_reports_saves_and_leaves_topics_alone(home, monkeypatch):
    items = [item(i, 0.1, 0.1, "ggplot tip") for i in range(1, 8)] + [item(8, 0.9, 0.1)]
    ids = [str(i) for i in range(1, 8)]
    fake_world(monkeypatch, items, {"topics": [raw("Data viz", ids)]},
               {i: {"new_data_viz": 0.8} for i in ids} | {"8": {"new_data_viz": 0.1}})
    before = (home / "topics.yaml").read_text(encoding="utf-8")
    out = []
    result = discover.run(app.load_config(), say=out.append)
    assert result["candidates"][0]["rescued"] == ids and result["missed"] == []
    text = "\n".join(out)
    assert "Data viz: learn charts" in text and "Would rescue 7 of the 7" in text
    assert (home / "topics.yaml").read_text(encoding="utf-8") == before
    saved = list(discover.runs_dir().glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8"))["jev_tokens"] == 123


def test_run_with_a_small_pool_makes_no_model_call(home, monkeypatch):
    calls = fake_world(monkeypatch, [item(1, 0.1, 0.1), item(2, 0.9, 0.1)], {"topics": []}, {})
    out = []
    assert discover.run(app.load_config(), say=out.append) is None
    assert not calls and "Too few" in "\n".join(out)


def test_pool_only_is_free(home, monkeypatch):
    calls = fake_world(monkeypatch, [item(i, 0.1, 0.1) for i in range(1, 8)], {"topics": []}, {})
    out = []
    assert discover.run(app.load_config(), say=out.append, pool_only=True) is None
    assert not calls and "Pool: 7 bookmarks" in out[0]


def test_answers_on_the_desk_leave_the_pool(home, monkeypatch):
    items = [item(i, 0.1, 0.1) for i in range(1, 8)]
    fake_world(monkeypatch, items, {"topics": []}, {})
    store = desk.Verdicts(review.verdicts_path())
    for i in ("1", "2", "3"):
        store.put(i, {"verdict": "drop", "source": "vault"})
    out = []
    discover.run(app.load_config(), say=out.append, pool_only=True)
    assert "Pool: 4 bookmarks" in out[0] and "not counting 3 you said aren't worth keeping" in out[0]
