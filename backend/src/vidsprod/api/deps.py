"""Request dependencies: the container, authentication and the acting person."""

from __future__ import annotations

import hmac
import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from vidsprod.container import Container

_ACTOR = re.compile(r"^[\w .@'-]{1,80}$")


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def require_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer API_TOKEN on every /api route. The dashboard's server-side proxy adds it, so
    the token never reaches the browser. Compared in constant time."""
    expected = request.app.state.container.settings.api_token
    if expected is None:
        return  # development without a token; production refuses to start without one
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})


def get_actor(x_actor: Annotated[str | None, Header()] = None) -> str:
    """Who is acting. Required for every change so the audit trail names a person.

    The name is declared by the operator in the dashboard; there is no identity provider
    yet (see SECURITY.md), so this attributes actions but does not authenticate people.
    """
    if not x_actor or not _ACTOR.match(x_actor.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "an X-Actor header naming the person acting is required")
    return x_actor.strip()


ActorDep = Annotated[str, Depends(get_actor)]
