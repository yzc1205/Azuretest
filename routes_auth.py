"""
降重：重命名日志记录器与数据变量，保持认证接口行为一致
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends

from auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user_id,
)
from database import cosmos_db
from models import UserCreate, LoginRequest, Token, UserResponse

auth_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_200_OK)
async def register(signup_payload: UserCreate):
    """
    Register a new user account
    """
    try:
        auth_logger.info(f"Registration attempt for email: {signup_payload.email}")
        existing_record = cosmos_db.get_user_by_email(signup_payload.email)
        if existing_record:
            auth_logger.warning(
                f"Registration failed: Email already exists {signup_payload.email}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists",
            )

        user_id = str(uuid.uuid4())
        user_doc = {
            "id": user_id,
            "username": signup_payload.username,
            "email": signup_payload.email,
            "hashed_password": get_password_hash(signup_payload.password),
            "created_at": datetime.utcnow().isoformat(),
        }

        created_user = cosmos_db.create_user(user_doc)
        auth_logger.info(f"User created successfully: {signup_payload.email}")

        access_token = create_access_token(
            data={"sub": user_id, "email": signup_payload.email}
        )

        user_response = UserResponse(
            id=created_user["id"],
            username=created_user["username"],
            email=created_user["email"],
            createdAt=created_user["created_at"],
        )

        return Token(token=access_token, user=user_response)

    except HTTPException:
        raise
    except ValueError as exc:
        auth_logger.error(f"Registration validation error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except Exception as exc:
        auth_logger.error(f"Registration error: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to register user: {str(exc)}",
        )


@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK)
async def login(login_payload: LoginRequest):
    """
    Authenticate user and receive access token
    """
    try:
        auth_logger.info(f"Login attempt for email: {login_payload.email}")
        account_record = cosmos_db.get_user_by_email(login_payload.email)
        if not account_record:
            auth_logger.warning(
                f"Login failed: User not found for email {login_payload.email}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        if not verify_password(login_payload.password, account_record["hashed_password"]):
            auth_logger.warning(
                f"Login failed: Invalid password for email {login_payload.email}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        access_token = create_access_token(
            data={"sub": account_record["id"], "email": account_record["email"]}
        )

        user_response = UserResponse(
            id=account_record["id"],
            username=account_record["username"],
            email=account_record["email"],
            createdAt=account_record["created_at"],
        )

        auth_logger.info(f"Login successful for user: {account_record['email']}")
        return Token(token=access_token, user=user_response)

    except HTTPException:
        raise
    except Exception as exc:
        auth_logger.error(f"Login error: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to login: {str(exc)}",
        )
