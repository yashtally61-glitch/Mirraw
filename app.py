"""
Mirraw Invoice Extractor
========================
Extracts invoice data from Aashirwad Garments → Mirraw PDF invoices (inside a ZIP),
and exports a formatted Excel report.

Run locally:
    pip install streamlit pdfplumber openpyxl pandas
    streamlit run app.py

Deploy: push to GitHub → share.streamlit.io → select repo → main file: app.py
"""

import io
import re
import zipfile

import openpyxl
import pandas as pd
import pdfplumber
import streamlit as st
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE CONFIG  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Mirraw Invoice Extractor",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
#  CUSTOM CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .stApp { background-color: #F0F4FF; }

    .main-title {
        font-size: 2.2rem; font-weight: 800;
        color: #1A237E; margin-bottom: 4px;
    }
    .sub-title {
        font-size: 1rem; color: #5C6BC0; margin-bottom: 24px;
    }
    .metric-card {
        background: white; border-radius: 12px;
        padding: 16px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.07);
        margin-bottom: 10px;
    }
    .metric-label {
        font-size: 0.75rem; color: #888;
        font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
    }
    .metric-value {
        font-size: 1.6rem; font-weight: 800; margin-top: 2px;
    }
    .section-header {
        font-size: 1.05rem; font-weight: 700; color: #1A237E;
        margin: 20px 0 10px; padding-bottom: 6px;
        border-bottom: 2px solid #C5CAE9;
    }
    .footer {
        text-align: center; color: #9E9E9E; font-size: 0.8rem;
        margin-top: 40px; padding: 16px;
        border-top: 1px solid #E0E0E0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _to_float(val: str) -> float:
    """Safely convert a string to float; return 0.0 on failure."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_nums(text: str):
    """Return list of numeric strings found in text."""
    return re.findall(r"\d+(?:\.\d+)?", text)


# ─────────────────────────────────────────────────────────────────────────────
#  CORE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_from_pdf(pdf_bytes: bytes) -> list:
    """
    Extract invoice line items from a single PDF.

    Returns a list of dicts (one per line item).
    Returns an empty list if the PDF is not an Aashirwad Garments tax invoice.
    """
    results = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_lines = []
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"
                all_lines.extend(text.split("\n"))

        # Filter: only Aashirwad GST invoices
        if "AASHIRWAD" not in full_text.upper():
            return results
        if "HSN_CODE" not in full_text and "HSN CODE" not in full_text:
            return results

        # ── Seller ────────────────────────────────────────────────────────
        seller_name = "AASHIRWAD GARMENTS"
        m = re.search(r"GST\s*no\s*:\s*([A-Z0-9]{15})", full_text)
        seller_gstin = m.group(1) if m else ""

        # ── Buyer & Registration Type ─────────────────────────────────────
        m = re.search(r"GST\s*NUMBER\s*:\s*([A-Z0-9]{15})", full_text)
        if m:
            buyer_gstin = m.group(1)
            buyer_name = "Mirraw Online Services Pvt Ltd."
            registration_type = "Registered"
        else:
            buyer_gstin = "N/A"
            m2 = re.search(r"Name:\s*(.+?)(?:\s{2,}|Address:|Invoice)", full_text)
            buyer_name = m2.group(1).strip() if m2 else "Individual / Unregistered"
            registration_type = "Unregistered"

        # ── Invoice header ────────────────────────────────────────────────
        m = re.search(r"Invoice No[:\s]+([\w/]+)", full_text)
        invoice_no = m.group(1).strip() if m else ""

        m = re.search(r"Invoice Date\s*[:\s]+([\d/\s]+)", full_text)
        invoice_date = m.group(1).strip().replace(" ", "") if m else ""

        m = re.search(r"Purchase Order No[:\s]+([\w]+)", full_text)
        purchase_order_no = m.group(1).strip() if m else ""

        m = re.search(r"Place of Supply\s*:\s*([^\n]+)", full_text)
        place_of_supply = m.group(1).strip() if m else ""

        m = re.search(r"State Code\s*:\s*(\d+)", full_text)
        state_code = m.group(1).strip() if m else ""

        # ── Line items ────────────────────────────────────────────────────
        i = 0
        while i < len(all_lines):
            line = all_lines[i]

            if "[sku:" not in line.lower():
                i += 1
                continue

            # Pattern A – SKU code already closed on the same line BEFORE HSN
            #   e.g.  "Tops [sku: ABC123] 61149090 1 299.0 ..."
            m_inline = re.search(r"\[sku:\s*([A-Za-z0-9_\-]+)\]\s*(\d{8})", line)

            if m_inline:
                sku_code = m_inline.group(1).strip()
                hsn = m_inline.group(2)
                m2 = re.match(r"^(.+?)\s*\[sku:", line)
                sku_name = m2.group(1).strip() if m2 else ""
                size = ""
                if i + 1 < len(all_lines):
                    m3 = re.search(
                        r"\[size[:\s]+([^\]]+)\]", all_lines[i + 1], re.IGNORECASE
                    )
                    if m3:
                        size = m3.group(1).strip()
                nums = _parse_nums(line[line.find(hsn) + len(hsn):])

            else:
                # Pattern B – SKU code is on the NEXT line
                #   e.g.  "Plus size kurtis [sku: 61149090 1 590.0 ..."
                #         "SKUCODE] [size: 3XL]"
                m_hsn = re.search(r"\b(\d{8})\b", line)
                hsn = m_hsn.group(1) if m_hsn else ""

                next1 = all_lines[i + 1] if i + 1 < len(all_lines) else ""
                next2 = all_lines[i + 2] if i + 2 < len(all_lines) else ""

                m2 = re.match(r"^([A-Za-z0-9_\-]+)\]", next1.strip())
                sku_code = m2.group(1).strip() if m2 else ""

                m3 = re.match(r"^(.+?)\s*\[sku:", line)
                sku_name = m3.group(1).strip() if m3 else ""

                m4 = re.search(
                    r"\[size[:\s]+([^\]]+)\]", next1 + " " + next2, re.IGNORECASE
                )
                size = m4.group(1).strip() if m4 else ""

                nums = _parse_nums(line[line.find(hsn) + len(hsn):] if hsn else line)

            # Skip if we couldn't identify the product
            if not sku_code and not hsn:
                i += 1
                continue

            # Numeric columns after HSN:
            #   With '-' discount  (7 values):  QTY VALUE NET  TAXABLE IGST_RATE IGST_AMT TOTAL
            #   With 0.0 discount  (8 values):  QTY VALUE DISC NET TAXABLE IGST_RATE IGST_AMT TOTAL
            qty = taxable = igst_rate = igst_amt = total = ""
            if len(nums) >= 8:
                qty, taxable, igst_rate, igst_amt, total = (
                    nums[0], nums[4], nums[5], nums[6], nums[7],
                )
            elif len(nums) >= 7:
                qty, taxable, igst_rate, igst_amt, total = (
                    nums[0], nums[3], nums[4], nums[5], nums[6],
                )
            elif len(nums) >= 6:
                qty, taxable, igst_rate, igst_amt = (
                    nums[0], nums[3], nums[4], nums[5],
                )
            elif len(nums) >= 1:
                qty = nums[0]

            results.append(
                {
                    "Invoice No": invoice_no,
                    "Invoice Date": invoice_date,
                    "Purchase Order No": purchase_order_no,
                    "Seller Name": seller_name,
                    "Seller GSTIN": seller_gstin,
                    "Buyer Name": buyer_name,
                    "Buyer GSTIN": buyer_gstin,
                    "Registration Type": registration_type,
                    "Place of Supply": place_of_supply,
                    "State Code": state_code,
                    "SKU Name": sku_name,
                    "SKU Code": sku_code,
                    "Size": size,
                    "HSN Code": hsn,
                    "Qty": qty,
                    "Taxable Amount": taxable,
                    "GST Rate (%)": igst_rate,
                    "GST Amount (IGST)": igst_amt,
                    "Total Amount": total,
                }
            )
            i += 1

    except Exception:
        # Silently skip unreadable PDFs
        pass

    return results


def process_zip(zip_bytes: bytes) -> list:
    """Extract all PDFs from a ZIP and return deduplicated invoice rows."""
    all_data = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        pdf_names = sorted(
            [n for n in z.namelist() if n.lower().endswith(".pdf")]
        )
        total = len(pdf_names)
        progress = st.progress(0, text="Reading invoices…")
        for idx, name in enumerate(pdf_names):
            with z.open(name) as f:
                all_data.extend(extract_from_pdf(f.read()))
            progress.progress(
                (idx + 1) / total,
                text=f"Processing {idx + 1}/{total} PDFs…",
            )
        progress.empty()

    # Deduplicate on (Invoice No, SKU Code)
    seen: set = set()
    unique = []
    for row in all_data:
        if not row["SKU Code"] and not row["HSN Code"]:
            continue
        key = (row["Invoice No"], row["SKU Code"])
        if key not in seen:
            seen.add(key)
            unique.append(row)

    unique.sort(key=lambda r: r["Invoice No"])
    return unique


# ─────────────────────────────────────────────────────────────────────────────
#  EXCEL BUILDER
# ─────────────────────────────────────────────────────────────────────────────
HEADERS = [
    "Sr No", "Invoice No", "Invoice Date", "Purchase Order No",
    "Seller Name", "Seller GSTIN",
    "Buyer Name", "Buyer GSTIN", "Registration Type",
    "Place of Supply", "State Code",
    "SKU Name", "SKU Code", "Size", "HSN Code",
    "Qty", "Taxable Amount (₹)", "GST Rate (%)", "GST Amount/IGST (₹)", "Total Amount (₹)",
]
COL_WIDTHS = [
    6, 22, 13, 22, 24, 18, 28, 18, 16, 18, 11, 20, 22, 12, 12, 6, 18, 13, 20, 16,
]
DATA_FIELDS = [
    "Invoice No", "Invoice Date", "Purchase Order No",
    "Seller Name", "Seller GSTIN",
    "Buyer Name", "Buyer GSTIN", "Registration Type",
    "Place of Supply", "State Code",
    "SKU Name", "SKU Code", "Size", "HSN Code",
    "Qty", "Taxable Amount", "GST Rate (%)", "GST Amount (IGST)", "Total Amount",
]
NUMERIC_FIELDS = {"Qty", "Taxable Amount", "GST Rate (%)", "GST Amount (IGST)", "Total Amount"}
# (col_index_1based, column_letter) for SUM totals
SUM_COLS = [(16, "P"), (17, "Q"), (19, "S"), (20, "T")]

_THIN = Side(style="thin", color="D0D0D0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_NAVY_FILL = PatternFill(start_color="1A237E", end_color="1A237E", fill_type="solid")
_EVEN_FILL = PatternFill(start_color="EEF0FB", end_color="EEF0FB", fill_type="solid")
_ODD_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
_REG_FILL = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
_UNREG_FILL = PatternFill(start_color="FFF3E0", end_color="FFF3E0", fill_type="solid")


def _header_font(bold=True, color="FFFFFF"):
    return Font(name="Arial", bold=bold, color=color, size=10)


def _data_font(color="000000", bold=False):
    return Font(name="Arial", size=9, color=color, bold=bold)


def build_excel(data: list) -> bytes:
    """Build and return a formatted Excel workbook as bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mirraw Invoices"

    # ── Header row ──────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 40
    for col, (header, width) in enumerate(zip(HEADERS, COL_WIDTHS), 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = _header_font()
        cell.fill = _NAVY_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(col)].width = width

    # ── Data rows ────────────────────────────────────────────────────────────
    for row_idx, row in enumerate(data, 2):
        ws.row_dimensions[row_idx].height = 18
        row_fill = _EVEN_FILL if row_idx % 2 == 0 else _ODD_FILL

        # Sr No (col 1)
        cell = ws.cell(row=row_idx, column=1, value=row_idx - 1)
        cell.font = _data_font()
        cell.fill = row_fill
        cell.border = _BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")

        # Data fields (cols 2‥20)
        for col, field in enumerate(DATA_FIELDS, 2):
            raw = row.get(field, "")
            # Convert numeric fields to float for proper Excel formatting
            if field in NUMERIC_FIELDS:
                val = _to_float(raw) if raw else ""
            else:
                val = raw

            cell = ws.cell(row=row_idx, column=col, value=val)
            cell.border = _BORDER

            if field == "Registration Type":
                if val == "Registered":
                    cell.font = _data_font(color="1B5E20", bold=True)
                    cell.fill = _REG_FILL
                else:
                    cell.font = _data_font(color="BF360C", bold=True)
                    cell.fill = _UNREG_FILL
                cell.alignment = Alignment(horizontal="center", vertical="center")

            elif field in {"Taxable Amount", "GST Amount (IGST)", "Total Amount"}:
                cell.font = _data_font()
                cell.fill = row_fill
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")

            elif field in {"Qty", "GST Rate (%)"}:
                cell.font = _data_font()
                cell.fill = row_fill
                cell.number_format = "0.00"
                cell.alignment = Alignment(horizontal="center", vertical="center")

            elif field == "Invoice Date":
                cell.font = _data_font()
                cell.fill = row_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")

            else:
                cell.font = _data_font()
                cell.fill = row_fill
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # ── Totals row ───────────────────────────────────────────────────────────
    total_row = len(data) + 2
    for col in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=total_row, column=col)
        cell.font = _header_font()
        cell.fill = _NAVY_FILL
        cell.border = _BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.cell(row=total_row, column=1, value="TOTAL")

    for col_idx, col_letter in SUM_COLS:
        cell = ws.cell(
            row=total_row,
            column=col_idx,
            value=f"=SUM({col_letter}2:{col_letter}{total_row - 1})",
        )
        cell.number_format = "#,##0.00"
        cell.alignment = Alignment(horizontal="right", vertical="center")

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
DISPLAY_COLS = [
    "Invoice No", "Invoice Date", "Purchase Order No",
    "Seller Name", "Seller GSTIN",
    "Buyer Name", "Buyer GSTIN", "Registration Type",
    "Place of Supply", "SKU Name", "SKU Code", "Size", "HSN Code",
    "Qty", "Taxable Amount", "GST Rate (%)", "GST Amount (IGST)", "Total Amount",
]

FORMAT_MAP = {
    "Taxable Amount": "₹{:.2f}",
    "GST Amount (IGST)": "₹{:.2f}",
    "Total Amount": "₹{:.2f}",
}


def _highlight_reg(val):
    if val == "Registered":
        return "background-color:#E8F5E9; color:#1B5E20; font-weight:bold"
    if val == "Unregistered":
        return "background-color:#FFF3E0; color:#BF360C; font-weight:bold"
    return ""


def render_metric(col, label, value, color):
    with col:
        st.markdown(
            f"""
            <div class="metric-card" style="border-left:5px solid {color}">
                <div class="metric-label">{label}</div>
                <div class="metric-value" style="color:{color}">{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧾 Mirraw Invoice Extractor")
    st.markdown("---")
    st.markdown(
        """
**How to use:**
1. Upload your **ZIP** file of invoice PDFs
2. Click **Extract Invoices**
3. Preview & filter the data
4. Download Excel or CSV

**Registration Types:**
- 🟢 **Registered** — Buyer has GSTIN
- 🟠 **Unregistered** — Individual, no GSTIN
        """
    )
    st.markdown("---")
    st.caption("Python · pdfplumber · openpyxl · Streamlit")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN UI
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    '<div class="main-title">🧾 Mirraw Invoice Extractor</div>', unsafe_allow_html=True
)
st.markdown(
    '<div class="sub-title">Extract & Export · Aashirwad Garments → Mirraw Sales Invoices</div>',
    unsafe_allow_html=True,
)

uploaded = st.file_uploader(
    "📁 Upload Invoice ZIP file",
    type=["zip"],
    help="ZIP containing Aashirwad Garments PDF invoices",
)

if uploaded:
    st.success(f"✅ Uploaded: **{uploaded.name}** ({uploaded.size / 1024:.1f} KB)")

    if st.button("🚀 Extract Invoices", use_container_width=True, type="primary"):
        with st.spinner("Scanning PDFs and extracting data…"):
            data = process_zip(uploaded.read())

        if not data:
            st.error(
                "❌ No Aashirwad Garments invoices found. "
                "Please check the ZIP contains the correct PDF files."
            )
        else:
            st.session_state["invoice_data"] = data
            st.success(f"✅ Extracted **{len(data)} invoice line items** successfully!")

# ── Results section ──────────────────────────────────────────────────────────
if "invoice_data" in st.session_state:
    data = st.session_state["invoice_data"]
    df = pd.DataFrame(data)

    # ── Metrics ──────────────────────────────────────────────────────────────
    total_invoices = int(df["Invoice No"].nunique())
    total_qty = int(df["Qty"].apply(_to_float).sum())
    total_taxable = df["Taxable Amount"].apply(_to_float).sum()
    total_gst = df["GST Amount (IGST)"].apply(_to_float).sum()
    total_amount = df["Total Amount"].apply(_to_float).sum()
    reg_count = int((df["Registration Type"] == "Registered").sum())
    unreg_count = int((df["Registration Type"] == "Unregistered").sum())

    cols = st.columns(6)
    render_metric(cols[0], "📄 Total Invoices", str(total_invoices), "#3F51B5")
    render_metric(cols[1], "📦 Total Qty", str(total_qty), "#00897B")
    render_metric(cols[2], "💰 Taxable Amount", f"₹{total_taxable:,.2f}", "#F57C00")
    render_metric(cols[3], "🏛️ Total GST", f"₹{total_gst:,.2f}", "#C62828")
    render_metric(cols[4], "🟢 Registered", str(reg_count), "#2E7D32")
    render_metric(cols[5], "🟠 Unregistered", str(unreg_count), "#BF360C")

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="section-header">🔍 Filter & Preview</div>', unsafe_allow_html=True
    )
    fc1, fc2, fc3 = st.columns(3)

    with fc1:
        inv_filter = st.multiselect(
            "Invoice No", options=sorted(df["Invoice No"].unique()), default=[]
        )
    with fc2:
        reg_filter = st.multiselect(
            "Registration Type",
            options=["Registered", "Unregistered"],
            default=[],
        )
    with fc3:
        sku_filter = st.multiselect(
            "SKU Code", options=sorted(df["SKU Code"].unique()), default=[]
        )

    filtered_df = df.copy()
    if inv_filter:
        filtered_df = filtered_df[filtered_df["Invoice No"].isin(inv_filter)]
    if reg_filter:
        filtered_df = filtered_df[filtered_df["Registration Type"].isin(reg_filter)]
    if sku_filter:
        filtered_df = filtered_df[filtered_df["SKU Code"].isin(sku_filter)]

    st.info(f"Showing **{len(filtered_df)}** of {len(df)} records")

    # ── Table ─────────────────────────────────────────────────────────────────
    display_df = filtered_df[DISPLAY_COLS].reset_index(drop=True)

    # Build format dict only for columns that exist and have non-empty numeric data
    fmt = {}
    for col, fmt_str in FORMAT_MAP.items():
        if col in display_df.columns:
            # Only apply format if column has at least some numeric-looking values
            has_numeric = display_df[col].apply(
                lambda v: bool(re.match(r"^\d+(?:\.\d+)?$", str(v)))
            ).any()
            if has_numeric:
                fmt[col] = lambda v, f=fmt_str: (
                    f.format(float(v)) if v and re.match(r"^\d+(?:\.\d+)?$", str(v)) else str(v)
                )

    styled = (
        display_df.style
        .applymap(_highlight_reg, subset=["Registration Type"])
        .format(fmt)
    )

    st.dataframe(styled, use_container_width=True, height=420)

    # ── Charts ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="section-header">📊 Summary Charts</div>', unsafe_allow_html=True
    )
    ch1, ch2 = st.columns(2)

    with ch1:
        st.markdown("**Sales by Invoice Date (₹)**")
        date_chart = (
            df.assign(Amount=df["Total Amount"].apply(_to_float))
            .groupby("Invoice Date")["Amount"]
            .sum()
            .reset_index()
            .rename(columns={"Invoice Date": "Date", "Amount": "Total (₹)"})
            .set_index("Date")
        )
        st.bar_chart(date_chart)

    with ch2:
        st.markdown("**Registration Type Distribution**")
        reg_series = df["Registration Type"].value_counts()
        reg_chart = pd.DataFrame(
            {"Count": reg_series.values}, index=reg_series.index
        )
        reg_chart.index.name = "Type"
        st.bar_chart(reg_chart)

    # ── Downloads ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div class="section-header">⬇️ Download Report</div>', unsafe_allow_html=True
    )

    dc1, dc2 = st.columns(2)

    with dc1:
        excel_bytes = build_excel(data)
        st.download_button(
            label="📥 Download Excel Report (.xlsx)",
            data=excel_bytes,
            file_name="Mirraw_Invoices_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )

    with dc2:
        csv_bytes = filtered_df[DISPLAY_COLS].to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📄 Download Filtered CSV",
            data=csv_bytes,
            file_name="Mirraw_Invoices_Filtered.csv",
            mime="text/csv",
            use_container_width=True,
        )

else:
    # Placeholder state
    st.markdown(
        """
        <div style="text-align:center;padding:60px 0;color:#9E9E9E">
            <div style="font-size:4rem">📁</div>
            <div style="font-size:1.2rem;margin-top:12px">Upload a ZIP file to get started</div>
            <div style="font-size:0.9rem;margin-top:8px">
                Supports Aashirwad Garments → Mirraw PDF invoices
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Footer
st.markdown(
    '<div class="footer">Mirraw Invoice Extractor · Aashirwad Garments · Streamlit + Python</div>',
    unsafe_allow_html=True,
)
