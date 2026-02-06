from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    receipts = db.relationship("Receipt", backref="user", lazy="dynamic")
    normalized_items = db.relationship("NormalizedItem", backref="user", lazy="dynamic")
    query_logs = db.relationship("QueryLog", backref="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Receipt(db.Model):
    __tablename__ = "receipts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    store_name = db.Column(db.String(255))
    store_address = db.Column(db.String(500))
    store_category = db.Column(db.String(50))
    receipt_date = db.Column(db.Date, nullable=False)
    subtotal = db.Column(db.Float)
    tax = db.Column(db.Float)
    tip = db.Column(db.Float)
    total = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    currency = db.Column(db.String(10), default="USD")
    photo_filename = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    line_items = db.relationship(
        "LineItem", backref="receipt", lazy="dynamic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.Index("ix_receipts_user_date", "user_id", "receipt_date"),
        db.Index("ix_receipts_user_category", "user_id", "store_category"),
    )

    def to_dict(self, include_items=False):
        d = {
            "id": self.id,
            "store_name": self.store_name,
            "store_address": self.store_address,
            "store_category": self.store_category,
            "receipt_date": self.receipt_date.isoformat() if self.receipt_date else None,
            "subtotal": self.subtotal,
            "tax": self.tax,
            "tip": self.tip,
            "total": self.total,
            "payment_method": self.payment_method,
            "currency": self.currency,
            "photo_filename": self.photo_filename,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_items:
            d["items"] = [item.to_dict() for item in self.line_items]
        return d


class LineItem(db.Model):
    __tablename__ = "line_items"

    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(
        db.Integer, db.ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False
    )
    item_name = db.Column(db.String(255), nullable=False)
    normalized_name = db.Column(db.String(255))
    category = db.Column(db.String(50))
    quantity = db.Column(db.Float, default=1)
    unit = db.Column(db.String(20), default="each")
    unit_price = db.Column(db.Float)
    line_total = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text)
    rating = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.Index("ix_line_items_receipt", "receipt_id"),
        db.Index("ix_line_items_normalized", "normalized_name"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "receipt_id": self.receipt_id,
            "item_name": self.item_name,
            "normalized_name": self.normalized_name,
            "category": self.category,
            "quantity": self.quantity,
            "unit": self.unit,
            "unit_price": self.unit_price,
            "line_total": self.line_total,
            "notes": self.notes,
            "rating": self.rating,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NormalizedItem(db.Model):
    __tablename__ = "normalized_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50))
    default_unit = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_normalized_user_name"),
    )


class QueryLog(db.Model):
    __tablename__ = "query_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    question = db.Column(db.Text, nullable=False)
    generated_sql = db.Column(db.Text)
    result_summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "generated_sql": self.generated_sql,
            "result_summary": self.result_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
