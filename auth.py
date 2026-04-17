"""
GitHub OAuth Authentication for Streamlit
------------------------------------------
Flow:
1. User clicks "Login with GitHub"
2. Redirect to GitHub OAuth authorize URL
3. GitHub redirects back with ?code=...
4. Exchange code for access token
5. Fetch GitHub username
6. Check against whitelist in st.secrets
7. Grant or deny access

Based on the pattern used in eod-financials (Nico2702/eod-financials).
"""

import streamlit as st
import requests
import urllib.parse


# ── Config from st.secrets ────────────────────────────────────────────────────

def _cfg():
    return st.secrets.get("github_oauth", {})

def _client_id(): return _cfg().get("client_id", "")
def _client_secret(): return _cfg().get("client_secret", "")
def _redirect_uri(): return _cfg().get("redirect_uri", "")

def _whitelist():
    raw = _cfg().get("allowed_users", [])
    if isinstance(raw, str):
        return [u.strip().lower() for u in raw.split(",") if u.strip()]
    return [u.strip().lower() for u in raw]


# ── GitHub OAuth URLs ─────────────────────────────────────────────────────────

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _auth_url() -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": "read:user",
        "allow_signup": "false",  # only existing GitHub accounts
    }
    return f"{GITHUB_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str):
    """Exchange OAuth code for access token. Returns token or None."""
    resp = requests.post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "code": code,
            "redirect_uri": _redirect_uri(),
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    if resp.ok:
        return resp.json().get("access_token")
    return None


def _get_github_user(token: str):
    """Fetch GitHub user info. Returns dict with login, name, avatar_url."""
    resp = requests.get(
        GITHUB_USER_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10,
    )
    if resp.ok:
        return resp.json()
    return None


def _is_allowed(username: str) -> bool:
    wl = _whitelist()
    if not wl:
        return False  # empty whitelist = nobody allowed
    return username.lower() in wl


# ── Session state helpers ─────────────────────────────────────────────────────

def _set_user(user: dict):
    st.session_state["gh_user"] = user
    st.session_state["gh_auth_ok"] = True


def _clear_user():
    st.session_state.pop("gh_user", None)
    st.session_state.pop("gh_auth_ok", None)


def is_authenticated() -> bool:
    return st.session_state.get("gh_auth_ok", False)


def current_user() -> dict:
    return st.session_state.get("gh_user", {})


# ── Main gate — call this at the top of your app ──────────────────────────────

def require_login():
    """
    Call at the very top of app.py.
    If not authenticated, shows login page and stops execution (st.stop()).
    If authenticated, returns silently and the app continues.
    """
    # ── Handle OAuth callback code in query params ────────────────────────
    params = st.query_params

    # Handle error param from GitHub (e.g. access_denied)
    if "error" in params:
        err = params.get("error", "")
        err_desc = params.get("error_description", "")
        st.query_params.clear()
        _show_error(f"GitHub error: {err} — {err_desc}")
        st.stop()

    if "code" in params and not is_authenticated():
        code = params["code"]
        # Clear the code from URL immediately
        st.query_params.clear()

        with st.spinner("Authenticating…"):
            token = _exchange_code(code)
            if token:
                user = _get_github_user(token)
                if user:
                    username = user.get("login", "")
                    if _is_allowed(username):
                        _set_user(user)
                        st.rerun()
                    else:
                        _show_denied(username)
                        st.stop()
                else:
                    _show_error("Could not retrieve GitHub user data.")
                    st.stop()
            else:
                _show_error(
                    "OAuth token exchange failed.\n\n"
                    "Possible causes:\n"
                    "- Client ID / Secret falsch\n"
                    "- Redirect URI in GitHub OAuth App does not match\n"
                    f"- Erwartet: `{_redirect_uri()}`"
                )
                st.stop()

    # ── Already authenticated ─────────────────────────────────────────────
    if is_authenticated():
        _render_user_badge()
        return  # ← app continues

    # ── Not authenticated → show login page ──────────────────────────────
    _render_login_page()
    st.stop()


# ── UI Components ─────────────────────────────────────────────────────────────

def _render_login_page():
    st.markdown("""
        <style>
            .login-container {
                max-width: 420px;
                margin: 80px auto 0 auto;
                padding: 40px 36px;
                background: #1a1f2e;
                border: 1px solid #2d3748;
                border-radius: 16px;
                text-align: center;
            }
            .login-title {
                font-size: 26px;
                font-weight: 700;
                color: #e2e8f0;
                margin-bottom: 6px;
            }
            .login-sub {
                font-size: 14px;
                color: #64748b;
                margin-bottom: 32px;
            }
            .login-btn {
                display: inline-flex;
                align-items: center;
                gap: 10px;
                background: #24292e;
                color: #ffffff !important;
                text-decoration: none !important;
                padding: 12px 24px;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
                border: 1px solid #444d56;
                transition: background 0.2s;
            }
            .login-btn:hover { background: #2f363d; }
            .login-note {
                margin-top: 24px;
                font-size: 12px;
                color: #475569;
            }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(f"""
        <div class="login-container">
            <div class="login-title">NaroIX Sanctions Screener</div>
            <div class="login-sub">Please sign in with your GitHub account.</div>
            <a class="login-btn" href="{_auth_url()}">
                <svg width="20" height="20" viewBox="0 0 16 16" fill="white">
                    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                        0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                        -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                        .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                        -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0
                        1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82
                        1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01
                        1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                </svg>
                Sign in with GitHub
            </a>
            <div class="login-note">Only approved accounts have access.</div>
        </div>
    """, unsafe_allow_html=True)


def _render_user_badge():
    """Small badge in the sidebar showing logged-in user with logout button."""
    user = current_user()
    avatar = user.get("avatar_url", "")
    login = user.get("login", "")
    name = user.get("name") or login

    with st.sidebar:
        st.markdown("---")
        cols = st.columns([1, 3])
        with cols[0]:
            if avatar:
                st.markdown(
                    f'<style>img.gh-avatar {{ border-radius: 10px !important; }}</style>'
                    f'<img class="gh-avatar" src="{avatar}" width="48" '
                    f'style="border-radius:10px !important; display:block;">',
                    unsafe_allow_html=True
                )
        with cols[1]:
            st.markdown(
                f'<div style="font-size:13px;color:#e2e8f0;font-weight:600;">{name}</div>'
                f'<div style="font-size:11px;color:#64748b;">@{login}</div>',
                unsafe_allow_html=True
            )
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("Sign out", key="gh_logout", use_container_width=True):
            _clear_user()
            st.rerun()
        st.markdown("---")


def _show_denied(username: str):
    st.error(
        f"**Access denied** — GitHub account `@{username}` is not approved.\n\n"
        "Please contact the administrator."
    )


def _show_error(msg: str):
    st.error(f"**Authentication error:** {msg}")
