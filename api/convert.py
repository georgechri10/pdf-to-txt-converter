from flask import Flask, request, jsonify
import io
import re
import math
import zipfile
import base64
from pdfminer.high_level import extract_text

app = Flask(__name__)


def extract_coordinates_from_pdf(pdf_bytes):
    coordinates = []
    try:
        full_text = extract_text(io.BytesIO(pdf_bytes))

        pattern = r'(\d+)\s+(4\d{5}[\d.]*)\s+(4\d{6}[\d.]*)'
        for match in re.finditer(pattern, full_text):
            seq = int(match.group(1))
            x = float(match.group(2))
            y = float(match.group(3))
            if 400000 < x < 500000 and 4000000 < y < 5000000:
                coordinates.append((seq, x, y))

        coordinates.sort(key=lambda c: c[0])
        # Remove duplicates
        seen = set()
        unique = []
        for c in coordinates:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        coordinates = unique

    except Exception as e:
        print(f"PDF error: {e}")
    return coordinates


def convert_to_txt_format(coordinates):
    if not coordinates:
        return ""
    total_points = len(coordinates)
    first_id = math.ceil(total_points / 10) * 10
    lines = []
    for i, (seq, x, y) in enumerate(coordinates):
        point_id = f"{first_id:02d}" if i == 0 else f"{seq:02d}"
        lines.append(f"{point_id},{x:.2f},{y:.2f},0.00,KTHMA")
    return '\n'.join(lines)


def process_zip(zip_bytes):
    output_buffer = io.BytesIO()
    file_count = 0
    errors = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as input_zip:
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as output_zip:
            for filename in input_zip.namelist():
                if filename.lower().endswith('.pdf') and not filename.startswith('__MACOSX'):
                    try:
                        pdf_bytes = input_zip.read(filename)
                        coords = extract_coordinates_from_pdf(pdf_bytes)
                        if coords:
                            txt_content = convert_to_txt_format(coords)
                            txt_filename = filename.rsplit('.', 1)[0] + '.txt'
                            if '/' in txt_filename:
                                txt_filename = txt_filename.split('/')[-1]
                            output_zip.writestr(txt_filename, txt_content.encode('utf-8'))
                            file_count += 1
                    except Exception as e:
                        errors.append(f"{filename}: {str(e)}")

    output_buffer.seek(0)
    return output_buffer.getvalue(), file_count, errors


@app.route('/api/convert', methods=['POST', 'OPTIONS'])
def convert():
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    try:
        data = request.get_json(force=True)
        if not data or 'file' not in data:
            return jsonify({'error': 'No file provided'}), 400

        zip_base64 = data['file']
        zip_bytes = base64.b64decode(zip_base64)

        result_zip, count, errors = process_zip(zip_bytes)

        if count == 0:
            err_msg = 'No valid PDFs found'
            if errors:
                err_msg += ': ' + '; '.join(errors[:3])
            return jsonify({'error': err_msg}), 400

        result_base64 = base64.b64encode(result_zip).decode('utf-8')

        response = jsonify({'file': result_base64, 'count': count})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except zipfile.BadZipFile:
        return jsonify({'error': 'Invalid ZIP file'}), 400
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


# Vercel serverless handler
app.debug = False
