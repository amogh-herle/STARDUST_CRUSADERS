"""
Router: /api/v1/auth

Handles user registration and authentication against the local PostgreSQL database.
"""

import hashlib
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from models import User
from schemas import UserCreate, UserLogin, UserOut

router = APIRouter(prefix="/auth", tags=["Authentication"])


def hash_password(password: str) -> str:
    """Simple SHA-256 password hashing."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new investigator."""
    username_cleaned = payload.username.strip().lower()
    
    # Check if username already exists
    stmt = select(User).where(User.username == username_cleaned)
    existing_user = (await db.execute(stmt)).scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    new_user = User(
        username=username_cleaned,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role="investigator"
    )
    db.add(new_user)
    await db.flush()
    await db.commit()
    return new_user


@router.post("/login", response_model=UserOut)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate an investigator."""
    username_cleaned = payload.username.strip().lower()
    hashed_pwd = hash_password(payload.password)
    
    stmt = select(User).where(User.username == username_cleaned)
    user = (await db.execute(stmt)).scalars().first()
    
    if not user or user.password_hash != hashed_pwd:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
        
    return user
