from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class ApiUser(BaseModel):
    id: str = Field(..., description="User id (UUID as string)")
    email: EmailStr = Field(..., description="User email address")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="Email address")
    password: str = Field(..., min_length=6, description="Password (min 6 chars)")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="Email address")
    password: str = Field(..., description="Password")


class AuthResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type (bearer)")


class NoteOut(BaseModel):
    id: str = Field(..., description="Note id (UUID as string)")
    title: str = Field(..., description="Note title")
    content: str = Field(..., description="Note content")
    tags: Optional[List[str]] = Field(default=None, description="Tag names assigned to this note")
    created_at: Optional[str] = Field(default=None, description="ISO timestamp")
    updated_at: Optional[str] = Field(default=None, description="ISO timestamp")


class NoteCreate(BaseModel):
    title: str = Field(..., description="Note title")
    content: str = Field(..., description="Note content")
    tags: Optional[List[str]] = Field(default=None, description="Optional tag names to assign")


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(default=None, description="Updated title")
    content: Optional[str] = Field(default=None, description="Updated content")
    tags: Optional[List[str]] = Field(default=None, description="Replace tags with this list")
