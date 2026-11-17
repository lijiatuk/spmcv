from PIL import Image

from app.services import ocr_service


def test_reconstruct_plate_city_letter(monkeypatch):
    monkeypatch.setattr(ocr_service, "_plate_city", lambda img: "晋D")
    img = Image.new("RGB", (100, 50))
    assert ocr_service._reconstruct_plate(img, "晋·08906D") == "晋D08906D"


def test_reconstruct_plate_requires_city(monkeypatch):
    monkeypatch.setattr(ocr_service, "_plate_city", lambda img: None)
    img = Image.new("RGB", (100, 50))
    assert ocr_service._reconstruct_plate(img, "晋·08906D") is None


def test_reconstruct_plate_rejects_other_province(monkeypatch):
    monkeypatch.setattr(ocr_service, "_plate_city", lambda img: "粤B")
    img = Image.new("RGB", (100, 50))
    assert ocr_service._reconstruct_plate(img, "晋·08906D") is None


