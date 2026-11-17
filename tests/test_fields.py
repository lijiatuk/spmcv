from app.services.fields import parse_fields, parse_plate


FRAME_VIN_TEXT = """出厂编号 T3305296
驱动电机型号 TZ310XS-LKM0825(2)
牵引座最大设计静载荷15070kg最大允许牵引质
整车型号HFC4259EV15
品牌：江淮车辆识别代号：LJ1ER4DU5T
中国 安徽江淮汽车集团股份有限公司
制造年月2026年5
动力电池系统额定电压618.2 V动力电池系统额定容量 654 Ah
"""

SERVICE_SHEET_TEXT = """DEEPWAY首/定保检查作业项目表
车辆牌期：晋K08469D
车辆VIN:3307010
车型：
1.检查动力电池箱体，清洁箱体应无明显灰尘堵塞。
9.检查电池冷却系统、管路及控制系统 Chiller 相关管路
"""


def test_frame_vin_reconstructed_from_serial():
    fields = parse_fields(FRAME_VIN_TEXT, keep=("vin",), require_vin_label=True)
    assert fields["vin"] == "LJ1ER4DU5T3305296"


def test_service_sheet_keeps_partial_vin_only():
    fields = parse_fields(SERVICE_SHEET_TEXT, keep=("vin",), require_vin_label=True)
    assert fields["vin"] is None
    assert fields["vin_partial"] == "3307010"


def test_vin_check_digit_rejects_template_text():
    # 2345AMT6789TCU10T can pass the check digit by chance, but it sits in body
    # text on another line. Once the VIN label is found, only that line is used.
    text = """6.更换润滑油 2345AMT6789TCU10T
车辆VIN:3307010
"""
    fields = parse_fields(text, keep=("vin",), require_vin_label=True)
    assert fields["vin"] is None
    assert fields["vin_partial"] == "3307010"


def test_plate_split_and_reversed_order():
    assert parse_plate("43573D\n粤B")[0] == "粤B43573D"


def test_plate_plain():
    assert parse_plate("车牌：皖H·04565D")[0] == "皖H04565D"


def test_odometer_ignores_range_and_voltage():
    fields = parse_fields("续航 311km 电压 618.2V 里程 12566 km", keep=("odometer",))
    assert fields["odometer"] == 12566


def test_license_vin_standalone_line_without_check_digit():
    # Some Chinese commercial VINs do not honour the ISO check digit; the
    # standalone 17-char line still wins over sliding-window garbage.
    text = """EMLK260427019
HLC3J2VE3TA683182
脑牌HLD425ZTSE9BEV
"""
    fields = parse_fields(text, keep=("vin",))
    assert fields["vin"] == "HLC3J2VE3TA683182"


def test_vin_shape_rejects_template_windows():
    from app.services.fields import _vin_shape
    assert _vin_shape("DEEPWAY5643822244") is False
    assert _vin_shape("2345AMT6789TCU10T") is False
    assert _vin_shape("HLC3J2VF9TA604594") is True


def test_vin_i_o_misread_is_normalised():
    # VINs cannot contain I/O/Q, so OCR reading them is a 1/0 mistake.
    fields = parse_fields("车辆VIN:LJIER4DU2T3305305", keep=("vin",),
                          require_vin_label=True)
    assert fields["vin"] == "LJ1ER4DU2T3305305"


def test_label_tail_stops_at_cjk():
    # "车辆VIN:TA002363车型：2524" must not become "TA0023632524".
    fields = parse_fields("车辆VIN:TA002363车型：2524", keep=("vin",),
                          require_vin_label=True)
    assert fields["vin"] is None
    assert fields["vin_partial"] == "TA002363"


def test_short_vin_tail_kept():
    fields = parse_fields("车辆VIN:TA60820)", keep=("vin",),
                          require_vin_label=True)
    assert fields["vin_partial"] == "TA60820"


def test_vin_checksum_flags_junk_but_keeps_value():
    """车架贴牌上的正文常被读成 17 位字符串；校验位能干净地把它们分开。

    实测样本（VLX 交叉验证）：6 个错读 checksum 全 False，5 个真 VIN 全 True。
    这里只要求「标记」，不要求「丢弃」——部分国产 VIN 不遵守校验位。
    """
    from app.services.fields import parse_fields

    junk = ["011EBSECASEPB1286", "5123456CKCKK712U3", "296DMEADDRHFC4259",
            "122EBSECASEPB1234", "5345612J3DCDC4515"]
    real = ["HLC3J2VF0TA605309", "LJ1ER4DU3T3304941", "HLC3J2VEXSA603850"]

    for text in junk:
        fields = parse_fields(text, keep=("vin",))
        # 值仍被保留（可能是真实 VIN），但必须标为不可信
        assert fields["vin"] is not None, text
        assert fields["vin_checksum_ok"] is False, text

    for text in real:
        fields = parse_fields(text, keep=("vin",))
        assert fields["vin_checksum_ok"] is True, text
