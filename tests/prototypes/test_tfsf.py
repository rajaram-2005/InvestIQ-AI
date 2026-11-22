from dataclasses import replace
import numpy as np
import pytest
from investiq.prototypes import tfsf


def obs(day, value, var=1.0):
    return tfsf.Observation(f"2020-01-{day:02d}T00:00:00Z", {"income": value}, {"income": var} if value is not None else {})


def test_missing_propagation_and_history():
    snapshots = tfsf.fuse([obs(1, 100), obs(2, None)], tfsf.Config(process_variance=4))
    assert snapshots[1].mean == snapshots[0].mean
    assert snapshots[1].variance[0] == pytest.approx(snapshots[0].variance[0] + 4)
    assert snapshots[1].observed == () and snapshots[0].mean[0] != 0
    with pytest.raises(Exception):
        snapshots[0].timestamp = "changed"


def test_uncertainty_and_change_detection():
    inputs = [obs(1, 100, 1), obs(2, 200, 1)]
    snapshots = tfsf.fuse(inputs, tfsf.Config(process_variance=0, change_z=3))
    assert snapshots[1].changes == ("income",)
    assert tfsf.fuse(inputs, tfsf.Config(process_variance=0, detect_changes=False))[1].changes == ()
    assert tfsf.fuse([obs(1, 100, 1)])[0].variance[0] < tfsf.fuse([obs(1, 100, 1000)])[0].variance[0]


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError): tfsf.fuse([obs(2, 1), obs(1, 1)])
    with pytest.raises(ValueError): tfsf.fuse([obs(1, 2, 0)])
    with pytest.raises(ValueError): tfsf.fuse([tfsf.Observation("2020-01-01", {"income": 1}, {"income": 1})])
    with pytest.raises(ValueError): tfsf.fuse([tfsf.Observation("2020-01-01T00:00:00Z", {"unknown": 1}, {"unknown": 1})])
    with pytest.raises(ValueError): tfsf.fuse([obs(1, 2), replace(obs(2, 3), currency="EUR")])
    with pytest.raises(ValueError): tfsf.fuse([replace(obs(1, 2), currency="U1D")])


def test_baseline_and_ground_truth_replay():
    result = tfsf.benchmark(1, 30)
    assert result == tfsf.benchmark(1, 30)
    assert len(result["truth"]) == len(result["snapshots"]) == 30
    expected = np.mean(np.abs(np.array(result["truth"]) - np.array([x["mean"] for x in result["snapshots"]])))
    assert result["mae"] == pytest.approx(expected)
    assert 0 <= result["coverage_90"] <= 1
