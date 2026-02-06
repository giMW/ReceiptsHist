from datetime import date
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from database import db, LineItem, Receipt, NormalizedItem

items_bp = Blueprint("items", __name__)


@items_bp.route("/api/items/<int:item_id>", methods=["PUT"])
@login_required
def update_item(item_id):
    item = (
        LineItem.query
        .join(Receipt)
        .filter(LineItem.id == item_id, Receipt.user_id == current_user.id)
        .first()
    )
    if not item:
        return jsonify({"error": "Item not found"}), 404

    data = request.get_json()

    for field in [
        "item_name", "normalized_name", "category", "quantity",
        "unit", "unit_price", "line_total", "notes", "rating",
    ]:
        if field in data:
            setattr(item, field, data[field])

    # Update normalized item record
    if item.normalized_name:
        existing = NormalizedItem.query.filter_by(
            user_id=current_user.id, name=item.normalized_name
        ).first()
        if not existing:
            ni = NormalizedItem(
                user_id=current_user.id,
                name=item.normalized_name,
                category=item.category,
                default_unit=item.unit,
            )
            db.session.add(ni)

    db.session.commit()
    return jsonify(item.to_dict())


@items_bp.route("/api/items/search", methods=["GET"])
@login_required
def search_items():
    query = (
        LineItem.query
        .join(Receipt)
        .filter(Receipt.user_id == current_user.id)
    )

    name = request.args.get("name")
    if name:
        query = query.filter(
            db.or_(
                LineItem.item_name.ilike(f"%{name}%"),
                LineItem.normalized_name.ilike(f"%{name}%"),
            )
        )

    category = request.args.get("category")
    if category:
        query = query.filter(LineItem.category == category)

    date_from = request.args.get("date_from")
    if date_from:
        query = query.filter(Receipt.receipt_date >= date.fromisoformat(date_from))

    date_to = request.args.get("date_to")
    if date_to:
        query = query.filter(Receipt.receipt_date <= date.fromisoformat(date_to))

    min_rating = request.args.get("min_rating")
    if min_rating:
        query = query.filter(LineItem.rating >= int(min_rating))

    query = query.order_by(Receipt.receipt_date.desc())
    items = query.limit(200).all()

    results = []
    for item in items:
        d = item.to_dict()
        d["store_name"] = item.receipt.store_name
        d["receipt_date"] = item.receipt.receipt_date.isoformat()
        results.append(d)

    return jsonify(results)
