from quality_cal.config import build_pressure_points


def test_build_pressure_points_uses_default_schedule():
    points = build_pressure_points({})

    assert points[0] == 0.05
    assert 30.0 in points
    assert points[-1] == 30.0
    assert len(points) == 31


def test_build_pressure_points_uses_explicit_list():
    points = build_pressure_points({"pressure_points_psia": [10, 5, 5, 20]})

    assert points == [5.0, 10.0, 20.0]
