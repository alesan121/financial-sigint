import pytest
from shared.math import compute_kelly_fraction

def test_compute_kelly_fraction_basic():
    # p=0.6, b=2.0 -> f* = (0.6*2 - 0.4)/2 = (1.2 - 0.4)/2 = 0.8/2 = 0.40
    # Capped at 0.25
    f, path = compute_kelly_fraction(p=0.6, entry=100, tp=120, sl=90)
    assert f == 0.25
    assert "p=0.600" in path
    assert "=2.000" in path

def test_compute_kelly_fraction_low_p():
    # p=0.3, b=1.0 -> f* = (0.3*1 - 0.7)/1 = -0.4
    # Expected: 0.0 (no investment)
    f, path = compute_kelly_fraction(p=0.3, entry=100, tp=110, sl=90)
    assert f == 0.0

def test_compute_kelly_fraction_zero_risk():
    # risk = entry - sl = 100 - 100 = 0
    # Expected: 0.0 (failsafe)
    f, path = compute_kelly_fraction(p=0.6, entry=100, tp=110, sl=100)
    assert f == 0.0
    assert "b=undefined" in path

def test_compute_kelly_fraction_zero_reward():
    # reward = tp - entry = 100 - 100 = 0
    # Expected: 0.0 (failsafe)
    f, path = compute_kelly_fraction(p=0.6, entry=100, tp=100, sl=90)
    assert f == 0.0
    assert "b=undefined" in path

def test_compute_kelly_fraction_custom_cap():
    # p=0.6, b=2.0 -> f* = 0.40
    # Custom cap at 1.0
    f, path = compute_kelly_fraction(p=0.6, entry=100, tp=120, sl=90, max_kelly=1.0)
    assert pytest.approx(f) == 0.40
