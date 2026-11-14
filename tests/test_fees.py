from kalbot.fees import taker_fee, maker_fee, fee_per_contract


def test_taker_fee_examples_from_schedule():
    # 100 contracts @ 50c: 0.07*100*.25 = $1.75 ; @ 1c: 0.07*100*.0099 = 0.0693 -> $0.07
    assert taker_fee(100, 0.50) == 1.75
    assert taker_fee(100, 0.01) == 0.07
    assert taker_fee(1, 0.50) == 0.02          # 0.0175 rounds UP to 2c
    assert taker_fee(10, 0.30) == 0.15         # 0.147 -> 0.15


def test_fee_multiplier_and_zero():
    assert taker_fee(100, 0.5, multiplier=0.5) == 0.88   # 0.875 -> 0.88
    assert taker_fee(0, 0.5) == 0.0
    assert taker_fee(100, 0.5, multiplier=0) == 0.0


def test_maker_fee_only_on_maker_series():
    assert maker_fee(100, 0.5, fee_type="quadratic") == 0.0
    assert maker_fee(100, 0.5, fee_type="quadratic_with_maker_fees") == 0.44  # 0.4375 -> 0.44


def test_fee_symmetry_and_peak():
    assert abs(fee_per_contract(0.2) - fee_per_contract(0.8)) < 1e-12
    assert fee_per_contract(0.5) > fee_per_contract(0.1)
