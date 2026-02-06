import os
import re
import json
from datetime import date
from openai import OpenAI
from sqlalchemy import text
from database import db, QueryLog

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

QUERY_PROMPT = """You are a SQL query generator for a receipt tracking application.
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
   - DO NOT use PostgreSQL functions like DATE_TRUNC, INTERVAL, or CURRENT_DATE
6. LIMIT results to 500 rows max.
7. Return ONLY the SQL query, no explanation, no markdown fences.

Example date queries:
- Last 30 days: receipt_date >= DATE('now', '-30 days')
- Last month: receipt_date >= DATE('now', 'start of month', '-1 month') AND receipt_date < DATE('now', 'start of month')
- This year: receipt_date >= DATE('now', 'start of year')
- Last year: strftime('%Y', receipt_date) = strftime('%Y', DATE('now', '-1 year'))

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

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": QUERY_PROMPT.format(
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
        log = QueryLog(
            user_id=user_id,
            question=question,
            generated_sql=generated_sql,
            result_summary=f"ERROR: {str(e)}",
        )
        db.session.add(log)
        db.session.commit()
        return {"error": str(e), "sql": generated_sql}
