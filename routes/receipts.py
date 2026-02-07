from datetime import date, datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from database import db, Receipt, LineItem, NormalizedItem

receipts_bp = Blueprint("receipts", __name__)


def _upsert_normalized(user_id, normalized_name, category, unit):
    """Create or update a NormalizedItem record for the user."""
    if not normalized_name:
        return
    existing = NormalizedItem.query.filter_by(
        user_id=user_id, name=normalized_name
    ).first()
    if not existing:
        ni = NormalizedItem(
            user_id=user_id,
            name=normalized_name,
            category=category,
            default_unit=unit,
        )
        db.session.add(ni)


def _save_line_items(receipt, items_data, user_id):
    """Save line items from a list of dicts to a receipt."""
    for item in items_data:
        line_total = item.get("line_total") or 0
        quantity = item.get("quantity") or 1
        unit_price = item.get("unit_price")
        if not unit_price and quantity:
            unit_price = round(line_total / quantity, 2)

        li = LineItem(
            receipt_id=receipt.id,
            item_name=item.get("item_name", "Unknown"),
            normalized_name=item.get("normalized_name"),
            category=item.get("category"),
            quantity=quantity,
            unit=item.get("unit", "each"),
            unit_price=unit_price,
            line_total=line_total,
            notes=item.get("notes"),
            rating=item.get("rating"),
        )
        db.session.add(li)
        _upsert_normalized(user_id, li.normalized_name, li.category, li.unit)


@receipts_bp.route("/api/receipts", methods=["POST"])
@login_required
def create_receipt():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    receipt_date = data.get("receipt_date")
    if receipt_date:
        receipt_date = date.fromisoformat(receipt_date)
    else:
        receipt_date = date.today()

    total = data.get("total")
    if total is None:
        return jsonify({"error": "Total is required"}), 400

    # Duplicate detection: same store + date + total + item count
    store_name = data.get("store_name")
    items_data = data.get("items", [])
    item_count = len(items_data)
    candidates = Receipt.query.filter_by(
        user_id=current_user.id,
        receipt_date=receipt_date,
        total=total,
    ).all()
    for candidate in candidates:
        # Case-insensitive store name comparison
        c_name = (candidate.store_name or "").strip().lower()
        s_name = (store_name or "").strip().lower()
        if c_name != s_name:
            continue
        # Check line item count matches
        if candidate.line_items.count() != item_count:
            continue
        return jsonify({
            "error": "Duplicate receipt",
            "message": f"A receipt from {store_name or 'this store'} on {receipt_date} for ${total:.2f} with {item_count} item(s) already exists.",
            "duplicate_id": candidate.id,
        }), 409

    receipt = Receipt(
        user_id=current_user.id,
        store_name=data.get("store_name"),
        store_address=data.get("store_address"),
        store_category=data.get("store_category"),
        receipt_date=receipt_date,
        subtotal=data.get("subtotal"),
        tax=data.get("tax"),
        tip=data.get("tip"),
        total=total,
        payment_method=data.get("payment_method"),
        currency=data.get("currency", "USD"),
        photo_filename=data.get("photo_filename"),
        photo_data=data.get("photo_data"),
        notes=data.get("notes"),
    )
    db.session.add(receipt)
    db.session.flush()  # get receipt.id

    items_data = data.get("items", [])
    _save_line_items(receipt, items_data, current_user.id)

    db.session.commit()
    return jsonify(receipt.to_dict(include_items=True)), 201


@receipts_bp.route("/api/receipts", methods=["GET"])
@login_required
def list_receipts():
    query = Receipt.query.filter_by(user_id=current_user.id)

    # Filters
    store_category = request.args.get("store_category")
    if store_category:
        query = query.filter_by(store_category=store_category)

    store_name = request.args.get("store_name")
    if store_name:
        query = query.filter(Receipt.store_name.ilike(f"%{store_name}%"))

    date_from = request.args.get("date_from")
    if date_from:
        query = query.filter(Receipt.receipt_date >= date.fromisoformat(date_from))

    date_to = request.args.get("date_to")
    if date_to:
        query = query.filter(Receipt.receipt_date <= date.fromisoformat(date_to))

    query = query.order_by(Receipt.receipt_date.desc())
    receipts = query.all()
    return jsonify([r.to_dict(include_items=True) for r in receipts])


@receipts_bp.route("/api/receipts/<int:receipt_id>", methods=["GET"])
@login_required
def get_receipt(receipt_id):
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
    if not receipt:
        return jsonify({"error": "Receipt not found"}), 404
    return jsonify(receipt.to_dict(include_items=True))


@receipts_bp.route("/api/receipts/<int:receipt_id>", methods=["PUT"])
@login_required
def update_receipt(receipt_id):
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
    if not receipt:
        return jsonify({"error": "Receipt not found"}), 404

    data = request.get_json()

    for field in [
        "store_name", "store_address", "store_category", "subtotal",
        "tax", "tip", "total", "payment_method", "currency", "notes",
    ]:
        if field in data:
            setattr(receipt, field, data[field])

    if "receipt_date" in data:
        receipt.receipt_date = date.fromisoformat(data["receipt_date"])

    # If items are provided, replace all line items
    if "items" in data:
        LineItem.query.filter_by(receipt_id=receipt.id).delete()
        db.session.flush()
        _save_line_items(receipt, data["items"], current_user.id)

    db.session.commit()
    return jsonify(receipt.to_dict(include_items=True))


@receipts_bp.route("/api/receipts/<int:receipt_id>", methods=["DELETE"])
@login_required
def delete_receipt(receipt_id):
    receipt = Receipt.query.filter_by(id=receipt_id, user_id=current_user.id).first()
    if not receipt:
        return jsonify({"error": "Receipt not found"}), 404
    db.session.delete(receipt)
    db.session.commit()
    return jsonify({"success": True})
