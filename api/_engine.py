"""Matrixify Studio engine.

Pure-Python port of the original Streamlit pipeline (SAP-driven iteration and
enrichment). All original rules are preserved; two rules were rebuilt:

  * clean_style()  - trailing-character removal for SAP master style codes
  * build_handle() - Handle = Title slug + Parent SKU (configurable pattern)
"""
import io
import re
import difflib
import pandas as pd

NONE = "Unmapped / Ignore"
PARENT_STYLE = "Metafield: custom.masterstyle [single_line_text_field]"
PDP = "Metafield: custom.pdp_details [rich_text_field]"

DEFAULT_CONFIG = {
    "header_row": 1,
    "repair_html": True,
    "strip_rs_barcode": True,
    "normalize_sizing": True,
    "pdp_to_list": True,
    # mode: smart | legacy | fixed | regex | off
    "style_cleanup": {"mode": "smart", "count": 1, "pattern": r"\d$"},
    "handle": {
        "enabled": True,
        "pattern": "{title}-{parent_sku}",
        "parent_sku_source": "auto",
        "parent_sku_strip_regex": "",
        "sku_case": "keep",       # keep | upper | lower
        "vn_to_van": True,
        "overwrite_existing": False,
    },
}


def merge_config(user):
    cfg = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    for k, v in (user or {}).items():
        if isinstance(cfg.get(k), dict) and isinstance(v, dict):
            cfg[k].update(v)
        elif k in cfg:
            cfg[k] = v
    return cfg


# ------------------------------------------------------------------
# File readers
# ------------------------------------------------------------------
def read_table(data, filename, nrows=None):
    if not data:
        return pd.DataFrame()
    try:
        if filename.lower().endswith(".csv"):
            return pd.read_csv(io.BytesIO(data), nrows=nrows)
        return pd.read_excel(io.BytesIO(data), nrows=nrows)
    except Exception:
        return pd.DataFrame()


def read_copy_sheet(xls, sheet_name):
    df = pd.read_excel(xls, sheet_name=sheet_name)
    if "Approved" in sheet_name:
        df = df.dropna(how="all")
        if len(df) > 1 and df.iloc[0].astype(str).str.contains("Material|SKU|Number", case=False).any():
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)
    return df


def template_rows(data, header_row):
    df = pd.read_excel(io.BytesIO(data), nrows=max(header_row + 1, 5), header=None)
    return df


def scan_template(data, header_row):
    df = template_rows(data, header_row)
    header_row = min(header_row, len(df) - 1)
    targets, seen = [], set()
    for c in df.iloc[header_row].tolist():
        s = str(c).strip()
        if s and s != "nan" and s not in seen:
            seen.add(s)
            targets.append(s)
    preview = df.head(6).fillna("").astype(str).values.tolist()
    return {"targets": targets, "preview": preview, "rows_available": len(df)}


def _suggest(col, targets):
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", str(s).lower())
    n = norm(col)
    if not n:
        return None
    best, score = None, 0.0
    for t in targets:
        r = difflib.SequenceMatcher(None, n, norm(t)).ratio()
        if r > score:
            best, score = t, r
    return best if score >= 0.86 else None


def scan_columns(data, filename, targets):
    """Columns + sample rows for the SAP / image tables."""
    df = read_table(data, filename, nrows=5)
    cols = [str(c).strip() for c in df.columns if str(c).strip() and not str(c).startswith("Unnamed")]
    sample = {c: next((str(v) for v in df[c].tolist() if pd.notnull(v)), "") for c in cols if c in df.columns}
    return [{"name": c, "sample": sample.get(c, ""), "sheet": "", "suggest": _suggest(c, targets)} for c in cols]


def scan_copy_deck(data, filename, targets):
    out, seen = [], set()
    xls = pd.ExcelFile(io.BytesIO(data))
    for sheet in xls.sheet_names:
        try:
            df = read_copy_sheet(xls, sheet).head(3)
        except Exception:
            continue
        for col in df.columns:
            c = str(col).strip()
            if c and c not in seen and not c.startswith("Unnamed"):
                seen.add(c)
                sample = next((str(v) for v in df[col].tolist() if pd.notnull(v)), "")
                out.append({"name": c, "sheet": sheet, "sample": sample, "suggest": _suggest(c, targets)})
    return out


# ------------------------------------------------------------------
# Original helpers (unchanged behaviour)
# ------------------------------------------------------------------
def slugify(text):
    if pd.isna(text):
        return ""
    text = re.sub(r"[^a-z0-9]+", "-", str(text).lower())
    return text.strip("-")


def clean_html_body(desc):
    if pd.isna(desc) or not str(desc).strip():
        return "", False
    s, fixed = str(desc).strip(), False
    if not s.startswith("<p>"):
        s, fixed = f"<p>{s}</p>", True
    if not s.endswith("</p>") and s.startswith("<p>"):
        s, fixed = s + "</p>", True
    return s, fixed


def extract_price_val(val):
    if pd.isna(val):
        return None
    s = re.sub(r"[^\d\.]", "", str(val))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def format_option_value(val, convert_h=True):
    if pd.isna(val):
        return val
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return val
    s = s.strip("\"'")
    if convert_h:
        m = re.match(r"^[Aa](\d+)$", s)
        if m:
            return m.group(1)
        up = s.upper()
        alpha = ["XS", "S", "M", "L", "XL", "XXL", "XXXL", "OS", "SM", "SL", "MED", "LRG"]
        if any(sz in up for sz in alpha) and s[0].isdigit():
            cleaned = re.sub(r"^[0-9]+", "", s)
            if cleaned:
                return cleaned
    if convert_h and ("H" in s or "h" in s):
        num_part = re.sub(r"[Hh]", "", s).strip()
        try:
            adj = float(num_part) + 0.5
            return str(int(adj)) if adj.is_integer() else str(adj)
        except ValueError:
            return re.sub(r"[Hh]", ".5", s)
    try:
        num = float(s)
        if num >= 10 and num.is_integer():
            i = int(num)
            if i % 10 == 0:
                return str(i // 10)
            if i % 10 == 5:
                return str(i / 10.0)
    except ValueError:
        pass
    if s.startswith("0") and len(s) > 1 and s[1].isdigit():
        try:
            s = str(float(s))
        except ValueError:
            pass
    return s


def convert_to_bullet_list(text):
    if pd.isna(text) or not str(text).strip():
        return text
    raw = str(text).strip()
    if "|" in raw:
        items = [x.strip() for x in raw.split("|") if x.strip()]
    elif "\n" in raw:
        items = [x.strip() for x in raw.split("\n") if x.strip()]
    elif "," in raw:
        items = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        items = [raw]
    cleaned = []
    for item in items:
        c = re.sub(r"^[\s•\-\|\*]*\s*", "", item)
        c = re.sub(r"^\d+[\.\)]\s*", "", c).strip()
        if c:
            cleaned.append(c)
    if not cleaned:
        return text
    return "<ul>" + "".join(f"<li>{i}</li>" for i in cleaned) + "</ul>"


# ------------------------------------------------------------------
# Rebuilt rule 1: master style cleanup
# ------------------------------------------------------------------
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)


def normalize_code(v):
    """Trim, drop invisible characters, undo Excel float artefacts (123.0)."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    s = str(v).translate(_INVISIBLE).replace("\u00a0", " ").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return "" if s.lower() == "nan" else s


def clean_style(raw, scfg, reference=frozenset()):
    """Remove the trailing suffix from a SAP master style code.

    smart  : strips one trailing digit only when the code ends in a digit, and
             leaves the code alone if it already matches a copy-deck style.
    legacy : always drops the last character (original behaviour).
    fixed  : drops N characters.   regex : removes the first match of a pattern.
    """
    s = normalize_code(raw)
    mode = (scfg or {}).get("mode", "smart")
    if not s or mode == "off":
        return s
    if mode == "legacy":
        return s[:-1] if len(s) > 1 else s
    if mode == "fixed":
        n = max(0, int(scfg.get("count", 1) or 0))
        return s[:-n] if 0 < n < len(s) else s
    if mode == "regex":
        try:
            out = re.sub(scfg.get("pattern", ""), "", s, count=1).strip()
        except re.error:
            return s
        return out or s
    # smart
    if len(s) < 2 or not s[-1].isdigit():
        return s
    if reference and s.upper() in reference:
        return s
    return s[:-1]


# ------------------------------------------------------------------
# Rebuilt rule 2: handle = title slug + parent SKU
# ------------------------------------------------------------------
def _vn_to_van(s):
    if s.lower().startswith("vn") and not s.lower().startswith("van"):
        return ("VAN" if s[:2].isupper() else "van") + s[2:]
    return re.sub(r"\bvn", "van", s, flags=re.IGNORECASE)


def _slug_case(text, case):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")
    return s.upper() if case == "upper" else s.lower() if case == "lower" else s


def resolve_parent_sku(row_data, style_code, hcfg):
    src = hcfg.get("parent_sku_source", "auto")
    if src and src != "auto":
        v = row_data.get(src, "")
    else:
        v = ""
        for k, val in row_data.items():
            if "parent" in k.lower() and "sku" in k.lower() and val:
                v = val
                break
        if not v:
            v = row_data.get(PARENT_STYLE) or ""
        if not v:
            for k, val in row_data.items():
                if ("masterstyle" in k.lower() or "parent" in k.lower()) and val:
                    v = val
                    break
        if not v:
            v = style_code or row_data.get("Variant SKU", "")
    v = normalize_code(v)
    rx = hcfg.get("parent_sku_strip_regex")
    if rx and v:
        try:
            v = re.sub(rx, "", v)
        except re.error:
            pass
    if v and hcfg.get("vn_to_van", True):
        v = _vn_to_van(v)
    return v


def build_handle(row_data, style_code, hcfg):
    psku = resolve_parent_sku(row_data, style_code, hcfg)
    values = {
        "title": slugify(row_data.get("Title", "")),
        "parent_sku": _slug_case(psku, hcfg.get("sku_case", "keep")),
        "masterstyle": _slug_case(row_data.get(PARENT_STYLE, ""), hcfg.get("sku_case", "keep")),
        "variant_sku": _slug_case(row_data.get("Variant SKU", ""), hcfg.get("sku_case", "keep")),
    }
    out = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), hcfg.get("pattern") or "{title}-{parent_sku}")
    return re.sub(r"-{2,}", "-", out).strip("-")


# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------
def run_pipeline(template_bytes, copy_files, sap_df, sap_map, img_df, img_map, copy_map, cfg):
    cfg = merge_config(cfg)
    hcfg, scfg = cfg["handle"], cfg["style_cleanup"]
    convert_h = cfg["normalize_sizing"]

    dfh = template_rows(template_bytes, cfg["header_row"])
    r_0 = dfh.iloc[0].tolist()
    r_2, seen = [], {}
    for c in dfh.iloc[min(cfg["header_row"], len(dfh) - 1)].tolist():
        s = str(c).strip() if pd.notnull(c) else ""
        if not s or s == "nan":
            s = "Unnamed_Col"
        if s in seen:
            seen[s] += 1
            r_2.append(f"{s}_{seen[s]}")
        else:
            seen[s] = 0
            r_2.append(s)
    r_0 = r_0 + [""] * (len(r_2) - len(r_0))
    lower_map = {c.strip().lower(): c for c in r_2}

    def valid(target):
        if not target or target == NONE:
            return None
        t = target.strip()
        if t in r_2:
            return t
        if t.lower() in lower_map:
            return lower_map[t.lower()]
        nt = re.sub(r"\s+", " ", t.lower())
        for c in r_2:
            if re.sub(r"\s+", " ", str(c).strip().lower()) == nt:
                return c
        return t

    flat_copy = {}
    for f_map in (copy_map or {}).values():
        flat_copy.update(f_map)

    def copy_target(fname, col):
        return (copy_map or {}).get(fname, {}).get(col) or flat_copy.get(col) or NONE

    def style_col(mp):
        for col, tgt in mp.items():
            if tgt == PARENT_STYLE or valid(tgt) == PARENT_STYLE:
                return col
        return None

    sap_style_col = style_col(sap_map)
    img_style_col = style_col(img_map)
    sap_sku_col = next((c for c, t in sap_map.items() if t == "Variant SKU" or valid(t) == "Variant SKU"), None)

    # 1. copy deck lookup (keyed by style code, upper case)
    lookup, copy_rows = {}, []
    for fname, data in copy_files:
        xls = pd.ExcelFile(io.BytesIO(data))
        for sheet in xls.sheet_names:
            df = read_copy_sheet(xls, sheet)
            for _, r in df.iterrows():
                base = {}
                for col in df.columns:
                    vt = valid(copy_target(fname, str(col).strip()))
                    val = r[col]
                    if vt and pd.notnull(val) and str(val).strip() not in ("", "nan"):
                        cv = str(val).strip()
                        if convert_h and ("option" in vt.lower() or "size" in vt.lower()):
                            cv = format_option_value(cv, True)
                        base[vt] = cv
                code = ""
                for t_col, val in base.items():
                    if "id" in t_col.lower() or "sku" in t_col.lower() or "masterstyle" in t_col.lower():
                        code = val
                        break
                if not code and base:
                    code = list(base.values())[0]
                if code:
                    lookup[str(code).strip().upper()] = base
                copy_rows.append(base)
    reference = frozenset(lookup.keys())

    # 2. clean style codes on SAP + image tables
    def prep(df, col):
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        if col and col in df.columns:
            df["__raw_style"] = df[col]
            df[col] = df[col].map(lambda v: clean_style(v, scfg, reference))
        return df

    sap = prep(sap_df, sap_style_col)
    img = prep(img_df, img_style_col)

    groups = {}
    if not img.empty and img_style_col and img_style_col in img.columns:
        for _, r in img.iterrows():
            k = str(r.get(img_style_col, "")).strip()
            if k and k != "nan":
                groups.setdefault(k.upper(), []).append(r)

    records, audit, tracker = [], [], {}

    def finish(row_data):
        if cfg["strip_rs_barcode"]:
            for k in list(row_data):
                if "barcode" in k.lower() and row_data[k]:
                    row_data[k] = re.sub(r"^[Rr][Ss]", "", str(row_data[k])).strip()
        for k in list(row_data):
            if "price" in k.lower() and row_data[k]:
                p = extract_price_val(row_data[k])
                if p is not None:
                    row_data[k] = f"{p:.2f}"
        if cfg["pdp_to_list"] and PDP in row_data:
            row_data[PDP] = convert_to_bullet_list(row_data[PDP])

    if not sap.empty:
        for idx, sap_row in sap.iterrows():
            style_code = ""
            if sap_style_col and sap_style_col in sap.columns:
                style_code = str(sap_row.get(sap_style_col, "")).strip()
            elif sap_sku_col and sap_sku_col in sap.columns:
                style_code = str(sap_row.get(sap_sku_col, "")).strip()
            key = style_code.upper() if style_code and style_code != "nan" else ""
            row_data = lookup.get(key, {}).copy()

            for col, tgt in sap_map.items():
                vt = valid(tgt)
                if not vt:
                    continue
                val = sap_row.get(col) if col in sap_row else next(
                    (sap_row.get(k) for k in sap_row.index if str(k).strip().lower() == col.strip().lower()), None)
                if pd.notnull(val) and str(val).strip() not in ("", "nan"):
                    cv = str(val).strip()
                    if convert_h and ("option" in vt.lower() or "size" in vt.lower()):
                        cv = format_option_value(cv, True)
                    row_data[vt] = cv
            if sap_style_col and sap_style_col in sap_row:
                ps = str(sap_row.get(sap_style_col, "")).strip()
                if ps and ps != "nan":
                    row_data[PARENT_STYLE] = ps
            finish(row_data)

            title = row_data.get("Title", "")
            handle = ""
            if hcfg["enabled"] and "Handle" in r_2 and (hcfg["overwrite_existing"] or not row_data.get("Handle")):
                handle = build_handle(row_data, style_code, hcfg)
                row_data["Handle"] = handle
            handle = row_data.get("Handle", handle)

            body = row_data.get("Body HTML", "")
            if cfg["repair_html"] and body:
                cb, _ = clean_html_body(body)
                if cb:
                    row_data["Body HTML"] = cb

            img_rows = groups.get(key, [])
            if img_rows:
                for ir in img_rows:
                    rd = row_data.copy()
                    for col, tgt in img_map.items():
                        vt = valid(tgt)
                        val = ir.get(col)
                        if vt and pd.notnull(val) and str(val).strip() not in ("", "nan"):
                            cv = str(val).strip()
                            if convert_h and ("option" in vt.lower() or "size" in vt.lower()):
                                cv = format_option_value(cv, True)
                            rd[vt] = cv
                    if cfg["pdp_to_list"] and PDP in rd:
                        rd[PDP] = convert_to_bullet_list(rd[PDP])
                    rec = {c: "" for c in r_2}
                    rec.update(rd)
                    records.append(rec)
            else:
                rec = {c: "" for c in r_2}
                rec.update(row_data)
                records.append(rec)

            raw = normalize_code(sap_row.get("__raw_style", ""))
            audit.append({
                "Style Code": style_code or "UNKNOWN", "Title": title, "File Source": "SAP-Driven",
                "SAP Integrated": True, "Copy Deck Integrated": key in lookup,
                "Variants Generated": 1, "Is Duplicate Style": style_code in tracker,
                "Missing Title": not title,
                "Raw Style": raw, "Style Changed": bool(raw) and raw != style_code,
                "Handle": handle, "Images Matched": len(img_rows),
            })
            if style_code:
                tracker[style_code] = True
    else:
        for base in copy_rows:
            if cfg["pdp_to_list"] and PDP in base:
                base[PDP] = convert_to_bullet_list(base[PDP])
            rec = {c: "" for c in r_2}
            rec.update(base)
            records.append(rec)

    if records:
        df_map = pd.DataFrame(records).reindex(columns=r_2).fillna("")
        export = pd.concat([pd.DataFrame([r_0], columns=r_2), pd.DataFrame([r_2], columns=r_2), df_map], ignore_index=True)
    else:
        df_map = pd.DataFrame(columns=r_2)
        export = pd.DataFrame([r_0, r_2], columns=r_2)
    return export, pd.DataFrame(audit), df_map, r_2
