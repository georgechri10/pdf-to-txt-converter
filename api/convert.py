from flask import Flask, request, jsonify
import io
import ntpath
import posixpath
import re
import math
import zipfile
import base64
from pdfminer.high_level import extract_text

app = Flask(__name__)


def safe_basename(filename):
    """Safely extract just the filename, handling both Unix and Windows path separators."""
    # First handle forward slashes (Unix paths)
    filename = posixpath.basename(filename)
    # Then handle backslashes (Windows paths)
    filename = ntpath.basename(filename)
    return filename


def extract_coordinates_from_pdf(pdf_bytes):
    """Extract coordinates from cadastral PDF."""
    coordinates = []
    try:
        full_text = extract_text(io.BytesIO(pdf_bytes))

        # Find where X column starts (after "X" header)
        # Then find Y column (after "Y" header or after X values)
        # Table values have exactly 2 decimal places

        # Pattern: exactly 6 digits with 2 decimals for X (41XXXX.XX)
        # Pattern: exactly 7 digits with 2 decimals for Y (449XXXX.XX)

        x_pattern = r'\b(41\d{4}\.\d{2})\b'
        y_pattern = r'\b(449\d{4}\.\d{2})\b'

        # Find all matches in order
        x_coords = re.findall(x_pattern, full_text)
        y_coords = re.findall(y_pattern, full_text)

        # Pair them up (keep all, including closing point duplicates)
        min_len = min(len(x_coords), len(y_coords))

        for i in range(min_len):
            x = float(x_coords[i])
            y = float(y_coords[i])
            coordinates.append((i, x, y))

    except Exception as e:
        print(f"PDF error: {e}")

    return coordinates


def convert_to_txt_format(coordinates):
    """Convert coordinates to output TXT format."""
    if not coordinates:
        return ""

    total_points = len(coordinates)
    first_id = math.ceil(total_points / 10) * 10

    lines = []
    for i, (seq, x, y) in enumerate(coordinates):
        if i == 0:
            point_id = f"{first_id:02d}"
        else:
            point_id = f"{i:02d}"
        lines.append(f"{point_id},{x:.2f},{y:.2f},0.00,KTHMA")

    return '\n'.join(lines)


def process_zip(zip_bytes):
    """Process ZIP file containing PDFs."""
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
                            # Use safe_basename to safely extract filename and prevent path traversal
                            txt_filename = safe_basename(filename.rsplit('.', 1)[0] + '.txt')
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


if __name__ == '__main__':
    app.run(debug=True, port=5000)
