import base64
import io
import json
import os
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

try:
    from . import _engine as eng
except ImportError:  # Vercel loads this file as a top-level module
    import _engine as eng

app = FastAPI(title="Matrixify Studio API")

MAX_PREVIEW_ROWS = 500
MAX_PRODUCTS = 300


def _json(df):
    return json.loads(df.to_json(orient="records"))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/scan")
async def scan(
    kind: str = Form(...),
    header_row: int = Form(1),
    targets: str = Form("[]"),
    file: Optional[UploadFile] = File(None),
):
    if file is None:
        raise HTTPException(400, "No file received.")
    data = await file.read()
    tg = json.loads(targets)
    try:
        if kind == "template":
            return eng.scan_template(data, header_row)
        if kind == "copy":
            return {"columns": eng.scan_copy_deck(data, file.filename, tg)}
        if kind in ("sap", "image"):
            df = eng.read_table(data, file.filename)
            return {"columns": eng.scan_columns(data, file.filename, tg), "rows": len(df), "cols": len(df.columns)}
    except Exception as e:  # surface readable errors in the UI
        raise HTTPException(422, f"Could not read {file.filename}: {e}")
    raise HTTPException(400, "Unknown scan kind.")


@app.post("/api/test-rules")
async def test_rules(payload: dict):
    cfg = eng.merge_config(payload.get("config"))
    style = eng.clean_style(payload.get("value", ""), cfg["style_cleanup"])
    row = {"Title": payload.get("title", ""), eng.PARENT_STYLE: style}
    src = cfg["handle"]["parent_sku_source"]
    if src != "auto" and payload.get("parent_sku"):
        row[src] = payload["parent_sku"]
    return {"style": style, "handle": eng.build_handle(row, style, cfg["handle"])}


@app.post("/api/process")
async def process(
    config: str = Form("{}"),
    mappings: str = Form("{}"),
    template: UploadFile = File(...),
    sap: Optional[UploadFile] = File(None),
    image: Optional[UploadFile] = File(None),
    copy_decks: List[UploadFile] = File(default=[]),
):
    cfg = json.loads(config)
    maps = json.loads(mappings)
    tpl = await template.read()
    copy_files = [(f.filename, await f.read()) for f in copy_decks]
    sap_df = eng.read_table(await sap.read(), sap.filename) if sap else pd.DataFrame()
    img_df = eng.read_table(await image.read(), image.filename) if image else pd.DataFrame()
    try:
        export, audit, mapped, columns = eng.run_pipeline(
            tpl, copy_files, sap_df, maps.get("sap", {}), img_df, maps.get("img", {}), maps.get("copy", {}), cfg
        )
    except Exception as e:
        raise HTTPException(422, f"Pipeline failed: {e}")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        export.to_excel(w, index=False, header=False)

    products = []
    if "Variant SKU" in mapped.columns and not mapped.empty:
        for sku, grp in list(mapped.groupby("Variant SKU", sort=False))[:MAX_PRODUCTS]:
            if not str(sku).strip():
                continue
            first = grp.iloc[0]
            images = []
            for _, r in grp.iterrows():
                for c in grp.columns:
                    if "image" in c.lower() or "src" in c.lower():
                        v = str(r[c]).strip()
                        if v and v != "nan" and v not in images:
                            images.append(v)
            products.append({
                "sku": str(sku), "title": str(first.get("Title", "")), "price": str(first.get("Variant Price", "")),
                "body": str(first.get("Body HTML", "")), "pdp": str(first.get(eng.PDP, "")), "images": images[:12],
            })

    return {
        "columns": columns,
        "rows": mapped.head(MAX_PREVIEW_ROWS).astype(str).values.tolist(),
        "total_rows": int(len(mapped)),
        "audit": _json(audit.head(5000)) if not audit.empty else [],
        "products": products,
        "xlsx": base64.b64encode(buf.getvalue()).decode(),
    }


if os.path.isdir("public"):  # local development only; Vercel serves /public from its CDN
    app.mount("/", StaticFiles(directory="public", html=True), name="static")
