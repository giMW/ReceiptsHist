import os
from flask import Blueprint, redirect, url_for, flash, session
from flask_login import login_user, current_user
from authlib.integrations.flask_client import OAuth
from database import db, User

oauth_bp = Blueprint("oauth", __name__)

# OAuth will be initialized in init_oauth()
oauth = OAuth()


def init_oauth(app):
    """Initialize OAuth with the Flask app."""
    oauth.init_app(app)

    # Only register Google if credentials are configured
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if client_id and client_secret:
        oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        return True
    return False


@oauth_bp.route("/auth/google")
def google_login():
    """Initiate Google OAuth flow."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if not oauth.google:
        flash("Google login is not configured.", "error")
        return redirect(url_for("login"))

    redirect_uri = url_for("oauth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@oauth_bp.route("/auth/google/callback")
def google_callback():
    """Handle Google OAuth callback."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get("userinfo")

        if not user_info:
            user_info = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo").json()

        email = user_info.get("email", "").lower()

        if not email:
            flash("Could not get email from Google.", "error")
            return redirect(url_for("login"))

        # Find or create user
        user = User.query.filter_by(email=email).first()

        if not user:
            # Create new user with Google OAuth (no password)
            user = User(email=email, google_id=user_info.get("sub"))
            db.session.add(user)
            db.session.commit()
            flash("Account created with Google!", "success")
        else:
            # Link Google ID if not already linked
            if not user.google_id:
                user.google_id = user_info.get("sub")
                db.session.commit()

        login_user(user, remember=True)
        return redirect(url_for("index"))

    except Exception as e:
        flash(f"Google login failed: {str(e)}", "error")
        return redirect(url_for("login"))
