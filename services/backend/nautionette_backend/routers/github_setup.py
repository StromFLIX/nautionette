from __future__ import annotations

import html
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import github_setup
from ..security import require_user

router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


class Connect(BaseModel):
    public_url: str = Field(default="", max_length=2048)
    organization: str = Field(default="", max_length=39)


def failure_response(error: HTTPException) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        "<title>GitHub connection</title><h1>GitHub connection</h1>"
        f'<p>{html.escape(str(error.detail))}</p><a href="/settings/projects">Return to Projects</a></html>',
        status_code=error.status_code,
        headers=HEADERS | {"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"},
    )


@router.post(github_setup.BASE_PATH + "/connect", dependencies=[Depends(require_user)])
async def connect(payload: Connect):
    return github_setup.begin(payload.public_url, payload.organization)


@router.get(github_setup.BASE_PATH + "/start", include_in_schema=False)
async def start(state: str = Query(max_length=100)):
    try:
        flow = github_setup.start(state)
    except HTTPException as exc:
        return failure_response(exc)
    if "manifest" in flow:
        nonce = secrets.token_urlsafe(24)
        response = HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width">'
            "<title>Connect GitHub</title>"
            f'<form method="post" action="{html.escape(flow["url"], quote=True)}">'
            '<input type="hidden" name="manifest" '
            f'value="{html.escape(json.dumps(flow["manifest"]), quote=True)}">'
            "<noscript><button>Continue to GitHub</button></noscript></form>"
            f'<script nonce="{nonce}">document.forms[0].submit()</script></html>',
            headers=HEADERS
            | {
                "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "form-action https://github.com; frame-ancestors 'none'; base-uri 'none'"
            },
        )
    else:
        response = RedirectResponse(flow["url"], status_code=303, headers=HEADERS)
    response.set_cookie(
        github_setup.COOKIE,
        flow["browser"],
        max_age=github_setup.SETUP_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get(github_setup.BASE_PATH + "/callback", include_in_schema=False)
async def callback(
    request: Request,
    state: str = Query(default="", max_length=100),
    code: str = Query(default="", max_length=200),
):
    try:
        target = await github_setup.convert(state, request.cookies.get(github_setup.COOKIE, ""), code)
        return RedirectResponse(target, status_code=303, headers=HEADERS)
    except HTTPException as exc:
        return failure_response(exc)


@router.get(github_setup.BASE_PATH + "/installed", include_in_schema=False)
async def installed(
    request: Request,
    state: str = Query(default="", max_length=100),
    installation_id: str = Query(default="", max_length=30),
    setup_action: str = "",
):
    if not state:
        return RedirectResponse("/settings/projects", status_code=303, headers=HEADERS)
    try:
        target = await github_setup.installed(
            state, request.cookies.get(github_setup.COOKIE, ""), installation_id, setup_action
        )
        response = RedirectResponse(target, status_code=303, headers=HEADERS)
        response.delete_cookie(github_setup.COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response
    except HTTPException as exc:
        return failure_response(exc)


@router.post(github_setup.BASE_PATH + "/webhook", include_in_schema=False)
async def webhook(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > github_setup.MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "GitHub webhook is too large")
        body.extend(chunk)
    return github_setup.webhook(
        bytes(body),
        request.headers.get("x-hub-signature-256", ""),
        request.headers.get("x-github-event", ""),
        request.headers.get("x-github-delivery", ""),
    )
