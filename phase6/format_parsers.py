"""
Phase 6 - Format-specific parsers

Each parser takes a file path and returns:
  (raw_df, header_text, source_format, warnings)

- raw_df      : DataFrame with raw content (pre-normalization)
- header_text : free text from the file header (bank name, account info)
- source_format: "csv" | "xlsx" | "pdf" | "image"
- warnings    : list of issues encountered during parsing
"""

import io
import os
import re
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# CSV Parser
# ---------------------------------------------------------------------------
def parse_csv(file_path: str) -> tuple:
    warnings = []
    header_text = ""

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = [f.readline() for _ in range(8)]
        header_text = " ".join(raw_lines)
    except Exception as e:
        warnings.append(f"Could not read header lines: {e}")

    from ingestion_config import COLUMN_ROLE_KEYWORDS
    all_col_keywords = set()
    for keywords in COLUMN_ROLE_KEYWORDS.values():
        all_col_keywords.update(keywords)

    skip = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                hits = sum(1 for kw in all_col_keywords if kw.lower() in line.lower())
                if hits >= 3:
                    skip = i
                    break
    except Exception:
        pass

    df = None
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(
                file_path,
                encoding=encoding,
                skiprows=skip,
                skip_blank_lines=True,
                dtype=str,
                on_bad_lines="skip",
            )
            if len(df.columns) >= 4:
                break
        except Exception as e:
            warnings.append(f"CSV read error (enc={encoding}): {e}")
            continue

    if df is None or df.empty:
        warnings.append("CSV parsing failed entirely")
        return pd.DataFrame(), header_text, "csv", warnings

    return df, header_text, "csv", warnings


# ---------------------------------------------------------------------------
# Excel Parser
# ---------------------------------------------------------------------------
def parse_xlsx(file_path: str) -> tuple:
    warnings = []
    header_text = ""

    try:
        raw = pd.read_excel(file_path, header=None, dtype=str, nrows=6)
        header_text = " ".join(
            str(v) for row in raw.values for v in row if pd.notna(v)
        )
    except Exception as e:
        warnings.append(f"Could not read Excel header block: {e}")

    df = None
    for header_row in (0, 1, 2, 3, 4):
        try:
            df = pd.read_excel(
                file_path,
                header=header_row,
                dtype=str,
                engine="openpyxl",
            )
            if len(df.columns) >= 4 and not all(str(c).startswith("Unnamed") for c in df.columns):
                break
        except Exception as e:
            warnings.append(f"Excel read failed at header_row={header_row}: {e}")
            continue

    if df is None:
        warnings.append("Excel parsing failed entirely")
        return pd.DataFrame(), header_text, "xlsx", warnings

    return df, header_text, "xlsx", warnings


# ---------------------------------------------------------------------------
# PDF Parser
# ---------------------------------------------------------------------------
def parse_pdf(file_path: str, password: str = None, password_candidates: list = None) -> tuple:
    try:
        import pdfplumber
        from pdfplumber.utils.exceptions import PdfminerException
    except ImportError:
        return pd.DataFrame(), "", "pdf", ["pdfplumber not installed"]

    warnings = []
    header_text = ""
    all_rows = []
    headers = None
    n_cols = 0

    candidates = ([password] if password else []) + list(password_candidates or [])

    def _open_pdf():
        try:
            return pdfplumber.open(file_path)
        except PdfminerException as e:
            if not candidates:
                raise PdfminerException(
                    "PDF appears to be password-protected. Re-run with "
                    "--pdf-password (or --pdf-passwords for multiple candidates) to supply one."
                ) from e
            last_err = e
            for pw in candidates:
                try:
                    return pdfplumber.open(file_path, password=pw)
                except PdfminerException as e2:
                    last_err = e2
                    continue
            raise PdfminerException(
                f"PDF is password-protected and none of the "
                f"{len(candidates)} supplied password(s) worked."
            ) from last_err

    try:
        with _open_pdf() as pdf:
            # ── Extract full text for bank/account detection ──────────────
            full_text_chunks = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    full_text_chunks.append(page_text)
            header_text = "\n".join(full_text_chunks)

            # ── Table extraction with strategy cascade ─────────────────────
            # Strategy order matters:
            # 1. "lines"        — native bank PDFs with real line objects
            # 2. "lines_strict" — stricter variant of above
            # 3. "text"         — ReportLab PDFs (Phase 5 output): borders
            #                     are drawn rectangles, not line primitives;
            #                     "text" infers columns from word positions
            # 4. {}             — pdfplumber default auto-detect
            extraction_strategies = [
                {"vertical_strategy": "lines",        "horizontal_strategy": "lines",
                 "snap_tolerance": 3, "join_tolerance": 3},
                {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict",
                 "snap_tolerance": 3, "join_tolerance": 3},
                {"vertical_strategy": "text",         "horizontal_strategy": "text",
                 "snap_tolerance": 3, "join_tolerance": 3},
                {},
            ]

            for page in pdf.pages:
                tables = []
                for strategy in extraction_strategies:
                    try:
                        tables = page.extract_tables(strategy) if strategy else page.extract_tables()
                        if tables and any(
                            t and len(t) >= 1 and len(t[0]) >= 5
                            for t in tables
                        ):
                            break
                        tables = []
                    except Exception:
                        tables = []
                        continue

                for table in tables:
                    if not table:
                        continue
                    if headers is None and len(table) < 2:
                        continue
                    if headers is not None:
                        if len(table[0]) != n_cols:
                            continue
                    elif not _is_transaction_table(table):
                        continue

                    cleaned = []
                    for row in table:
                        clean_row = [
                            str(cell).strip() if cell is not None else ""
                            for cell in row
                        ]
                        if any(c for c in clean_row):
                            cleaned.append(clean_row)

                    if not cleaned:
                        continue

                    if headers is None:
                        header_row_idx = _find_pdf_header_row(cleaned)
                        raw_headers = cleaned[header_row_idx]
                        headers = _merge_split_headers(raw_headers)
                        n_cols = len(headers)
                        data_rows = cleaned[header_row_idx + 1:]
                    else:
                        data_rows = cleaned
                        if cleaned[0] == headers or _merge_split_headers(cleaned[0]) == headers:
                            data_rows = cleaned[1:]

                    for row in data_rows:
                        if len(row) < n_cols:
                            row += [""] * (n_cols - len(row))
                        all_rows.append(row[:n_cols])

    except PdfminerException as e:
        return pd.DataFrame(), header_text, "pdf", [f"PASSWORD_PROTECTED: {e}"]
    except Exception as e:
        return pd.DataFrame(), header_text, "pdf", [f"PDF parsing error: {e}"]

    if not all_rows or headers is None:
        warnings.append("No tables found in PDF — trying text fallback")
        return _pdf_text_fallback(file_path, header_text, warnings)

    df = pd.DataFrame(all_rows, columns=headers)
    df = df.replace("", pd.NA)
    warnings.append(f"PDF: extracted {len(df)} rows, {len(headers)} columns")
    return df, header_text, "pdf", warnings


def _merge_split_headers(headers: list) -> list:
    merged = []
    skip_next = False
    for i, cell in enumerate(headers):
        if skip_next:
            skip_next = False
            continue
        cell = str(cell).strip()
        if i + 1 < len(headers):
            next_cell = str(headers[i + 1]).strip()
            if next_cell and next_cell[0] in (".", ",", "/", " ") and len(next_cell) < 10:
                merged.append((cell + next_cell).strip())
                skip_next = True
                continue
        merged.append(cell)
    return merged


def _find_pdf_header_row(rows: list) -> int:
    from ingestion_config import COLUMN_ROLE_KEYWORDS
    all_col_keywords = set()
    for kws in COLUMN_ROLE_KEYWORDS.values():
        all_col_keywords.update(kw.lower() for kw in kws)

    best_idx = 0
    best_score = 0
    for i, row in enumerate(rows[:6]):
        score = sum(
            1 for cell in row
            if any(kw in str(cell).lower() for kw in all_col_keywords)
        )
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _is_transaction_table(table: list) -> bool:
    if not table or len(table) < 2:
        return False
    if len(table[0]) < 5:
        return False
    header_text = " ".join(str(c).lower() for c in table[0])
    date_keywords    = {"date", "txn", "tran", "post"}
    amount_keywords  = {"debit", "credit", "withdrawal", "deposit", "balance", "amount"}
    return (
        any(kw in header_text for kw in date_keywords) and
        any(kw in header_text for kw in amount_keywords)
    )


# ---------------------------------------------------------------------------
# PDF text fallback — 3-layer cascade for PDFs where table extraction fails
# ---------------------------------------------------------------------------

def _pdf_text_fallback(file_path: str, header_text: str, warnings: list) -> tuple:
    """
    3-layer fallback for PDFs where pdfplumber table extraction finds nothing:

    Layer 1: _pdf_word_position_parser — uses native PDF word x/y positions
             to reconstruct columns. Best for ReportLab PDFs (Phase 5 output).

    Layer 2: _text_line_to_dataframe — extracts raw text lines and splits on
             double-space gaps. Works for simple text-only PDFs.

    Layer 3: _text_linefallback_to_dataframe — last resort single-space split.
    """
    try:
        import pdfplumber

        # Layer 1: word-position parser (best for ReportLab)
        with pdfplumber.open(file_path) as pdf:
            df = _pdf_word_position_parser(pdf, warnings)
        if not df.empty:
            warnings.append("PDF: parsed via word-position method")
            return df, header_text, "pdf", warnings

        # Layer 2 + 3: text-line parsers
        all_lines = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                all_lines.extend(text.splitlines())

        df = _text_line_to_dataframe(all_lines, warnings)
        if not df.empty:
            warnings.append("PDF: parsed via text-line double-space method")
            return df, header_text, "pdf", warnings

        df = _text_linefallback_to_dataframe(all_lines, warnings)
        if not df.empty:
            warnings.append("PDF: parsed via text-line single-space fallback")
            return df, header_text, "pdf", warnings

        warnings.append("PDF: all fallback methods failed — no transaction rows found")
        return pd.DataFrame(), header_text, "pdf", warnings

    except Exception as e:
        return pd.DataFrame(), header_text, "pdf", warnings + [f"PDF text fallback error: {e}"]


def _pdf_word_position_parser(pdf, warnings: list) -> pd.DataFrame:
    """
    Reconstruct a transaction table from native PDF word x/y positions.
    Works for ReportLab-generated PDFs where table borders are drawn
    rectangles (not PDF line primitives), so strategy='lines' finds nothing.
    Uses the same column-band clustering as the OCR bbox parser.
    """
    from ingestion_config import COLUMN_ROLE_KEYWORDS

    all_col_keywords = set()
    for kws in COLUMN_ROLE_KEYWORDS.values():
        all_col_keywords.update(kw.lower() for kw in kws)

    # Group words by y-position (same row = within Y_TOLERANCE pixels)
    all_rows_by_y = {}
    Y_TOLERANCE = 4

    for page in pdf.pages:
        words = page.extract_words(x_tolerance=2, y_tolerance=2)
        for w in words:
            text = str(w.get("text", "")).strip()
            if not text:
                continue
            y = round(float(w.get("top", 0)) / Y_TOLERANCE) * Y_TOLERANCE
            x = float(w.get("x0", 0))
            all_rows_by_y.setdefault(y, []).append((x, text))

    if not all_rows_by_y:
        return pd.DataFrame()

    # Sort rows top-to-bottom
    text_rows = [
        sorted(all_rows_by_y[y], key=lambda t: t[0])
        for y in sorted(all_rows_by_y.keys())
    ]

    # Find header row — most column keyword hits
    header_idx = 0
    best_score = 0
    for i, row in enumerate(text_rows[:25]):
        row_text = " ".join(w for _, w in row).lower()
        score = sum(1 for kw in all_col_keywords if kw in row_text)
        if score > best_score:
            best_score = score
            header_idx = i

    if best_score < 2:
        warnings.append("Word-position parser: could not find header row")
        return pd.DataFrame()

    # Build column bands from header word x-positions
    header_words = text_rows[header_idx]
    raw_x = [x for x, _ in header_words]
    # Cluster x positions into bands with 15px snapping
    col_x_positions = []
    for x in sorted(raw_x):
        snapped = round(x / 15) * 15
        if not col_x_positions or snapped - col_x_positions[-1] > 20:
            col_x_positions.append(snapped)

    if len(col_x_positions) < 4:
        warnings.append("Word-position parser: fewer than 4 column bands found")
        return pd.DataFrame()

    def assign_col(x):
        return min(range(len(col_x_positions)),
                   key=lambda i: abs(x - col_x_positions[i]))

    # Build column names
    n_cols = len(col_x_positions)
    header_cells = [""] * n_cols
    for x, word in header_words:
        ci = assign_col(x)
        header_cells[ci] = (header_cells[ci] + " " + word).strip()
    header_cells = [c or f"col_{i}" for i, c in enumerate(header_cells)]

    # Deduplicate column names
    seen = {}
    deduped = []
    for name in header_cells:
        if name in seen:
            seen[name] += 1
            deduped.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            deduped.append(name)
    header_cells = deduped

    # Parse data rows
    SKIP_PHRASES = ["account holder", "ifsc", "statement period",
                    "page ", "generated", "computer-generated", "branch"]
    rows = []
    for row_words in text_rows[header_idx + 1:]:
        if not row_words:
            continue
        row_text = " ".join(w for _, w in row_words).lower()
        if any(phrase in row_text for phrase in SKIP_PHRASES):
            continue
        cells = [""] * n_cols
        for x, word in row_words:
            ci = assign_col(x)
            cells[ci] = (cells[ci] + " " + word).strip()
        if sum(1 for c in cells if c.strip()) >= 3:
            rows.append(cells)

    if not rows:
        warnings.append("Word-position parser: header found but no data rows")
        return pd.DataFrame()

    warnings.append(f"Word-position parser: {len(rows)} rows from {n_cols} columns")
    return pd.DataFrame(rows, columns=header_cells)


def _text_line_to_dataframe(lines: list, warnings: list) -> pd.DataFrame:
    """
    Layer 2: Split text lines on 2+ consecutive spaces to reconstruct columns.
    Works when PDF text extraction preserves column spacing.
    """
    header_keywords = [
        "date", "debit", "credit", "balance", "narration",
        "description", "withdrawal", "deposit", "particulars",
        "remarks", "amount", "ref", "cheque", "txn", "post",
    ]
    header_idx = None
    for i, line in enumerate(lines):
        hits = sum(1 for kw in header_keywords if kw in line.lower())
        if hits >= 3:
            header_idx = i
            break

    if header_idx is None:
        return pd.DataFrame()

    header_line = lines[header_idx]
    col_names = re.split(r"\s{2,}", header_line.strip())
    col_names = [c.strip() for c in col_names if c.strip()]
    if len(col_names) < 4:
        return pd.DataFrame()

    n_cols = len(col_names)
    rows = []
    SKIP = ["account holder", "ifsc", "page", "statement period", "generated"]
    for line in lines[header_idx + 1:]:
        if not line or len(line) < 8:
            continue
        if any(kw in line.lower() for kw in SKIP):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        parts = [p.strip() for p in parts if p.strip()]
        if abs(len(parts) - n_cols) > 2:
            continue
        while len(parts) < n_cols:
            parts.append("")
        rows.append(parts[:n_cols])

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=col_names)


def _text_linefallback_to_dataframe(lines: list, warnings: list) -> pd.DataFrame:
    """
    Layer 3 (last resort): single-space split on detected header line.
    Less accurate but handles minimal text-layer PDFs.
    """
    header_keywords = [
        "date", "debit", "credit", "balance", "narration",
        "description", "withdrawal", "deposit", "particulars",
        "remarks", "amount", "ref", "cheque", "txn",
    ]
    header_idx = None
    for i, line in enumerate(lines):
        hits = sum(1 for kw in header_keywords if kw in line.lower())
        if hits >= 3:
            header_idx = i
            break

    if header_idx is None:
        return pd.DataFrame()

    header_line = lines[header_idx]
    col_names = re.split(r"\s+", header_line.strip())
    col_names = [c.strip() for c in col_names if c.strip()]
    if len(col_names) < 4:
        return pd.DataFrame()

    n_cols = len(col_names)
    rows = []
    for line in lines[header_idx + 1:]:
        if not line or len(line) < 8:
            continue
        if any(kw in line.lower() for kw in ["account holder", "ifsc", "page"]):
            continue
        parts = re.split(r"\s+", line.strip())
        parts = [p.strip() for p in parts if p.strip()]
        if abs(len(parts) - n_cols) > 3:
            continue
        while len(parts) < n_cols:
            parts.append("")
        rows.append(parts[:n_cols])

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=col_names)


# ---------------------------------------------------------------------------
# Image / Scanned Statement Parser (Tesseract OCR)
# ---------------------------------------------------------------------------
def parse_image(file_path: str) -> tuple:
    try:
        import pytesseract
        from PIL import Image
        import PIL.ImageEnhance as IE
    except ImportError:
        return pd.DataFrame(), "", "image", ["pytesseract or pillow not installed"]

    warnings = []
    header_text = ""

    try:
        img = Image.open(file_path).convert("L")
        w, h = img.size
        scale = max(1, 2400 // max(w, 1))
        if scale > 1:
            img = img.resize((w * scale, h * scale), Image.LANCZOS)
        img = IE.Contrast(img).enhance(1.5)
        img = IE.Sharpness(img).enhance(2.0)

        tsv_data = pytesseract.image_to_data(
            img, config="--psm 6 --oem 3",
            output_type=pytesseract.Output.DATAFRAME
        )
        raw_text = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        header_text = " ".join(lines[:5])

    except Exception as e:
        return pd.DataFrame(), "", "image", [f"Image load/OCR error: {e}"]

    df = _ocr_bbox_to_dataframe(tsv_data, warnings)

    if df.empty and lines:
        warnings.append("OCR bbox reconstruction failed, trying line-split fallback")
        df = _text_line_to_dataframe(lines, warnings)

    if df.empty and lines:
        df = _text_linefallback_to_dataframe(lines, warnings)

    if df.empty:
        warnings.append("OCR produced no parseable table rows")

    return df, header_text, "image", warnings


def _ocr_bbox_to_dataframe(tsv: "pd.DataFrame", warnings: list) -> "pd.DataFrame":
    import numpy as np

    tsv = tsv[tsv["conf"] > 30].copy()

    tsv = tsv[
    tsv["text"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()
    if tsv.empty:
        warnings.append("OCR: no confident words detected")
        return pd.DataFrame()

    line_groups = tsv.groupby(["block_num", "line_num"])
    logical_lines = []
    for (blk, ln), grp in line_groups:
        grp = grp.sort_values("left")
        text = " ".join(grp["text"].tolist())
        left_positions = grp["left"].tolist()
        words = grp["text"].tolist()
        logical_lines.append({
            "block": blk, "line": ln,
            "text": text,
            "words": words,
            "lefts": left_positions,
            "top": grp["top"].min(),
        })
    logical_lines.sort(key=lambda x: (x["block"], x["top"]))

    header_keywords = [
        "date", "debit", "credit", "balance", "narration",
        "description", "withdrawal", "deposit", "particulars",
        "remarks", "amount", "ref", "cheque", "txn", "post",
    ]
    header_idx = None
    for i, ll in enumerate(logical_lines):
        hits = sum(1 for kw in header_keywords if kw in ll["text"].lower())
        if hits >= 3:
            header_idx = i
            break

    if header_idx is None:
        warnings.append("OCR bbox: could not identify header row")
        return pd.DataFrame()

    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ingestion_config import COLUMN_ROLE_KEYWORDS

    _HEADER_VOCAB = {
        "date", "post", "txn", "tran", "transaction", "value", "booking",
        "narration", "description", "particulars", "remarks", "details",
        "debit", "credit", "withdrawal", "deposit", "withdrawals", "deposits",
        "balance", "closing", "running", "available",
        "ref", "cheque", "chq", "utr", "instrument", "reference",
        "dr", "cr", "no", "id",
        "amt", "amount", "paid", "money", "out", "in",
    }

    header_ll = logical_lines[header_idx]
    filtered_words = []
    filtered_lefts = []
    for word, left in zip(header_ll["words"], header_ll["lefts"]):
        w_clean = word.lower().strip(".:(),/-0123456789")
        if w_clean in _HEADER_VOCAB:
            filtered_words.append(word)
            filtered_lefts.append(left)

    if len(filtered_words) < 3:
        filtered_words = header_ll["words"]
        filtered_lefts = header_ll["lefts"]

    col_lefts = sorted(filtered_lefts)
    GAP = 40
    col_bands = [col_lefts[0]]
    for x in col_lefts[1:]:
        if x - col_bands[-1] > GAP:
            col_bands.append(x)

    def assign_col(x_pos):
        best = 0
        best_dist = abs(x_pos - col_bands[0])
        for i, band in enumerate(col_bands[1:], 1):
            d = abs(x_pos - band)
            if d < best_dist:
                best_dist = d
                best = i
        return best

    n_cols = len(col_bands)
    header_cells = [""] * n_cols
    for word, left in zip(filtered_words, filtered_lefts):
        col_idx = assign_col(left)
        header_cells[col_idx] = (header_cells[col_idx] + " " + word).strip()
    header_cells = [c if c else f"col_{i}" for i, c in enumerate(header_cells)]

    seen = {}
    deduped = []
    for name in header_cells:
        if name in seen:
            seen[name] += 1
            deduped.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            deduped.append(name)
    header_cells = deduped

    if len(set(header_cells)) < 3:
        warnings.append(f"OCR bbox: only {n_cols} distinct column bands found")
        return pd.DataFrame()

    rows = []
    for ll in logical_lines[header_idx + 1:]:
        text = ll["text"]
        if not text.strip() or len(text) < 8:
            continue
        if any(kw in text.lower() for kw in ["account holder", "ifsc", "statement period", "page"]):
            continue
        row_cells = [""] * n_cols
        for word, left in zip(ll["words"], ll["lefts"]):
            col_idx = assign_col(left)
            row_cells[col_idx] = (row_cells[col_idx] + " " + word).strip()
        if sum(1 for c in row_cells if c.strip()) >= 3:
            rows.append(row_cells)

    if not rows:
        warnings.append("OCR bbox: header found but no data rows could be reconstructed")
        return pd.DataFrame()

    return pd.DataFrame(rows, columns=header_cells)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------
def parse_json(file_path: str) -> tuple:
    import json
    warnings = []
    header_text = ""

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, list) and len(val) > 0:
                    records = val
                    header_text = str(key)
                    break
            else:
                return pd.DataFrame(), "", "json", ["No list of records found in JSON"]
        else:
            return pd.DataFrame(), "", "json", ["Unsupported JSON structure"]

        df = pd.json_normalize(records)
        return df, header_text, "json", warnings

    except Exception as e:
        return pd.DataFrame(), "", "json", [f"JSON parse error: {e}"]


# ---------------------------------------------------------------------------
# TSV / pipe-delimited parser
# ---------------------------------------------------------------------------
def parse_tsv(file_path: str) -> tuple:
    warnings = []
    header_text = ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            sample = f.read(500)
        delim = "\t" if "\t" in sample else "|" if "|" in sample else ","
        df = pd.read_csv(file_path, sep=delim, dtype=str,
                         encoding="utf-8", errors="replace")
        return df, header_text, "tsv", warnings
    except Exception as e:
        return pd.DataFrame(), "", "tsv", [f"TSV parse error: {e}"]