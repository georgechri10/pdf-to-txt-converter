"""
PDF to TXT Converter Web App
Converts Greek cadastral/topographic PDFs to coordinate TXT files.
Upload a ZIP with PDFs, get a ZIP with TXTs.
"""

import io
import re
import math
import zipfile
import streamlit as st
import pdfplumber
from pathlib import Path


def extract_coordinates_from_pdf(pdf_bytes: bytes) -> list[tuple[int, float, float]]:
    """Extract coordinate data from PDF bytes."""
    coordinates = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    pattern = r'^(\d+)\s+([\d.]+)\s+([\d.]+)\s*$'

    for line in full_text.split('\n'):
        line = line.strip()
        match = re.match(pattern, line)
        if match:
            seq = int(match.group(1))
            x = float(match.group(2))
            y = float(match.group(3))
            if 400000 < x < 500000 and 4000000 < y < 5000000:
                coordinates.append((seq, x, y))

    coordinates.sort(key=lambda c: c[0])
    return coordinates


def convert_to_txt_format(coordinates: list[tuple[int, float, float]]) -> str:
    """Convert coordinates to TXT format."""
    if not coordinates:
        return ""

    total_points = len(coordinates)
    first_id = math.ceil(total_points / 10) * 10

    lines = []
    for i, (seq, x, y) in enumerate(coordinates):
        if i == 0:
            point_id = f"{first_id:02d}"
        else:
            point_id = f"{seq:02d}"
        line = f"{point_id},{x:.2f},{y:.2f},0.00,KTHMA"
        lines.append(line)

    return '\n'.join(lines)


def process_pdf(pdf_bytes: bytes) -> str | None:
    """Process a single PDF and return TXT content."""
    try:
        coordinates = extract_coordinates_from_pdf(pdf_bytes)
        if coordinates:
            return convert_to_txt_format(coordinates)
    except Exception:
        pass
    return None


def process_zip(zip_bytes: bytes) -> bytes:
    """Process ZIP containing PDFs, return ZIP containing TXTs."""
    output_buffer = io.BytesIO()

    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as input_zip:
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as output_zip:
            for filename in input_zip.namelist():
                if filename.lower().endswith('.pdf'):
                    pdf_bytes = input_zip.read(filename)
                    txt_content = process_pdf(pdf_bytes)

                    if txt_content:
                        txt_filename = Path(filename).stem + '.txt'
                        output_zip.writestr(txt_filename, txt_content.encode('utf-8'))

    output_buffer.seek(0)
    return output_buffer.getvalue()


# --- Streamlit UI ---

st.set_page_config(
    page_title="PDF to TXT Converter",
    page_icon="📄",
    layout="centered"
)

st.title("PDF to TXT Converter")
st.write("Upload a ZIP file containing PDFs. Get a ZIP with converted TXT files.")

uploaded_file = st.file_uploader(
    "Choose a ZIP file",
    type=['zip'],
    help="ZIP file containing cadastral PDF files"
)

if uploaded_file is not None:
    with st.spinner('Converting...'):
        try:
            result_zip = process_zip(uploaded_file.getvalue())

            # Count files in result
            with zipfile.ZipFile(io.BytesIO(result_zip), 'r') as zf:
                file_count = len(zf.namelist())

            if file_count > 0:
                st.success(f"Converted {file_count} files!")

                st.download_button(
                    label="Download TXT files (ZIP)",
                    data=result_zip,
                    file_name="converted_txt_files.zip",
                    mime="application/zip"
                )
            else:
                st.error("No valid PDFs found in the ZIP file.")

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")
