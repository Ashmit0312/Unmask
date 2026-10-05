import pandas as pd

from trustscore.realdata import normalize_values


def _fb(tag, values):
    return pd.DataFrame({"agent_id": range(len(values)), "client": "c", "tag1": tag, "value": values})


def test_percent_tags_stay_as_they_are():
    out = normalize_values(_fb("quality", [0.0, 40.0, 100.0]))
    assert out.value.tolist() == [0.0, 40.0, 100.0]


def test_other_scales_are_rescaled_per_tag():
    out = normalize_values(pd.concat([_fb("stars", [1.0, 3.0, 5.0]), _fb("rps", [-1.0, 1.0, 1.0])]))
    stars = out[out.tag1 == "stars"].value.round(1).tolist()
    rps = out[out.tag1 == "rps"].value.round(1).tolist()
    assert stars[0] < 5 and stars[-1] > 95 and 45 < stars[1] < 55
    assert rps[0] < 5 and rps[1] > 95


def test_constant_tags_kept_only_when_already_percent():
    out = normalize_values(pd.concat([_fb("auto", [80.0] * 4), _fb("count", [20521.0] * 3)]))
    assert out.tag1.tolist() == ["auto"] * 4 and (out.value == 80).all()


def test_raw_value_is_kept():
    out = normalize_values(_fb("stars", [1.0, 5.0]))
    assert out.value_raw.tolist() == [1.0, 5.0]


def test_spam_values_in_a_percent_tag_are_dropped():
    out = normalize_values(_fb("eve", [100.0] * 30 + [1e38]))
    assert len(out) == 30 and (out.value == 100).all()
