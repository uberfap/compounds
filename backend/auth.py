"""Optional Discord OAuth and server-side browser sessions."""

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as UpstreamRequest, urlopen
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from backend import database

router = APIRouter(prefix="/api/auth")
SESSION = "compounds_session"
FLOW = "compounds_discord_flow"
CALLBACK = "/api/auth/discord/callback"
SESSION_SECONDS = 30 * 24 * 60 * 60
FLOW_SECONDS = 600


@dataclass(frozen=True)
class DiscordConfig:
    client_id: str
    secret: str
    redirect: str

    @property
    def secure(self):
        return urlsplit(self.redirect).scheme == "https"


def config():
    client = os.getenv("COMPOUNDS_DISCORD_CLIENT_ID")
    secret = os.getenv("COMPOUNDS_DISCORD_CLIENT_SECRET")
    redirect = os.getenv("COMPOUNDS_DISCORD_REDIRECT_URI")
    if not all((client, secret, redirect)):
        return None
    try:
        uri = urlsplit(redirect)
        hostname = uri.hostname
    except ValueError:
        return None
    local_http = uri.scheme == "http" and hostname in {"127.0.0.1", "localhost", "::1"}
    if (not hostname or uri.path != CALLBACK or uri.query or uri.fragment
            or uri.username is not None or (uri.scheme != "https" and not local_http)):
        return None
    return DiscordConfig(client, secret, redirect)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_account(connection, request):
    token = request.cookies.get(SESSION)
    if not token:
        return None
    return connection.execute("""
        SELECT accounts.player_id, accounts.display_name
        FROM discord_sessions AS sessions
        JOIN discord_accounts AS accounts USING (player_id)
        WHERE token_hash = %s AND expires_at > now()
    """, (token_hash(token),)).fetchone()


def account_player(connection, player_id):
    return connection.execute(
        "SELECT 1 FROM discord_accounts WHERE player_id = %s", (player_id,)
    ).fetchone() is not None


def linked_guest(connection, player_id):
    return connection.execute(
        "SELECT 1 FROM linked_guests WHERE guest_id = %s", (player_id,)
    ).fetchone() is not None


def lock_guest(connection, guest_id):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(guest_id),))


def effective_player(connection, request, guest_id, *, write=False):
    account = session_account(connection, request)
    if account:
        if write:
            connection.execute("SELECT 1 FROM discord_accounts WHERE player_id = %s FOR UPDATE", (account[0],))
        return account[0]
    if guest_id is None:
        return None
    if write:
        lock_guest(connection, guest_id)
    if account_player(connection, guest_id):
        raise HTTPException(404, "Player not found")
    if linked_guest(connection, guest_id):
        raise HTTPException(409, "Guest progress was already linked")
    return guest_id


def link_guest(connection, guest_id, player_id):
    lock_guest(connection, guest_id)
    if account_player(connection, guest_id):
        return
    claimed = connection.execute("""
        INSERT INTO linked_guests (guest_id, player_id)
        VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING guest_id
    """, (guest_id, player_id)).fetchone()
    if claimed:
        connection.execute("""
            UPDATE attempts AS guest SET player_id = %s
            WHERE guest.player_id = %s AND NOT EXISTS (
                SELECT 1 FROM attempts AS account
                WHERE account.player_id = %s AND
                    (account.number, account.day, account.version) =
                    (guest.number, guest.day, guest.version)
            )
        """, (player_id, guest_id, player_id))


def exchange(settings, code, verifier):
    form = urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": settings.redirect,
        "client_id": settings.client_id, "client_secret": settings.secret, "code_verifier": verifier,
    }).encode()
    with urlopen(UpstreamRequest("https://discord.com/api/v10/oauth2/token", data=form), timeout=8) as response:
        access_token = json.load(response)["access_token"]
    request = UpstreamRequest("https://discord.com/api/v10/users/@me", headers={
        "Authorization": f"Bearer {access_token}",
    })
    with urlopen(request, timeout=8) as response:
        user = json.load(response)
    return user["id"], user.get("global_name") or user["username"]


@router.get("/session")
def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    account = None
    if request.cookies.get(SESSION):
        with database.connect() as connection:
            account = session_account(connection, request)
    return {
        "enabled": config() is not None,
        "account": {"name": account[1], "storage_key": token_hash(str(account[0]))} if account else None,
    }


@router.post("/discord")
def start(request: Request, response: Response, guest_id: UUID | None = Body(embed=True)):
    settings = config()
    if settings is None:
        raise HTTPException(404, "Discord sign-in is unavailable")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with database.connect() as connection:
        if session_account(connection, request):
            raise HTTPException(409, "Sign out before switching accounts")
        connection.execute("DELETE FROM discord_flows WHERE expires_at <= now()")
        connection.execute("""
            INSERT INTO discord_flows (token_hash, verifier, guest_id, expires_at)
            VALUES (%s, %s, %s, now() + make_interval(secs => %s))
        """, (token_hash(state), verifier, guest_id, FLOW_SECONDS))
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(FLOW, state, max_age=FLOW_SECONDS, path=CALLBACK,
                        httponly=True, secure=settings.secure, samesite="lax")
    return {"url": "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": settings.client_id, "redirect_uri": settings.redirect,
        "response_type": "code", "scope": "identify", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })}


@router.get("/discord/callback")
def callback(request: Request, state: str = "", code: str = "", iss: str = ""):
    settings = config()
    response = RedirectResponse("/?auth=failed", status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })
    response.delete_cookie(FLOW, path=CALLBACK, secure=bool(settings and settings.secure), httponly=True)
    cookie = request.cookies.get(FLOW, "")
    if not settings or not state or not secrets.compare_digest(state.encode(), cookie.encode()):
        return response
    with database.connect() as connection:
        flow = connection.execute("""
            DELETE FROM discord_flows WHERE token_hash = %s AND expires_at > now()
            RETURNING verifier, guest_id
        """, (token_hash(state),)).fetchone()
    if not flow or not code or iss != "https://discord.com":
        return response
    verifier, guest_id = flow
    try:
        discord_id, name = exchange(settings, code, verifier)
    except (OSError, ValueError, KeyError, TypeError):
        return response
    token = secrets.token_urlsafe(32)
    with database.connect() as connection:
        player_id = connection.execute("""
            INSERT INTO discord_accounts (player_id, discord_id, display_name)
            VALUES (%s, %s, %s) ON CONFLICT (discord_id)
            DO UPDATE SET display_name = EXCLUDED.display_name RETURNING player_id
        """, (uuid4(), discord_id, name)).fetchone()[0]
        if guest_id:
            link_guest(connection, guest_id, player_id)
        previous = request.cookies.get(SESSION)
        if previous:
            connection.execute("DELETE FROM discord_sessions WHERE token_hash = %s", (token_hash(previous),))
        connection.execute("DELETE FROM discord_sessions WHERE expires_at <= now()")
        connection.execute("""
            INSERT INTO discord_sessions (token_hash, player_id, expires_at)
            VALUES (%s, %s, now() + make_interval(secs => %s))
        """, (token_hash(token), player_id, SESSION_SECONDS))
    response.headers["Location"] = "/?auth=success"
    response.set_cookie(SESSION, token, max_age=SESSION_SECONDS,
                        httponly=True, secure=settings.secure, samesite="lax")
    return response


@router.post("/logout", status_code=204)
def logout(request: Request, confirm: bool = Body(embed=True)):
    if not confirm:
        raise HTTPException(400, "Sign-out confirmation is required")
    token = request.cookies.get(SESSION)
    if token:
        with database.connect() as connection:
            connection.execute("DELETE FROM discord_sessions WHERE token_hash = %s", (token_hash(token),))
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    settings = config()
    response.delete_cookie(SESSION, secure=bool(settings and settings.secure), httponly=True)
    return response
