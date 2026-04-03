import os
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "transcripts")


def is_blob_enabled() -> bool:
    return bool(AZURE_BLOB_CONNECTION_STRING)


async def upload_transcript(text: str, summary_id: int, filename: str | None = None) -> str | None:
    """Upload transcript text to Azure Blob Storage. Returns blob URL or None if disabled."""
    if not is_blob_enabled():
        return None

    try:
        from azure.storage.blob.aio import BlobServiceClient
        from azure.storage.blob import ContentSettings
    except ImportError:
        logger.warning("azure-storage-blob not installed, skipping blob upload")
        return None

    now = datetime.now(timezone.utc)
    blob_name = f"{now.strftime('%Y/%m/%d')}/summary_{summary_id}"
    if filename:
        blob_name += f"_{filename}"
    else:
        blob_name += ".txt"

    try:
        async with BlobServiceClient.from_connection_string(AZURE_BLOB_CONNECTION_STRING) as service:
            container = service.get_container_client(AZURE_BLOB_CONTAINER)

            # Ensure container exists
            try:
                await container.create_container()
                logger.info("Created blob container: %s", AZURE_BLOB_CONTAINER)
            except Exception:
                pass  # already exists

            blob_client = container.get_blob_client(blob_name)
            await blob_client.upload_blob(
                text.encode("utf-8"),
                overwrite=True,
                content_settings=ContentSettings(
                    content_type="text/plain; charset=utf-8"
                ),
            )

        logger.info("Uploaded transcript to blob: %s", blob_name)
        return blob_client.url

    except Exception as e:
        logger.error("Failed to upload to Azure Blob Storage: %s", e)
        return None


async def download_transcript(blob_url: str) -> str | None:
    """Download transcript text from Azure Blob Storage by URL."""
    if not is_blob_enabled() or not blob_url:
        return None

    try:
        from azure.storage.blob.aio import BlobClient
    except ImportError:
        return None

    try:
        async with BlobClient.from_blob_url(
            blob_url,
            connection_string=AZURE_BLOB_CONNECTION_STRING,
        ) as blob_client:
            stream = await blob_client.download_blob()
            content = await stream.readall()
            return content.decode("utf-8")
    except Exception as e:
        logger.error("Failed to download from Azure Blob Storage: %s", e)
        return None


async def list_transcripts(prefix: str = "") -> list[dict]:
    """List transcripts in Azure Blob Storage."""
    if not is_blob_enabled():
        return []

    try:
        from azure.storage.blob.aio import BlobServiceClient
    except ImportError:
        return []

    try:
        results = []
        async with BlobServiceClient.from_connection_string(AZURE_BLOB_CONNECTION_STRING) as service:
            container = service.get_container_client(AZURE_BLOB_CONTAINER)
            async for blob in container.list_blobs(name_starts_with=prefix or None):
                results.append({
                    "name": blob.name,
                    "size": blob.size,
                    "last_modified": blob.last_modified.isoformat() if blob.last_modified else None,
                    "url": f"{container.url}/{blob.name}",
                })
        return results
    except Exception as e:
        logger.error("Failed to list blobs: %s", e)
        return []
