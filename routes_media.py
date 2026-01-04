"""
降重：重命名日志记录器与局部变量，保持媒体接口的行为与数据逻辑不变
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form, Query

from auth import get_current_user_id
from database import cosmos_db
from media_helpers import fetch_and_verify_media_ownership, extract_thumbnail_blob_identifier
from models import MediaResponse, MediaUpdate, MediaListResponse
from storage import blob_storage
from utils import validate_file_type, validate_file_size, generate_thumbnail

media_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["Media Management"])


@router.post("", response_model=MediaResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user_id),
):
    """
    Upload a new image or video file
    """
    try:
        asset_type = validate_file_type(file)
        payload_size = validate_file_size(file)

        tag_bundle = None
        if tags:
            try:
                tag_bundle = json.loads(tags)
                if not isinstance(tag_bundle, list):
                    raise ValueError("Tags must be an array")
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid tags format. Must be a JSON array.",
                )

        file_content = await file.read()
        await file.seek(0)

        blob_name, blob_url = blob_storage.upload_file(
            file.file, user_id, file.filename, file.content_type
        )

        thumbnail_link = None
        if asset_type == "image":
            thumbnail_data = generate_thumbnail(file_content)
            if thumbnail_data:
                try:
                    import io

                    thumbnail_file = io.BytesIO(thumbnail_data)
                    _, thumbnail_link = blob_storage.upload_file(
                        thumbnail_file,
                        user_id,
                        f"thumb_{file.filename}",
                        "image/jpeg",
                    )
                except Exception as exc:
                    media_logger.warning(f"Failed to upload thumbnail: {exc}")

        media_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        media_doc = {
            "id": media_id,
            "userId": user_id,
            "fileName": blob_name,
            "originalFileName": file.filename,
            "mediaType": asset_type,
            "fileSize": payload_size,
            "mimeType": file.content_type,
            "blobUrl": blob_url,
            "thumbnailUrl": thumbnail_link,
            "description": description,
            "tags": tag_bundle,
            "uploadedAt": now,
            "updatedAt": now,
        }

        created_media = cosmos_db.create_media(media_doc)

        return MediaResponse(**created_media)

    except HTTPException:
        raise
    except Exception as exc:
        media_logger.error(f"Upload error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload media: {str(exc)}",
        )


@router.get("/search", response_model=MediaListResponse, status_code=status.HTTP_200_OK)
async def search_media(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
):
    """
    Search media files by filename, description, or tags
    """
    try:
        items, total = cosmos_db.search_media(
            user_id=user_id, query=query, page=page, page_size=pageSize
        )

        media_payloads = [MediaResponse(**item) for item in items]

        return MediaListResponse(
            items=media_payloads, total=total, page=page, pageSize=pageSize
        )

    except Exception as exc:
        media_logger.error(f"Search media error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search media",
        )


@router.get("", response_model=MediaListResponse, status_code=status.HTTP_200_OK)
async def get_media_list(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    mediaType: Optional[str] = Query(None, regex="^(image|video)$"),
    user_id: str = Depends(get_current_user_id),
):
    """
    Retrieve paginated list of user's media files
    """
    try:
        items, total = cosmos_db.get_user_media(
            user_id=user_id, page=page, page_size=pageSize, media_type=mediaType
        )

        media_payloads = [MediaResponse(**item) for item in items]

        return MediaListResponse(
            items=media_payloads, total=total, page=page, pageSize=pageSize
        )

    except Exception as exc:
        media_logger.error(f"Get media list error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve media list",
        )


@router.get("/{media_id}", response_model=MediaResponse, status_code=status.HTTP_200_OK)
async def get_media_by_id(
    media_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Retrieve details of a specific media file
    """
    try:
        media_record = fetch_and_verify_media_ownership(media_id, user_id)
        return MediaResponse(**media_record)

    except HTTPException:
        raise
    except Exception as exc:
        media_logger.error(f"Get media error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve media",
        )


@router.put("/{media_id}", response_model=MediaResponse, status_code=status.HTTP_200_OK)
async def update_media_metadata(
    media_id: str,
    update_data: MediaUpdate,
    user_id: str = Depends(get_current_user_id),
):
    """
    Update description and tags of a media file
    """
    try:
        media_record = fetch_and_verify_media_ownership(media_id, user_id)

        update_payload = {"updatedAt": datetime.utcnow().isoformat()}

        if update_data.description is not None:
            update_payload["description"] = update_data.description

        if update_data.tags is not None:
            update_payload["tags"] = update_data.tags

        updated_media = cosmos_db.update_media(media_id, user_id, update_payload)

        return MediaResponse(**updated_media)

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except Exception as exc:
        media_logger.error(f"Update media error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update media",
        )


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    media_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Delete a media file and its metadata
    """
    try:
        media_record = fetch_and_verify_media_ownership(media_id, user_id)

        blob_storage.delete_file(media_record["fileName"])

        thumb_identifier = extract_thumbnail_blob_identifier(media_record)
        if thumb_identifier:
            try:
                blob_storage.delete_file(thumb_identifier)
            except Exception as exc:
                media_logger.warning(f"Thumbnail deletion failed: {exc}")

        cosmos_db.delete_media(media_id, user_id)

        return None

    except HTTPException:
        raise
    except Exception as exc:
        media_logger.error(f"Delete media error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete media",
        )
