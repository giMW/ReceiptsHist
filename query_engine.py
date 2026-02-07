import os
import re
import json
from datetime import date
from openai import OpenAI
from sqlalchemy import text
from flask import current_app
from database import db, QueryLog


def _is_postgresql():
    """Check if we're using PostgreSQL."""
    db_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    return "postgresql" in db_url.lower() or "postgres" in db_url.lower()

SCHEMA_DESCRIPTION = """
Database tables:

1. receipts:
   - id (INTEGER, primary key)
   - user_id (INTEGER, foreign key to users)
   - store_name (VARCHAR) - name of the store
   - store_address (VARCHAR)
   - store_category (VARCHAR) - one of: Grocery, Restaurant, Gas Station, Retail, Online, Service, Other
   - receipt_date (DATE) - date of the receipt
   - subtotal (FLOAT)
   - tax (FLOAT)
   - tip (FLOAT)
   - total (FLOAT) - total amount
   - payment_method (VARCHAR) - Cash, Credit, Debit, or Other
   - currency (VARCHAR, default USD)
   - notes (TEXT)

2. line_items:
   - id (INTEGER, primary key)
   - receipt_id (INTEGER, foreign key to receipts)
   - item_name (VARCHAR) - raw item name from receipt
   - normalized_name (VARCHAR) - cleaned/normalized item name
   - category (VARCHAR) - one of: Dairy, Produce, Meat, Bakery, Beverages, Snacks, Frozen, Household, Fuel, Entree, Appetizer, Dessert, Drink, Side, Clothing, Electronics, Other
   - quantity (FLOAT, default 1)
   - unit (VARCHAR) - each, lb, oz, gal, kg, L
   - unit_price (FLOAT)
   - line_total (FLOAT)
   - notes (TEXT)
   - rating (INTEGER, 1-5 stars, nullable)

3. normalized_items:
   - id (INTEGER, primary key)
   - user_id (INTEGER, foreign key to users)
   - name (VARCHAR) - canonical item name
   - category (VARCHAR)
   - default_unit (VARCHAR)

Relationships:
- receipts.id -> line_items.receipt_id (one-to-many)
- Use normalized_name in line_items for price comparisons over time
- Always filter by user_id in the receipts table (use :user_id parameter)
- When joining line_items with receipts, join on line_items.receipt_id = receipts.id
"""

QUERY_PROMPT_SQLITE = """You are a SQL query generator for a receipt tracking application.
Given the user's question and the database schema, generate a SELECT SQL query.

{schema}

IMPORTANT: This database uses SQLite. Use SQLite date functions only.

RULES:
1. Generate ONLY a SELECT query - no INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or TRUNCATE.
2. ALWAYS filter receipts by user_id = :user_id (use the :user_id parameter placeholder).
3. When querying line_items, JOIN with receipts and filter receipts.user_id = :user_id.
4. Use normalized_name for item matching when possible (it's the cleaned version of item_name).
5. For date calculations, use SQLite functions:
   - Current date: DATE('now')
   - Subtract days: DATE('now', '-30 days')
   - Subtract months: DATE('now', '-1 month')
   - Start of current month: DATE('now', 'start of month')
   - Start of last month: DATE('now', 'start of month', '-1 month')
   - Extract year: strftime('%Y', receipt_date)
   - Extract month: strftime('%m', receipt_date)
6. LIMIT results to 500 rows max.
7. Return ONLY the SQL query, no explanation, no markdown fences.

Example date queries:
- Last 30 days: receipt_date >= DATE('now', '-30 days')
- Last month: receipt_date >= DATE('now', 'start of month', '-1 month') AND receipt_date < DATE('now', 'start of month')
- This year: receipt_date >= DATE('now', 'start of year')

Today's date: {today}

User question: {question}

SQL query:"""

QUERY_PROMPT_POSTGRES = """You are a SQL query generator for a receipt tracking application.
Given the user's question and the database schema, generate a SELECT SQL query.

{schema}

IMPORTANT: This database uses PostgreSQL. Use PostgreSQL date functions only.

RULES:
1. Generate ONLY a SELECT query - no INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or TRUNCATE.
2. ALWAYS filter receipts by user_id = :user_id (use the :user_id parameter placeholder).
3. When querying line_items, JOIN with receipts and filter receipts.user_id = :user_id.
4. Use normalized_name for item matching when possible (it's the cleaned version of item_name).
5. For date calculations, use PostgreSQL functions:
   - Current date: CURRENT_DATE
   - Subtract days: CURRENT_DATE - INTERVAL '30 days'
   - Subtract months: CURRENT_DATE - INTERVAL '1 month'
   - Start of current month: DATE_TRUNC('month', CURRENT_DATE)
   - Start of last month: DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
   - End of last month: DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 day'
   - Extract year: EXTRACT(YEAR FROM receipt_date)
   - Extract month: EXTRACT(MONTH FROM receipt_date)
6. LIMIT results to 500 rows max.
7. Return ONLY the SQL query, no explanation, no markdown fences.

Example date queries:
- Last 30 days: receipt_date >= CURRENT_DATE - INTERVAL '30 days'
- Last month: receipt_date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') AND receipt_date < DATE_TRUNC('month', CURRENT_DATE)
- This year: receipt_date >= DATE_TRUNC('year', CURRENT_DATE)
- This month: receipt_date >= DATE_TRUNC('month', CURRENT_DATE)

Today's date: {today}

User question: {question}

SQL query:"""

DANGEROUS_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

MAX_ROWS = 500


def _validate_sql(sql):
    """Validate that the SQL is a safe SELECT query."""
    sql_stripped = sql.strip().rstrip(";")

    if not sql_stripped.upper().startswith("SELECT"):
        return False, "Query must be a SELECT statement."

    if DANGEROUS_KEYWORDS.search(sql_stripped):
        return False, "Query contains forbidden keywords."

    return True, None


def run_query(user_id, question):
    """Generate and execute a SQL query from a natural language question."""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # Use the correct prompt for the database type
    prompt_template = QUERY_PROMPT_POSTGRES if _is_postgresql() else QUERY_PROMPT_SQLITE

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": prompt_template.format(
                    schema=SCHEMA_DESCRIPTION,
                    question=question,
                    today=date.today().isoformat(),
                ),
            }
        ],
        max_tokens=1000,
        temperature=0.0,
    )

    generated_sql = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if generated_sql.startswith("```"):
        lines = generated_sql.split("\n")
        generated_sql = "\n".join(
            l for l in lines if not l.strip().startswith("```")
        ).strip()

    valid, error = _validate_sql(generated_sql)
    if not valid:
        log = QueryLog(
            user_id=user_id,
            question=question,
            generated_sql=generated_sql,
            result_summary=f"REJECTED: {error}",
        )
        db.session.add(log)
        db.session.commit()
        return {"error": error, "sql": generated_sql}

    # Ensure LIMIT exists
    if "LIMIT" not in generated_sql.upper():
        generated_sql = generated_sql.rstrip(";") + f" LIMIT {MAX_ROWS}"

    try:
        result = db.session.execute(
            text(generated_sql), {"user_id": user_id}
        )
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

        # Convert non-serializable types and format currency
        money_keywords = ('total', 'spent', 'price', 'cost', 'amount', 'sum', 'subtotal', 'tax', 'tip')
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, (bytes, bytearray)):
                    row[k] = str(v)
                elif v is None and any(kw in k.lower() for kw in money_keywords):
                    # NULL values for money columns become $0.00
                    row[k] = "$0.00"
                elif isinstance(v, (int, float)) and any(kw in k.lower() for kw in money_keywords):
                    row[k] = f"${v:,.2f}"

        # If no rows returned and query looks like a sum/total, return $0.00
        if not rows and any(kw in generated_sql.upper() for kw in ('SUM(', 'TOTAL')):
            rows = [{col: "$0.00" if any(kw in col.lower() for kw in money_keywords) else 0 for col in columns}]

        summary = f"{len(rows)} row(s) returned"

        log = QueryLog(
            user_id=user_id,
            question=question,
            generated_sql=generated_sql,
            result_summary=summary,
        )
        db.session.add(log)
        db.session.commit()

        return {
            "sql": generated_sql,
            "columns": columns,
            "rows": rows,
            "summary": summary,
        }

    except Exception as e:
        # Rollback the failed transaction first
        db.session.rollback()

        error_msg = str(e)
        # Make common database errors more user-friendly
        if "pattern" in error_msg.lower() or "did not match" in error_msg.lower():
            friendly_error = "The query had a date or format error. Try rephrasing (e.g., 'last month' instead of specific dates)."
        elif "no such column" in error_msg.lower() or "does not exist" in error_msg.lower():
            friendly_error = "The query tried to use an invalid column or function. Try a simpler question."
        elif "syntax error" in error_msg.lower():
            friendly_error = "The generated SQL had a syntax error. Try rephrasing your question."
        elif "operator does not exist" in error_msg.lower():
            friendly_error = "Type mismatch in query. Try rephrasing your question."
        else:
            friendly_error = f"Query failed: {error_msg[:100]}"

        try:
            log = QueryLog(
                user_id=user_id,
                question=question,
                generated_sql=generated_sql,
                result_summary=f"ERROR: {error_msg[:200]}",
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()  # Ignore logging errors

        return {"error": friendly_error, "sql": generated_sql}
