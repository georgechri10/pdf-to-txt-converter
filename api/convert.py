import io
import re
import math
import zipfile
import base64
import json
from http.server import BaseHTTPRequestHandler
import pdfplumber


def extract_coordinates_from_pdf(pdf_bytes: bytes) -> list:
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


def convert_to_txt_format(coordinates: list) -> str:
    if not coordinates:
        return ""
    total_points = len(coordinates)
    first_id = math.ceil(total_points / 10) * 10
    lines = []
    for i, (seq, x, y) in enumerate(coordinates):
        point_id = f"{first_id:02d}" if i == 0 else f"{seq:02d}"
        lines.append(f"{point_id},{x:.2f},{y:.2f},0.00,KTHMA")
    return '\n'.join(lines)


def process_zip(zip_bytes: bytes) -> bytes:
    output_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as input_zip:
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as output_zip:
            for filename in input_zip.namelist():
                if filename.lower().endswith('.pdf'):
                    pdf_bytes = input_zip.read(filename)
                    coords = extract_coordinates_from_pdf(pdf_bytes)
                    if coords:
                        txt_content = convert_to_txt_format(coords)
                        txt_filename = filename.rsplit('.', 1)[0] + '.txt'
                        if '/' in txt_filename:
                            txt_filename = txt_filename.split('/')[-1]
                        output_zip.writestr(txt_filename, txt_content.encode('utf-8'))
    output_buffer.seek(0)
    return output_buffer.getvalue()


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data)
            zip_base64 = data.get('file', '')
            zip_bytes = base64.b64decode(zip_base64)

            result_zip = process_zip(zip_bytes)
            result_base64 = base64.b64encode(result_zip).decode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'file': result_base64}).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
