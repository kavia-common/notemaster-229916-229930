from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
import os

from fastapi.middleware.cors import CORSMiddleware

from src.api.models import (
    ApiUser,
    AuthResponse,
    LoginRequest,
    NoteCreate,
    NoteOut,
    NoteUpdate,
    RegisterRequest,
)
from src.auth.deps import get_current_user
from src.auth.security import create_access_token, hash_password, verify_password
from src.db.connection import execute, fetch_all, fetch_one

openapi_tags = [
    {"name": "Health", "description": "Service health checks"},
    {"name": "Auth", "description": "Register/login endpoints"},
    {"name": "Users", "description": "User profile endpoints"},
    {"name": "Notes", "description": "CRUD for notes with optional tag assignment"},
    {"name": "Tags", "description": "Tag listing/management and note↔tag assignments"},
]

app = FastAPI(
    title="NoteMaster Backend API",
    description="FastAPI backend for a notes app with JWT auth and Postgres storage.",
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS wiring:
# - In preview, the frontend runs on a different origin than the backend.
# - Set CORS_ALLOW_ORIGINS to a comma-separated list of allowed origins, e.g.:
#     CORS_ALLOW_ORIGINS="http://localhost:3000,https://<preview-frontend-host>"
# - If unset, we keep permissive "*" to avoid integration friction in smoke checks.
cors_allow_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
allow_origins = (
    [o.strip() for o in cors_allow_origins_env.split(",") if o.strip()]
    if cors_allow_origins_env
    else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Health"], summary="Health check")
def health_check():
    """Return a simple health status payload."""
    return {"message": "Healthy"}


def _note_tags(note_id: str, user_id: str) -> List[str]:
    rows = fetch_all(
        """
        SELECT t.name
        FROM note_tags nt
        JOIN tags t ON t.id = nt.tag_id
        JOIN notes n ON n.id = nt.note_id
        WHERE nt.note_id = %s AND n.user_id = %s
        ORDER BY t.name ASC
        """,
        (note_id, user_id),
    )
    return [r["name"] for r in rows]


def _ensure_tag_ids(user_id: str, tag_names: List[str]) -> List[str]:
    """
    Ensure tags exist for user and return their ids (as strings).
    Tag names are treated case-sensitive (matches DB unique constraint).
    """
    tag_ids: List[str] = []
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        existing = fetch_one(
            "SELECT id::text AS id FROM tags WHERE user_id = %s AND name = %s",
            (user_id, name),
        )
        if existing:
            tag_ids.append(existing["id"])
            continue
        _, row = execute(
            "INSERT INTO tags (user_id, name) VALUES (%s, %s) RETURNING id::text AS id",
            (user_id, name),
            returning=True,
        )
        tag_ids.append(row["id"])
    # De-duplicate while preserving order
    seen = set()
    out: List[str] = []
    for tid in tag_ids:
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def _set_note_tags(note_id: str, user_id: str, tag_names: List[str]) -> None:
    """Replace note tags with provided list (creating missing tags)."""
    # Ensure note belongs to user
    owned = fetch_one("SELECT id::text AS id FROM notes WHERE id = %s AND user_id = %s", (note_id, user_id))
    if not owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    tag_ids = _ensure_tag_ids(user_id, tag_names)

    # Remove existing
    execute(
        """
        DELETE FROM note_tags nt
        USING notes n
        WHERE nt.note_id = n.id AND n.id = %s AND n.user_id = %s
        """,
        (note_id, user_id),
        returning=False,
    )

    # Insert new
    for tid in tag_ids:
        execute(
            "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (note_id, tid),
            returning=False,
        )


@app.post(
    "/auth/register",
    tags=["Auth"],
    summary="Register a new user",
    response_model=ApiUser | Dict[str, ApiUser],
)
def register(req: RegisterRequest):
    """
    Register a user with email + password.

    Returns a user payload. (Frontend accepts either `{user: User}` or `User`.)
    """
    existing = fetch_one("SELECT id::text AS id FROM users WHERE email = %s", (req.email,))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    pw_hash = hash_password(req.password)
    _, row = execute(
        "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id::text AS id, email",
        (req.email, pw_hash),
        returning=True,
    )
    return {"user": row}


@app.post(
    "/auth/login",
    tags=["Auth"],
    summary="Login and receive access token",
    response_model=AuthResponse,
)
def login(req: LoginRequest):
    """Validate credentials and return a JWT access token."""
    user = fetch_one(
        "SELECT id::text AS id, email, password_hash FROM users WHERE email = %s",
        (req.email,),
    )
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(subject=user["id"])
    return {"access_token": token, "token_type": "bearer"}


@app.get(
    "/users/me",
    tags=["Users"],
    summary="Get current user profile",
    response_model=ApiUser,
)
def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Return the authenticated user's basic profile (id, email)."""
    return current_user


@app.get(
    "/notes",
    tags=["Notes"],
    summary="List notes (optionally search and/or filter by tag)",
    response_model=List[NoteOut],
)
def list_notes(
    search: Optional[str] = Query(default=None, description="Search in title/content (ILIKE)"),
    tag: Optional[str] = Query(default=None, description="Filter notes by tag name"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Return notes for the authenticated user."""
    user_id = current_user["id"]
    params: List[Any] = [user_id]
    where = ["n.user_id = %s", "n.is_archived = false"]

    if search:
        where.append("(n.title ILIKE %s OR n.content ILIKE %s)")
        like = f"%{search}%"
        params.extend([like, like])

    if tag:
        where.append(
            """EXISTS (
                SELECT 1
                FROM note_tags nt
                JOIN tags t ON t.id = nt.tag_id
                WHERE nt.note_id = n.id AND t.user_id = %s AND t.name = %s
            )"""
        )
        params.extend([user_id, tag])

    rows = fetch_all(
        f"""
        SELECT n.id::text AS id,
               n.title,
               n.content,
               n.created_at::text AS created_at,
               n.updated_at::text AS updated_at
        FROM notes n
        WHERE {' AND '.join(where)}
        ORDER BY n.updated_at DESC
        """,
        params,
    )

    out: List[NoteOut] = []
    for r in rows:
        r["tags"] = _note_tags(r["id"], user_id)
        out.append(NoteOut(**r))
    return out


@app.post(
    "/notes",
    tags=["Notes"],
    summary="Create a new note",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_note(note: NoteCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Create a note for the authenticated user, optionally assigning tags."""
    user_id = current_user["id"]
    _, row = execute(
        """
        INSERT INTO notes (user_id, title, content)
        VALUES (%s, %s, %s)
        RETURNING id::text AS id, title, content, created_at::text AS created_at, updated_at::text AS updated_at
        """,
        (user_id, note.title, note.content),
        returning=True,
    )

    if note.tags is not None:
        _set_note_tags(row["id"], user_id, note.tags)

    row["tags"] = _note_tags(row["id"], user_id)
    return NoteOut(**row)


@app.patch(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Update a note (title/content/tags)",
    response_model=NoteOut,
)
def update_note(
    note_id: str, patch: NoteUpdate, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Update a note the authenticated user owns."""
    user_id = current_user["id"]

    existing = fetch_one(
        """
        SELECT id::text AS id, title, content, created_at::text AS created_at, updated_at::text AS updated_at
        FROM notes
        WHERE id = %s AND user_id = %s AND is_archived = false
        """,
        (note_id, user_id),
    )
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    title = patch.title if patch.title is not None else existing["title"]
    content = patch.content if patch.content is not None else existing["content"]

    _, row = execute(
        """
        UPDATE notes
        SET title = %s, content = %s
        WHERE id = %s AND user_id = %s
        RETURNING id::text AS id, title, content, created_at::text AS created_at, updated_at::text AS updated_at
        """,
        (title, content, note_id, user_id),
        returning=True,
    )

    if patch.tags is not None:
        _set_note_tags(note_id, user_id, patch.tags)

    row["tags"] = _note_tags(note_id, user_id)
    return NoteOut(**row)


@app.delete(
    "/notes/{note_id}",
    tags=["Notes"],
    summary="Delete a note",
)
def delete_note(note_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Delete a note the authenticated user owns."""
    user_id = current_user["id"]
    count, _ = execute("DELETE FROM notes WHERE id = %s AND user_id = %s", (note_id, user_id), returning=False)
    if count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return {"ok": True}


@app.get(
    "/tags",
    tags=["Tags"],
    summary="List tags for current user",
    response_model=List[str],
)
def list_tags(current_user: Dict[str, Any] = Depends(get_current_user)):
    """List tag names for the authenticated user."""
    user_id = current_user["id"]
    rows = fetch_all("SELECT name FROM tags WHERE user_id = %s ORDER BY name ASC", (user_id,))
    return [r["name"] for r in rows]


@app.post(
    "/tags",
    tags=["Tags"],
    summary="Create a tag",
    response_model=Dict[str, str],
    status_code=status.HTTP_201_CREATED,
)
def create_tag(name: str = Query(..., min_length=1, description="Tag name"), current_user: Dict[str, Any] = Depends(get_current_user)):
    """Create a new tag for the authenticated user."""
    user_id = current_user["id"]
    # Insert idempotently
    existing = fetch_one("SELECT id::text AS id FROM tags WHERE user_id = %s AND name = %s", (user_id, name))
    if existing:
        return {"name": name}

    execute("INSERT INTO tags (user_id, name) VALUES (%s, %s)", (user_id, name), returning=False)
    return {"name": name}


@app.delete(
    "/tags",
    tags=["Tags"],
    summary="Delete a tag (and its note assignments)",
    response_model=Dict[str, bool],
)
def delete_tag(name: str = Query(..., min_length=1, description="Tag name"), current_user: Dict[str, Any] = Depends(get_current_user)):
    """Delete a tag owned by the authenticated user."""
    user_id = current_user["id"]
    count, _ = execute("DELETE FROM tags WHERE user_id = %s AND name = %s", (user_id, name), returning=False)
    if count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    return {"ok": True}
