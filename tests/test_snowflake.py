from app.core.snowflake import Snowflake


def test_unique_and_increasing():
    s = Snowflake(mid=1, offset=1704067200000)
    ids = [s.generate() for _ in range(10000)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)


def test_fits_in_bigint():
    s = Snowflake(mid=1023, offset=1704067200000)
    for _ in range(100):
        assert s.generate() < 2**63


def test_worker_id_modulo_1023():
    s = Snowflake(mid=2000, offset=0)
    assert s.mid == 2000 % 1023


def test_same_ms_uses_sequence():
    s = Snowflake(mid=1, offset=0)
    a = s.generate()
    b = s.generate()
    # 同毫秒内序列递增：id 差应为 1
    assert b - a in (1, 1 + (1 << 12))
