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

    try:
        result = run_query(current_user.id, question)
        return jsonify(result)
    except Exception as e:
        error_msg = str(e)
        # Make common errors more user-friendly
        if "pattern" in error_msg.lower():
            error_msg = "The generated query had a syntax error. Try rephrasing your question."
        elif "no such column" in error_msg.lower():
            error_msg = "Query referenced an invalid column. Try rephrasing your question."
        elif "no such table" in error_msg.lower():
            error_msg = "Query referenced an invalid table. Try rephrasing your question."
        return jsonify({"error": error_msg}), 500


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
