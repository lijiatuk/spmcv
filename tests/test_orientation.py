from app.api.analyze import _box_to_original


def test_identity_when_not_rotated():
    assert _box_to_original([10, 20, 30, 40], 0, 100, 200) == [10, 20, 30, 40]


def test_rotate_180_roundtrip():
    # Original box (10,20)-(30,40) in a 100x200 image rotated 180.
    rotated = [100 - 30, 200 - 40, 100 - 10, 200 - 20]
    assert _box_to_original(rotated, 180, 100, 200) == [10, 20, 30, 40]


def test_rotate_90_roundtrip():
    # rotate(90): (x,y) -> (y, W-x); box inverse must land back on the original.
    w, h = 100, 200
    original = [10, 20, 30, 40]
    x1, y1, x2, y2 = original
    rotated = [y1, w - x2, y2, w - x1]
    assert _box_to_original(rotated, 90, w, h) == original


def test_rotate_270_roundtrip():
    # rotate(270): (x,y) -> (H-y, x)
    w, h = 100, 200
    original = [10, 20, 30, 40]
    x1, y1, x2, y2 = original
    rotated = [h - y2, x1, h - y1, x2]
    assert _box_to_original(rotated, 270, w, h) == original
