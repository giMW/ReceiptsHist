import os
from datetime import date, datetime, timedelta
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv

load_dotenv()

from database import db, User

# Rate limiting storage (in production, use Redis)
login_attempts = defaultdict(list)  # IP -> list of timestamps
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW = 60  # seconds


def is_rate_limited(ip):
    """Check if an IP is rate limited."""
    now = datetime.now()
    cutoff = now - timedelta(seconds=RATE_LIMIT_WINDOW)
    # Clean old attempts
    login_attempts[ip] = [t for t in login_attempts[ip] if t > cutoff]
    return len(login_attempts[ip]) >= RATE_LIMIT_ATTEMPTS


def record_login_attempt(ip):
    """Record a login attempt for rate limiting."""
    login_attempts[ip].append(datetime.now())


def clear_login_attempts(ip):
    """Clear login attempts after successful login."""
    login_attempts[ip] = []

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///receipts.db")
# Fix Render postgres:// -> postgresql://
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace(
        "postgres://", "postgresql://", 1
    )
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["REMEMBER_COOKIE_SECURE"] = False  # Set True in production with HTTPS
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# --- Auth Routes ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        ip = request.remote_addr

        # Check rate limiting
        if is_rate_limited(ip):
            flash("Too many login attempts. Please wait a minute and try again.", "error")
            return render_template("login.html", google_enabled=google_oauth_enabled)

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "1"

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            clear_login_attempts(ip)
            login_user(user, remember=remember)
            flash("Welcome back!", "success")
            return redirect(url_for("index"))

        record_login_attempt(ip)
        remaining = RATE_LIMIT_ATTEMPTS - len(login_attempts[ip])
        if remaining > 0:
            flash(f"Invalid email or password. {remaining} attempts remaining.", "error")
        else:
            flash("Too many login attempts. Please wait a minute and try again.", "error")

    return render_template("login.html", google_enabled=google_oauth_enabled)


@app.route("/signup", methods=["POST"])
def signup():
    ip = request.remote_addr

    # Rate limit signup too
    if is_rate_limited(ip):
        flash("Too many attempts. Please wait a minute and try again.", "error")
        return redirect(url_for("login"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("login"))

    # Validate password strength
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("login"))

    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*(),.?\":{}|<>" for c in password)

    strength_score = sum([has_upper, has_lower, has_digit, has_special])
    if strength_score < 3:
        flash("Password needs uppercase, lowercase, number, and special character.", "error")
        return redirect(url_for("login"))

    if User.query.filter_by(email=email).first():
        record_login_attempt(ip)
        flash("Email already registered.", "error")
        return redirect(url_for("login"))

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    clear_login_attempts(ip)
    login_user(user, remember=True)
    flash("Account created successfully! Welcome to ReceiptsHist.", "success")
    return redirect(url_for("index"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# --- Main Page ---

@app.route("/")
@login_required
def index():
    return render_template("index.html")


# --- Category Endpoints ---

@app.route("/api/categories/store")
@login_required
def store_categories():
    return jsonify([
        "Grocery", "Restaurant", "Gas Station", "Retail", "Online", "Service", "Other"
    ])


@app.route("/api/categories/item")
@login_required
def item_categories():
    return jsonify([
        "Dairy", "Produce", "Meat", "Bakery", "Beverages", "Snacks", "Frozen",
        "Household", "Fuel", "Entree", "Appetizer", "Dessert", "Drink", "Side",
        "Clothing", "Electronics", "Other"
    ])


# --- Register Blueprints ---

from routes.receipts import receipts_bp
from routes.items import items_bp
from routes.scanner import scanner_bp
from routes.analytics import analytics_bp
from routes.query import query_bp
from routes.export import export_bp
from routes.oauth import oauth_bp, init_oauth

app.register_blueprint(receipts_bp)
app.register_blueprint(items_bp)
app.register_blueprint(scanner_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(query_bp)
app.register_blueprint(export_bp)
app.register_blueprint(oauth_bp)

# Initialize Google OAuth (if configured)
google_oauth_enabled = init_oauth(app)


# --- Init DB ---

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    # host="0.0.0.0" allows access from other devices on the network
    app.run(debug=True, host="0.0.0.0", port=5000)
