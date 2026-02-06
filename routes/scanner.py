import os
import uuid
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ai_scanner import scan_receipt
from database import db, NormalizedItem

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

    # Match normalized names to existing NormalizedItem records
    existing = {
        ni.name.lower(): ni.name
        for ni in NormalizedItem.query.filter_by(user_id=current_user.id).all()
    }
    for receipt in result:
        receipt["photo_filename"] = filename
        for item in receipt.get("items", []):
            norm = item.get("normalized_name", "")
            if norm and norm.lower() in existing:
                item["normalized_name"] = existing[norm.lower()]

    return jsonify(result)
