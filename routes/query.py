from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from query_engine import run_query
from database import db, QueryLog

query_bp = Blueprint("query", __name__)


@query_bp.route("/api/query", methods=["POST"])
@login_required
def ask_query():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    result = run_query(current_user.id, question)
    return jsonify(result)


@query_bp.route("/api/query/history", methods=["GET"])
@login_required
def query_history():
    logs = (
        QueryLog.query
        .filter_by(user_id=current_user.id)
        .order_by(QueryLog.created_at.desc())
        .limit(50)
        .all()
    )
    return jsonify([log.to_dict() for log in logs])
