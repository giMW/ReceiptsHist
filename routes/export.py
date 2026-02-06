import csv
import io
from flask import Blueprint, request, Response
from flask_login import login_required, current_user
from database import Receipt

export_bp = Blueprint("export", __name__)


@export_bp.route("/api/export/csv", methods=["GET"])
@login_required
def export_csv():
    receipts = (
        Receipt.query
        .filter_by(user_id=current_user.id)
        .order_by(Receipt.receipt_date.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Receipt Date", "Store Name", "Store Category", "Store Address",
        "Subtotal", "Tax", "Tip", "Total", "Payment Method", "Currency",
        "Item Name", "Normalized Name", "Item Category", "Quantity",
        "Unit", "Unit Price", "Line Total", "Rating", "Item Notes",
    ])

    for receipt in receipts:
        items = list(receipt.line_items)
        if items:
            for item in items:
                writer.writerow([
                    receipt.receipt_date.isoformat() if receipt.receipt_date else "",
                    receipt.store_name or "",
                    receipt.store_category or "",
                    receipt.store_address or "",
                    receipt.subtotal or "",
                    receipt.tax or "",
                    receipt.tip or "",
                    receipt.total or "",
                    receipt.payment_method or "",
                    receipt.currency or "",
                    item.item_name or "",
                    item.normalized_name or "",
                    item.category or "",
                    item.quantity or "",
                    item.unit or "",
                    item.unit_price or "",
                    item.line_total or "",
                    item.rating or "",
                    item.notes or "",
                ])
        else:
            writer.writerow([
                receipt.receipt_date.isoformat() if receipt.receipt_date else "",
                receipt.store_name or "",
                receipt.store_category or "",
                receipt.store_address or "",
                receipt.subtotal or "",
                receipt.tax or "",
                receipt.tip or "",
                receipt.total or "",
                receipt.payment_method or "",
                receipt.currency or "",
                "", "", "", "", "", "", "", "", "",
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=receipts_export.csv"},
    )
