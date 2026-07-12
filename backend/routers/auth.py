"""
Router: /api/v1/auth

Handles user registration and authentication against the local PostgreSQL database.
"""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db
from models import User
from schemas import UserCreate, UserLogin, UserOut

router = APIRouter(prefix="/auth", tags=["Authentication"])

# bcrypt truncates the input at 72 bytes; longer passwords still hash fine,
# just without extra entropy past that point (documented bcrypt limitation).


def hash_password(password: str) -> str:
    """Salted bcrypt password hashing."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


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

    stmt = select(User).where(User.username == username_cleaned)
    user = (await db.execute(stmt)).scalars().first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
        
    return user
