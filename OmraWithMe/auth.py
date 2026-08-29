from datetime import datetime, timedelta
from typing import Optional
import os
import re
import secrets
import warnings
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db, User

# JWT secret — must be set via OMRA_SECRET_KEY in production.
# In dev (OMRA_ENV != "production"), persist a generated key to a local cache file
# so it survives server reloads (otherwise every code edit invalidates all tokens).
_ENV = os.environ.get("OMRA_ENV", "development").lower()
_SECRET_FROM_ENV = os.environ.get("OMRA_SECRET_KEY", "").strip()
if _SECRET_FROM_ENV:
    SECRET_KEY = _SECRET_FROM_ENV
elif _ENV == "production":
    raise RuntimeError("OMRA_SECRET_KEY must be set when OMRA_ENV=production")
else:
    _key_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dev_secret")
    if os.path.exists(_key_cache):
        with open(_key_cache, "r", encoding="utf-8") as f:
            SECRET_KEY = f.read().strip()
    if not os.path.exists(_key_cache) or not SECRET_KEY:
        SECRET_KEY = secrets.token_urlsafe(48)
        try:
            with open(_key_cache, "w", encoding="utf-8") as f:
                f.write(SECRET_KEY)
        except OSError:
            warnings.warn("Could not persist .dev_secret; tokens will be invalidated on restart")
        else:
            warnings.warn("OMRA_SECRET_KEY not set — generated dev key cached to .dev_secret (delete to rotate)")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("OMRA_TOKEN_TTL_MINUTES", 60 * 24))  # default 24h
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("OMRA_REFRESH_TTL_DAYS", 30))

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer(auto_error=False)

_PASSWORD_MIN = 8

def validate_password_strength(password: str) -> None:
    if len(password) < _PASSWORD_MIN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {_PASSWORD_MIN} characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Password must contain letters and numbers")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire, "type": "refresh"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_password_reset_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(hours=1)
    payload = {"sub": str(user_id), "exp": expire, "type": "reset"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_email_verification_token(user_id: int) -> str:
    """Typed JWT used in the email-verification link (48h validity)."""
    expire = datetime.utcnow() + timedelta(hours=48)
    payload = {"sub": str(user_id), "exp": expire, "type": "verify_email"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    if not credentials:
        return None
    
    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        return None

    # Only access tokens grant API access — refresh/reset tokens must not work here
    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    user = db.query(User).filter(User.id == int(user_id), User.is_deleted == False).first()
    return user

async def get_current_user_required(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    user = await get_current_user(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    return user
