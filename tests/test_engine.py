import io, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import pandas as pd
import _engine as e

def test_clean_style():
    s = {"mode": "smart"}
    assert e.clean_style("VN000EE3BKA1", s) == "VN000EE3BKA"
    assert e.clean_style("  vn000ee3bka1\u200b ", s) == "vn000ee3bka"
    assert e.clean_style("VN000EE3BKA", s) == "VN000EE3BKA"          # no digit, untouched
    assert e.clean_style("VN000EE3BKA1", s, frozenset({"VN000EE3BKA1"})) == "VN000EE3BKA1"
    assert e.clean_style(123451.0, s) == "12345"
    assert e.clean_style("ABC12", {"mode": "legacy"}) == "ABC1"
    assert e.clean_style("ABC12", {"mode": "fixed", "count": 2}) == "ABC"
    assert e.clean_style("ABC12", {"mode": "regex", "pattern": r"\d+$"}) == "ABC"
    assert e.clean_style("ABC12", {"mode": "off"}) == "ABC12"

def test_handle():
    h = e.DEFAULT_CONFIG["handle"]
    row = {"Title": "Classic Slip-On Shoe", "Metafield: custom.parentsku": "VAN200023027100"}
    assert e.build_handle(row, "", {**h, "parent_sku_source": "Metafield: custom.parentsku"}) == "classic-slip-on-shoe-VAN200023027100"
    row = {"Title": "Old Skool", e.PARENT_STYLE: "VN000EE3BKA"}
    assert e.build_handle(row, "", h) == "old-skool-VAN000EE3BKA"

def test_pipeline():
    tpl = pd.DataFrame([["", "", "", "", ""], ["Handle", "Title", "Variant SKU", e.PARENT_STYLE, "Image Src"]])
    b = io.BytesIO(); tpl.to_excel(b, index=False, header=False)
    sap = pd.DataFrame({"Vendor": ["VN000EE3BKA1", "VN000EE3BKA1"], "Mat": ["S1", "S2"]})
    img = pd.DataFrame({"Style": ["VN000EE3BKA1"], "Url": ["http://x/1.jpg"]})
    cd = pd.DataFrame({"Style": ["VN000EE3BKA"], "Name": ["Old Skool"]}); cb = io.BytesIO(); cd.to_excel(cb, index=False)
    exp, aud, mp, cols = e.run_pipeline(
        b.getvalue(), [("deck.xlsx", cb.getvalue())], sap, {"Vendor": e.PARENT_STYLE, "Mat": "Variant SKU"},
        img, {"Style": e.PARENT_STYLE, "Url": "Image Src"}, {"deck.xlsx": {"Style": e.PARENT_STYLE, "Name": "Title"}}, {})
    assert list(mp[e.PARENT_STYLE]) == ["VN000EE3BKA"] * 2
    assert list(mp["Handle"]) == ["old-skool-VAN000EE3BKA"] * 2
    assert aud["Copy Deck Integrated"].all() and aud["Style Changed"].all()
    assert mp["Image Src"].iloc[0] == "http://x/1.jpg"
