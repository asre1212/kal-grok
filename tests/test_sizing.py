from kalbot.sizing import kelly_fraction_binary, kelly_contracts, cap_contracts


def test_kelly_formula():
    # p=0.6 at cost 0.5: f* = (0.6-0.5)/(0.5) = 0.2
    assert abs(kelly_fraction_binary(0.6, 0.5) - 0.2) < 1e-12
    assert kelly_fraction_binary(0.5, 0.5) == 0
    assert kelly_fraction_binary(0.4, 0.5) == 0


def test_fractional_kelly_contracts():
    # quarter Kelly of $10k at f*=0.2 -> $500 / 0.5 = 1000 contracts
    assert kelly_contracts(0.6, 0.5, 0.0, 10_000, 0.25) == 1000
    assert kelly_contracts(0.6, 0.5, 0.0, 10_000, 0.25, dd_mult=0.5) == 500
    assert kelly_contracts(0.55, 0.5, 0.06, 10_000) == 0      # fees kill the edge


def test_caps():
    assert cap_contracts(1000, 0.5, 100, 400) == 200
    assert cap_contracts(10, 0.5, 100) == 10
    assert cap_contracts(10, 0.5, -1) == 0
