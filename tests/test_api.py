import io, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
from fastapi.testclient import TestClient
from api.index import app
import api._engine as e

c = TestClient(app)
def xl(df, header=True):
    b = io.BytesIO(); df.to_excel(b, index=False, header=header); return b.getvalue()

def test_flow():
    tpl = xl(pd.DataFrame([["", "", "", ""], ["Handle", "Title", "Variant SKU", e.PARENT_STYLE]]), header=False)
    sap = xl(pd.DataFrame({"Vendor Article No.": ["VN000EE3BKA1"], "Material": ["S1"]}))
    r = c.post("/api/scan", data={"kind": "template", "header_row": 1}, files={"file": ("t.xlsx", tpl)})
    assert r.status_code == 200 and e.PARENT_STYLE in r.json()["targets"]
    r = c.post("/api/scan", data={"kind": "sap", "targets": json.dumps(r.json()["targets"])}, files={"file": ("s.xlsx", sap)})
    assert r.json()["columns"][0]["name"] == "Vendor Article No."
    maps = {"sap": {"Vendor Article No.": e.PARENT_STYLE, "Material": "Variant SKU"}, "copy": {}, "img": {}}
    r = c.post("/api/process", data={"config": "{}", "mappings": json.dumps(maps)}, files={"template": ("t.xlsx", tpl), "sap": ("s.xlsx", sap)})
    assert r.status_code == 200, r.text
    d = r.json(); assert d["rows"][0][0] == "VAN000EE3BKA" or "VAN000EE3BKA" in d["rows"][0][0]
    assert d["audit"][0]["Raw Style"] == "VN000EE3BKA1"
    t = c.post("/api/test-rules", json={"value": "VN000EE3BKA1", "title": "Old Skool"}).json()
    assert t == {"style": "VN000EE3BKA", "handle": "old-skool-VAN000EE3BKA"}
