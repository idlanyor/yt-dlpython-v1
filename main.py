#!/usr/bin/env python3
import os
import shutil
import uuid
import logging
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
from endpoints.instagram import Instagram
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit

# --- Configuration ---
DOWNLOAD_DIR = "./downloads"
MAX_FILE_SIZE_MB = 500 
BASE_URL = "https://ytdlp.antidonasi.web.id" 

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class YtdlpLogger:
    def debug(self, msg):
        pass
    def warning(self, msg):
        logger.warning(msg)
    def error(self, msg):
        logger.error(msg)

# --- Helper Functions ---
def create_download_dir():
    """Creates the download directory if it doesn't exist."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    logger.info(f"Download directory created/ensured: {DOWNLOAD_DIR}")

def cleanup_file(file_path: str):
    """Removes a file."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
    except Exception as e:
        logger.error(f"Error cleaning up file {file_path}: {e}")

def get_file_size_mb(file_path: str) -> float:
    """Returns the size of a file in megabytes."""
    try:
        return os.path.getsize(file_path) / (1024 * 1024)
    except OSError as e:
        logger.error(f"Error getting file size for {file_path}: {e}")
        return 0

# --- Pydantic Models ---
class DownloadRequest(BaseModel):
    url: str

# Updated Response Model
class DownloadResponse(BaseModel):
    message: str
    title: str | None = None
    url: str | None = None
    thumbnail: str | None = None
    error: str | None = None

# --- Rate Limiter Initialization ---
limiter = Limiter(key_func=get_remote_address)

# Initialize scheduler
scheduler = AsyncIOScheduler()

# Function to clean downloads folder
def clean_downloads_folder():
    """Clean all files in the downloads folder"""
    try:
        if os.path.exists(DOWNLOAD_DIR):
            # Remove all files in the directory
            for filename in os.listdir(DOWNLOAD_DIR):
                file_path = os.path.join(DOWNLOAD_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
            logger.info(f"Downloads folder cleaned successfully at {DOWNLOAD_DIR}")
        else:
            logger.warning(f"Downloads folder does not exist: {DOWNLOAD_DIR}")
    except Exception as e:
        logger.error(f"Error cleaning downloads folder: {e}")

# --- FastAPI App Initialization ---
app = FastAPI(
    title="YouTube Downloader API",
    description="API to download YouTube Audio, Shorts, and Videos using yt-dlp. Provides a public URL for downloaded files.",
    version="1.1.0", # Incremented version
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Schedule daily cleanup at 00:00
scheduler.add_job(
    clean_downloads_folder,
    CronTrigger(hour=0, minute=0),  # Every day at 00:00
    id='daily_cleanup',
    name='Clean downloads folder daily',
    replace_existing=True
)

# --- API Endpoints ---
@app.on_event("startup")
async def startup_event():
    create_download_dir()
    
    # Start the scheduler
    scheduler.start()
    logger.info("Daily cleanup scheduler started - files will be cleaned at 00:00 every day")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown scheduler gracefully"""
    scheduler.shutdown()
    logger.info("Scheduler shutdown completed")

# --- File Serving Endpoint ---
@app.get("/files/{filename}", tags=["Files"])
async def get_file(filename: str):
    """Serves a downloaded file.

    - **filename**: The name of the file to retrieve (as provided in the download response URL).
    """
    file_location = os.path.join(DOWNLOAD_DIR, filename)
    logger.info(f"Attempting to serve file: {file_location}")
    if not os.path.exists(file_location):
        logger.error(f"File not found for serving: {file_location}")
        raise HTTPException(status_code=404, detail="File not found")
    # Optional: Add security checks here if needed (e.g., prevent path traversal)
    if ".." in filename or filename.startswith("/"):
         logger.error(f"Invalid filename requested: {filename}")
         raise HTTPException(status_code=400, detail="Invalid filename")
    return FileResponse(
        file_location,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# Landing page endpoint
@app.get("/", response_class=HTMLResponse, tags=["Landing"])
async def landing_page():
    """Serves the landing page for the YouTube Downloader API."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Landing page not found")

# --- Download Endpoints (Modified Response) ---

@app.get("/download/audio", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_audio_get(request: Request, url: str, background_tasks: BackgroundTasks):
    """Downloads the best quality audio from a YouTube URL and returns a public URL (GET method).

    - **url**: The full URL of the YouTube video.
    """
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'logger': YtdlpLogger(),
        'progress_hooks': [],
    }

    final_filepath = None
    extracted_title = None
    thumbnail_url = None

    try:
        logger.info(f"Starting audio download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            thumbnail_url = info_dict.get('thumbnail')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}.mp3")

            if not os.path.exists(final_filepath):
                 possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(download_id) and f.endswith('.mp3')]
                 if possible_files:
                     final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                 else:
                     possible_files_pre_convert = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(download_id)]
                     if possible_files_pre_convert:
                         logger.warning(f"MP3 file not found directly, possibly still converting? Found: {possible_files_pre_convert}")
                         raise FileNotFoundError(f"Downloaded audio file (expected {final_filepath}) not found after processing.")
                     else:
                         raise FileNotFoundError(f"Downloaded file for {download_id} not found at all.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Audio file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded audio: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional - consider your file retention policy)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600) # e.g., cleanup after 1 hour

        return DownloadResponse(
            message="Audio downloaded successfully.",
            title=extracted_title,
            url=public_url,
            thumbnail=thumbnail_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e):
             raise HTTPException(status_code=404, detail="Video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download audio: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during audio download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.post("/download/audio", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_audio(request: Request, download_request: DownloadRequest, background_tasks: BackgroundTasks):
    """Downloads the best quality audio from a YouTube URL and returns a public URL.

    - **url**: The full URL of the YouTube video.
    """
    url = download_request.url
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'logger': YtdlpLogger(),
        'progress_hooks': [],
    }

    final_filepath = None
    extracted_title = None
    thumbnail_url = None

    try:
        logger.info(f"Starting audio download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            thumbnail_url = info_dict.get('thumbnail')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}.mp3")

            if not os.path.exists(final_filepath):
                 possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(download_id) and f.endswith('.mp3')]
                 if possible_files:
                     final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                 else:
                     possible_files_pre_convert = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(download_id)]
                     if possible_files_pre_convert:
                         logger.warning(f"MP3 file not found directly, possibly still converting? Found: {possible_files_pre_convert}")
                         raise FileNotFoundError(f"Downloaded audio file (expected {final_filepath}) not found after processing.")
                     else:
                         raise FileNotFoundError(f"Downloaded file for {download_id} not found at all.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Audio file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded audio: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional - consider your file retention policy)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600) # e.g., cleanup after 1 hour

        return DownloadResponse(
            message="Audio downloaded successfully.",
            title=extracted_title,
            url=public_url,
            thumbnail=thumbnail_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e):
             raise HTTPException(status_code=404, detail="Video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download audio: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during audio download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.get("/download/shorts", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_shorts_get(request: Request, url: str, background_tasks: BackgroundTasks):
    """Downloads a YouTube Short video and returns a public URL (GET method).

    - **url**: The full URL of the YouTube Short (e.g., https://www.youtube.com/shorts/...). 
    """
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'logger': YtdlpLogger(),
        'progress_hooks': [],
        'merge_output_format': 'mp4',
        'writethumbnail': True,
    }

    final_filepath = None
    extracted_title = None

    try:
        logger.info(f"Starting shorts download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.mp4")

            if not os.path.exists(final_filepath):
                 original_ext = info_dict.get('ext')
                 possible_original_path = None
                 if original_ext:
                     possible_original_path = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.{original_ext}")
                 
                 if possible_original_path and os.path.exists(possible_original_path):
                     final_filepath = possible_original_path
                     logger.warning(f"Merged MP4 not found, using original extension file: {final_filepath}")
                 else:
                    possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"{download_id}_short")]
                    if possible_files:
                        final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                        logger.warning(f"Merged MP4 not found, using first found file: {final_filepath}")
                    else:
                        raise FileNotFoundError(f"Downloaded short video file for {download_id} not found.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Shorts file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded short: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600)

        return DownloadResponse(
            message="Shorts video downloaded successfully.",
            title=extracted_title,
            url=public_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e) or "Private video" in str(e):
             raise HTTPException(status_code=404, detail="Shorts video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download Shorts video: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during shorts download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.post("/download/shorts", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_shorts(request: Request, download_request: DownloadRequest, background_tasks: BackgroundTasks):
    """Downloads a YouTube Short video and returns a public URL.

    - **url**: The full URL of the YouTube Short (e.g., https://www.youtube.com/shorts/...). 
    """
    url = download_request.url
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'logger': YtdlpLogger(),
        'progress_hooks': [],
        'merge_output_format': 'mp4',
        'writethumbnail': True,
    }

    final_filepath = None
    extracted_title = None

    try:
        logger.info(f"Starting shorts download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.mp4")

            if not os.path.exists(final_filepath):
                 original_ext = info_dict.get('ext')
                 possible_original_path = None
                 if original_ext:
                     possible_original_path = os.path.join(DOWNLOAD_DIR, f"{download_id}_short.{original_ext}")
                 
                 if possible_original_path and os.path.exists(possible_original_path):
                     final_filepath = possible_original_path
                     logger.warning(f"Merged MP4 not found, using original extension file: {final_filepath}")
                 else:
                    possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"{download_id}_short")]
                    if possible_files:
                        final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                        logger.warning(f"Merged MP4 not found, using first found file: {final_filepath}")
                    else:
                        raise FileNotFoundError(f"Downloaded short video file for {download_id} not found.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Shorts file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded short: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600)

        return DownloadResponse(
            message="Shorts video downloaded successfully.",
            title=extracted_title,
            url=public_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e) or "Private video" in str(e):
             raise HTTPException(status_code=404, detail="Shorts video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download Shorts video: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during shorts download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.get("/download/video", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_video_get(request: Request, url: str, background_tasks: BackgroundTasks):
    """Downloads the best quality video (usually MP4) from a YouTube URL and returns a public URL (GET method).

    - **url**: The full URL of the YouTube video.
    """
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'logger': YtdlpLogger(),
        'progress_hooks': [],
        'merge_output_format': 'mp4',
    }

    final_filepath = None
    extracted_title = None

    try:
        logger.info(f"Starting video download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            thumbnail_url = info_dict.get('thumbnail')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.mp4")

            if not os.path.exists(final_filepath):
                 original_ext = info_dict.get('ext')
                 possible_original_path = None
                 if original_ext:
                     possible_original_path = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.{original_ext}")
                 
                 if possible_original_path and os.path.exists(possible_original_path):
                     final_filepath = possible_original_path
                     logger.warning(f"Merged MP4 not found, using original extension file: {final_filepath}")
                 else:
                    possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"{download_id}_video")]
                    if possible_files:
                        final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                        logger.warning(f"Merged MP4 not found, using first found file: {final_filepath}")
                    else:
                        raise FileNotFoundError(f"Downloaded video file for {download_id} not found.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Video file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded video: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600)

        return DownloadResponse(
            message="Video downloaded successfully.",
            title=extracted_title,
            url=public_url,
            thumbnail=thumbnail_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e) or "Private video" in str(e):
             raise HTTPException(status_code=404, detail="Video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download video: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during video download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.post("/download/video", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_video(request: Request, download_request: DownloadRequest, background_tasks: BackgroundTasks):
    """Downloads the best quality video (usually MP4) from a YouTube URL and returns a public URL.

    - **url**: The full URL of the YouTube video.
    """
    url = download_request.url
    download_id = str(uuid.uuid4())
    output_path_template = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path_template,
        'noplaylist': True,
        'logger': YtdlpLogger(),
        'progress_hooks': [],
        'merge_output_format': 'mp4',
    }

    final_filepath = None
    extracted_title = None

    try:
        logger.info(f"Starting video download for URL: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            extracted_title = info_dict.get('title', 'Unknown Title')
            thumbnail_url = info_dict.get('thumbnail')
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.mp4")

            if not os.path.exists(final_filepath):
                 original_ext = info_dict.get('ext')
                 possible_original_path = None
                 if original_ext:
                     possible_original_path = os.path.join(DOWNLOAD_DIR, f"{download_id}_video.{original_ext}")
                 
                 if possible_original_path and os.path.exists(possible_original_path):
                     final_filepath = possible_original_path
                     logger.warning(f"Merged MP4 not found, using original extension file: {final_filepath}")
                 else:
                    possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"{download_id}_video")]
                    if possible_files:
                        final_filepath = os.path.join(DOWNLOAD_DIR, possible_files[0])
                        logger.warning(f"Merged MP4 not found, using first found file: {final_filepath}")
                    else:
                        raise FileNotFoundError(f"Downloaded video file for {download_id} not found.")

            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Video file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")

        # Construct the public URL
        filename = os.path.basename(final_filepath)
        public_url = f"{BASE_URL}/files/{filename}"
        logger.info(f"Successfully downloaded video: {extracted_title}, URL: {public_url}")
        
        # Schedule cleanup (optional)
        # background_tasks.add_task(cleanup_file, final_filepath, delay=3600)

        return DownloadResponse(
            message="Video downloaded successfully.",
            title=extracted_title,
            url=public_url,
            thumbnail=thumbnail_url
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp Download Error for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        if "Unsupported URL" in str(e):
             raise HTTPException(status_code=400, detail=f"Unsupported URL: {url}")
        elif "Video unavailable" in str(e) or "Private video" in str(e):
             raise HTTPException(status_code=404, detail="Video not found or unavailable.")
        else:
             raise HTTPException(status_code=500, detail=f"Failed to download video: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error after download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing downloaded file: {e}")
    except Exception as e:
        logger.exception(f"General Error during video download for {url}: {e}")
        if final_filepath and os.path.exists(final_filepath):
             cleanup_file(final_filepath)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.get("/download/instagram", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_instagram_get(request: Request, url: str, background_tasks: BackgroundTasks):
    """Downloads Instagram content (photos/videos) from an Instagram URL using GET method with URL parameter.

    - **url**: The full URL of the Instagram post (e.g., https://www.instagram.com/p/..., https://www.instagram.com/reel/...).
    """
    try:
        logger.info(f"Starting Instagram download for URL: {url}")
        result = Instagram(url)
        
        if 'msg' in result and result['msg'] == 'Try again later':
            logger.error(f"Instagram download failed for {url}: Service temporarily unavailable")
            raise HTTPException(status_code=503, detail="Instagram service temporarily unavailable. Please try again later.")
        
        if 'url' not in result or not result['url']:
            logger.error(f"No download URLs found for Instagram URL: {url}")
            raise HTTPException(status_code=404, detail="No downloadable content found for this Instagram URL.")
        
        download_urls = result['url']
        if isinstance(download_urls, str):
            download_urls = [download_urls]
        
        # Get metadata if available
        metadata = result.get('metadata', {})
        title = metadata.get('caption', 'Instagram Content')
        if len(title) > 100:  # Truncate long captions
            title = title[:97] + "..."
        
        # For Instagram, we return the direct download URLs since they're already hosted
        # We could optionally download and re-host them, but Instagram URLs are typically accessible
        primary_url = download_urls[0] if download_urls else None
        
        logger.info(f"Successfully processed Instagram content: {title}, URLs: {len(download_urls)}")
        
        return DownloadResponse(
            message=f"Instagram content processed successfully. Found {len(download_urls)} item(s).",
            title=title,
            url=primary_url
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except ValueError as e:
        logger.error(f"Invalid Instagram URL {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid Instagram URL: {e}")
    except Exception as e:
        logger.exception(f"General Error during Instagram download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while processing Instagram content: {e}")

@app.post("/download/instagram", response_model=DownloadResponse, tags=["Downloads"])
@limiter.limit("5/second")
@limiter.limit("50/minute")
async def download_instagram_post(request: Request, download_request: DownloadRequest, background_tasks: BackgroundTasks):
    """Downloads Instagram content (photos/videos) from an Instagram URL using POST method with JSON body.

    - **url**: The full URL of the Instagram post (e.g., https://www.instagram.com/p/..., https://www.instagram.com/reel/...).
    """
    url = download_request.url
    download_id = str(uuid.uuid4())
    
    try:
        logger.info(f"Starting Instagram download for URL: {url}")
        result = Instagram(url)
        
        if 'msg' in result and result['msg'] == 'Try again later':
            logger.error(f"Instagram download failed for {url}: Service temporarily unavailable")
            raise HTTPException(status_code=503, detail="Instagram service temporarily unavailable. Please try again later.")
        
        if 'url' not in result or not result['url']:
            logger.error(f"No download URLs found for Instagram URL: {url}")
            raise HTTPException(status_code=404, detail="No downloadable content found for this Instagram URL.")
        
        download_urls = result['url']
        if isinstance(download_urls, str):
            download_urls = [download_urls]
        
        # Get metadata if available
        metadata = result.get('metadata', {})
        title = metadata.get('caption', 'Instagram Content')
        if len(title) > 100:  # Truncate long captions
            title = title[:97] + "..."
        
        # Download and re-host the Instagram content
        primary_url = download_urls[0] if download_urls else None
        
        if primary_url:
            # Determine file extension from URL or content type
            try:
                response = requests.head(primary_url, timeout=10)
                content_type = response.headers.get('content-type', '')
            except:
                content_type = ''
            
            if 'video' in content_type:
                file_extension = 'mp4'
            elif 'image' in content_type:
                file_extension = 'jpg'
            else:
                # Fallback: try to get extension from URL
                file_extension = primary_url.split('.')[-1].split('?')[0] if '.' in primary_url else 'jpg'
            
            final_filepath = os.path.join(DOWNLOAD_DIR, f"{download_id}_instagram.{file_extension}")
            
            # Download the file
            response = requests.get(primary_url, stream=True, timeout=30)
            response.raise_for_status()
            
            with open(final_filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            file_size_mb = get_file_size_mb(final_filepath)
            logger.info(f"Instagram file downloaded: {final_filepath}, Size: {file_size_mb:.2f} MB")
            
            if file_size_mb > MAX_FILE_SIZE_MB:
                cleanup_file(final_filepath)
                logger.warning(f"File size ({file_size_mb:.2f} MB) exceeds limit ({MAX_FILE_SIZE_MB} MB) for {url}")
                raise HTTPException(status_code=400, detail=f"File size ({file_size_mb:.2f} MB) exceeds the limit of {MAX_FILE_SIZE_MB} MB.")
            
            # Construct the public URL
            filename = os.path.basename(final_filepath)
            public_url = f"{BASE_URL}/files/{filename}"
            
            logger.info(f"Successfully processed Instagram content: {title}, URL: {public_url}")
            
            return DownloadResponse(
                message=f"Instagram content downloaded successfully.",
                title=title,
                url=public_url
            )
        else:
            raise HTTPException(status_code=404, detail="No downloadable content found for this Instagram URL.")
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except ValueError as e:
        logger.error(f"Invalid Instagram URL {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid Instagram URL: {e}")
    except Exception as e:
        logger.exception(f"General Error during Instagram download for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while processing Instagram content: {e}")

# --- Main Execution (for local testing) ---
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server, downloads will be stored in: {DOWNLOAD_DIR}")
    logger.info(f"Files will be served from base URL: {BASE_URL}/files/")
    uvicorn.run(app, host="0.0.0.0", port=8087)

