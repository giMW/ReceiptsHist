import os
import uuid
import base64
from flask import Blueprint, request, jsonify, current_app, send_from_directory, Response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ai_scanner import scan_receipt
from database import db, NormalizedItem, Receipt

scanner_bp = Blueprint("scanner", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@scanner_bp.route("/api/scan", methods=["POST"])
@login_required
def scan():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not _allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    # Save with unique name
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    try:
        result = scan_receipt(filepath)
    except Exception as e:
        return jsonify({"error": f"Scan failed: {str(e)}"}), 500

    # scan_receipt returns a list of receipts, or a dict with "error"
    if isinstance(result, dict) and "error" in result:
        return jsonify(result), 500

    # Read and encode image for database storage
    photo_data = None
    try:
        # For PDFs, the scanner creates a JPG - try to find it
        if ext == "pdf":
            jpg_path = filepath.rsplit(".", 1)[0] + "_page0.jpg"
            if os.path.exists(jpg_path):
                with open(jpg_path, "rb") as f:
                    photo_data = base64.b64encode(f.read()).decode("utf-8")
                os.remove(jpg_path)  # Clean up
        else:
            with open(filepath, "rb") as f:
                photo_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        pass  # Photo storage is optional

    # Clean up uploaded file (we have it in base64 now)
    try:
        os.remove(filepath)
    except Exception:
        pass

    # Match normalized names to existing NormalizedItem records
    existing = {
        ni.name.lower(): ni.name
        for ni in NormalizedItem.query.filter_by(user_id=current_user.id).all()
    }
    for receipt in result:
        receipt["photo_filename"] = filename
        receipt["photo_data"] = photo_data
        for item in receipt.get("items", []):
            norm = item.get("normalized_name", "")
            if norm and norm.lower() in existing:
                item["normalized_name"] = existing[norm.lower()]

    return jsonify(result)


@scanner_bp.route("/api/uploads/<filename>")
@login_required
def serve_upload(filename):
    """Serve uploaded receipt images (only if user owns the receipt)."""
    # Security: verify user owns a receipt with this photo
    receipt = Receipt.query.filter_by(
        user_id=current_user.id, photo_filename=filename
    ).first()
    if not receipt:
        return jsonify({"error": "Not found"}), 404

    # Try local file first (development)
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(filepath):
        return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)

    # Fall back to database storage (production/Render)
    if receipt.photo_data:
        image_data = base64.b64decode(receipt.photo_data)
        # Determine mime type from filename
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
        mime_types = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
        mime = mime_types.get(ext, "image/jpeg")
        return Response(image_data, mimetype=mime)

    return jsonify({"error": "Image not found"}), 404
