from datetime import date, timedelta
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from database import db, Receipt, LineItem

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/api/analytics/summary", methods=["GET"])
@login_required
def spending_summary():
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    query = Receipt.query.filter_by(user_id=current_user.id)
    if date_from:
        query = query.filter(Receipt.receipt_date >= date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Receipt.receipt_date <= date.fromisoformat(date_to))

    # By store category
    by_category = (
        db.session.query(
            Receipt.store_category,
            func.sum(Receipt.total),
            func.count(Receipt.id),
        )
        .filter(Receipt.user_id == current_user.id)
        .group_by(Receipt.store_category)
    )
    if date_from:
        by_category = by_category.filter(Receipt.receipt_date >= date.fromisoformat(date_from))
    if date_to:
        by_category = by_category.filter(Receipt.receipt_date <= date.fromisoformat(date_to))

    categories = [
        {"category": cat or "Unknown", "total": round(total or 0, 2), "count": count}
        for cat, total, count in by_category.all()
    ]

    # Monthly spending
    receipts = query.all()
    monthly = {}
    for r in receipts:
        key = r.receipt_date.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + (r.total or 0)

    monthly_list = [
        {"month": k, "total": round(v, 2)}
        for k, v in sorted(monthly.items())
    ]

    grand_total = sum(c["total"] for c in categories)
    receipt_count = sum(c["count"] for c in categories)

    return jsonify({
        "grand_total": round(grand_total, 2),
        "receipt_count": receipt_count,
        "by_category": categories,
        "monthly": monthly_list,
    })


@analytics_bp.route("/api/analytics/price-history", methods=["GET"])
@login_required
def price_history():
    item_name = request.args.get("item")
    if not item_name:
        return jsonify({"error": "item parameter required"}), 400

    results = (
        db.session.query(
            Receipt.receipt_date,
            LineItem.unit_price,
            LineItem.quantity,
            LineItem.unit,
            Receipt.store_name,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .filter(
            Receipt.user_id == current_user.id,
            LineItem.normalized_name.ilike(f"%{item_name}%"),
            LineItem.unit_price.isnot(None),
        )
        .order_by(Receipt.receipt_date)
        .all()
    )

    data_points = [
        {
            "date": r.receipt_date.isoformat(),
            "unit_price": r.unit_price,
            "quantity": r.quantity,
            "unit": r.unit,
            "store": r.store_name,
        }
        for r in results
    ]

    return jsonify({"item": item_name, "data": data_points})


@analytics_bp.route("/api/analytics/top-items", methods=["GET"])
@login_required
def top_items():
    limit = request.args.get("limit", 20, type=int)

    results = (
        db.session.query(
            func.coalesce(LineItem.normalized_name, LineItem.item_name).label("name"),
            func.count(LineItem.id).label("purchase_count"),
            func.sum(LineItem.line_total).label("total_spent"),
            func.avg(LineItem.unit_price).label("avg_price"),
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .filter(Receipt.user_id == current_user.id)
        .group_by(func.coalesce(LineItem.normalized_name, LineItem.item_name))
        .order_by(func.count(LineItem.id).desc())
        .limit(limit)
        .all()
    )

    return jsonify([
        {
            "name": r.name,
            "purchase_count": r.purchase_count,
            "total_spent": round(r.total_spent or 0, 2),
            "avg_price": round(r.avg_price or 0, 2),
        }
        for r in results
    ])


@analytics_bp.route("/api/analytics/top-stores", methods=["GET"])
@login_required
def top_stores():
    limit = request.args.get("limit", 20, type=int)

    results = (
        db.session.query(
            Receipt.store_name,
            func.count(Receipt.id).label("visit_count"),
            func.sum(Receipt.total).label("total_spent"),
        )
        .filter(Receipt.user_id == current_user.id, Receipt.store_name.isnot(None))
        .group_by(Receipt.store_name)
        .order_by(func.count(Receipt.id).desc())
        .limit(limit)
        .all()
    )

    return jsonify([
        {
            "store_name": r.store_name,
            "visit_count": r.visit_count,
            "total_spent": round(r.total_spent or 0, 2),
        }
        for r in results
    ])
