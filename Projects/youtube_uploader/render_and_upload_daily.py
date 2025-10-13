#!/usr/bin/env python3
import os, sys, time, glob, shutil, subprocess, shlex, csv, json
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from zoneinfo import ZoneInfo
import unicodedata

# ====== K O N F I G ======
BASE_DIR   = "/path/to/youtube_uploader/"
QUEUE_DIR  = os.path.join(BASE_DIR, "queue")
RENDER_DIR = os.path.join(BASE_DIR, "rendered")
DONE_DIR   = os.path.join(BASE_DIR, "uploaded")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
# LOG_FILE   = os.path.join(BASE_DIR, "uploader.log")
CSV_FILE = os.path.join(BASE_DIR, "overlay_texts.csv")
# Schedule videos
SCHEDULE_ENABLED = True
SCHEDULE_TZ = "Europe/Amsterdam"
SCHEDULE_MODE = "weekly" # daily or weekly
SCHEDULE_HOUR = 9     # 24h format
SCHEDULE_MINUTE = 0
SCHEDULE_WEEKDAY = 0  # 0=Monday, 6=Sunday (for weekly mode)
NEXT_SLOT_FILE   = os.path.join(BASE_DIR, "next_slot.json")

# YouTube settings
PRIVACY        = "private"   # private, public, unlisted
CATEGORY_ID    = "15" # 15 = Pets & Animals, 17 = Sports
MADE_FOR_KIDS  = False
TITLE_PREFIX   = "" # e.g. "name of channel - daily video name"
DEFAULT_DESC   = "" # video description
TAGS = ["cute", "shorts"] 
DEFAULT_LANG = "en"

# Text parameters (FFmpeg)
# macOS: "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_FILE = "/Library/Fonts/Arial Unicode.ttf"
TEXT_SIZE = 72 
TEXT_COLOR = "white" # with @opacity (0.0-1.0) e.g. black@0.8
TEXT_BORDER = "black"
TEXT_BORDER_W = 2
TEXT_POS = "center"   # top, center, bottom
MARGIN = 40          
BOX = True            # background boxing
BOX_COLOR = "black@0.4"  # transparancy 
LINE_SPACING = 5
BOX_BORDER_W = 10

########################## More options
# BOX_BORDER_W = 5
# SHADOW_COLOR = "black@0.6"
# SHADOW_X = 2
# SHADOW_Y = 2
# ALPHA = 0.9
# LINE_SPACING = 5
# ENABLE_EXPR = None  # e.g. "between(t,0,5)"
##########################

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
EXTS = (".mp4", ".mov", ".MOV")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_creds():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds

def pick_next_file():
    files = []
    for ext in EXTS:
        files.extend(glob.glob(os.path.join(QUEUE_DIR, f"*{ext}")))
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p))
    return files[0]

# def read_overlay_text(video_path):
#     base, _ = os.path.splitext(video_path)
#     txt_path = base + ".txt"
#     if os.path.exists(txt_path):
#         with open(txt_path, "r", encoding="utf-8") as f:
#             return f.read().strip()
#     # fallback: use filename (without extension) if not found in CSV
#     return os.path.basename(base)

# def read_overlay_text(video_path):
#     video_name = os.path.basename(video_path)
#     if os.path.exists(CSV_FILE):
#         try:
#             with open(CSV_FILE, newline='', encoding='utf-8') as f:
#                 reader = csv.DictReader(f)
#                 for row in reader:
#                     fn = row.get("filename")
#                     txt = row.get("text")
#                     if fn and fn.strip().lower() == video_name.lower() and txt:
#                         return txt.strip()
#         except Exception as e:
#             log(f"Warning: CSV read error: {e}")
#     # fallback
#     base, _ = os.path.splitext(video_name)
#     return base

def _title_with_prefix(base: str) -> str:
    return f"{TITLE_PREFIX}: {base}".strip(": ").strip()

def read_metadata(video_path):
    """Return overlay text, title, tags, and hashtags from CSV."""
    video_name = os.path.basename(video_path)
    base, _ = os.path.splitext(video_name)
    want = {"text": None, "title": None, "tags": None, "hashtags": None}

    if os.path.exists(CSV_FILE):
        try:
            with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f, delimiter=",")
                for row in reader:
                    fn = (row.get("filename") or "").strip()
                    base_fn, _ = os.path.splitext(fn)
                    base_video, _ = os.path.splitext(video_name)
                    if base_fn.lower() == base_video.lower():
                        want["text"] = (row.get("text") or "").strip() or None
                        want["title"] = (row.get("title") or "").strip() or None
                        want["hashtags"] = (row.get("hashtags") or "").strip() or None
                        break
        except Exception as e:
            log(f"CSV read warning: {e}")

    # fallbacks
    if want["text"] is None:
        want["text"] = base
    if want["title"] is None:
        want["title"] = f"{TITLE_PREFIX}: {base}"
    if want["tags"] is None:
        want["tags"] = TAGS

    return want

def _ffmpeg_safe_text(s: str) -> str: # to prevent FFmpeg errors with special chars like ', :, or \\ 
    return (s.replace("\\","\\\\")
             .replace(":", "\\:")
             .replace("'", r"\'")
             .replace("%", r"\%"))

def ffmpeg_drawtext_cmd(input_path, output_path, text):
    if not os.path.exists(FONT_FILE):
        raise FileNotFoundError(f"FONT_FILE not found: {FONT_FILE}")
    safe = _ffmpeg_safe_text(text)

    # Text position
    if TEXT_POS == "top":
        y_expr = f"{MARGIN}"
    elif TEXT_POS == "center":
        y_expr = "(h-text_h)/2"
    else:
        y_expr = f"h-text_h-{MARGIN}"

    # stroke and boxing
    draw_opts = [
        f"fontfile='{FONT_FILE}'",
        f"text='{safe}'",
        f"fontsize={TEXT_SIZE}",
        f"fontcolor={TEXT_COLOR}",
        f"x=(w-text_w)/2",
        f"y={y_expr}",
        f"borderw={TEXT_BORDER_W}",
        f"bordercolor={TEXT_BORDER}",
        f"line_spacing={LINE_SPACING}",
    ]
    if BOX:
        draw_opts += [f"box=1", 
                      f"boxcolor={BOX_COLOR}", 
                      f"boxborderw={BOX_BORDER_W}"]

    filter_str = f"drawtext={':'.join(draw_opts)}" #Combine all as a string for ffmpeg

    cmd = [
        "ffmpeg",
        "-y",  # overwrite output
        "-i", input_path, # input video
        "-vf", filter_str,  # video filter (drawtext)
        "-c:a", "copy", # copy audio as is
        output_path
    ]
    return cmd

def render_with_text(input_path):
    os.makedirs(RENDER_DIR, exist_ok=True)

    meta = read_metadata(input_path)      # <-- get text and title
    overlay = meta["text"]                # use text for the on-video overlay

    out_name = os.path.splitext(os.path.basename(input_path))[0] + "_rendered.mp4"
    output_path = os.path.join(RENDER_DIR, out_name)

    cmd = ffmpeg_drawtext_cmd(input_path, output_path, overlay)
    log("FFmpeg: " + " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg error: " + proc.stderr[-500:])
    return output_path, meta  # return both path and metadata

def _local_now(): # current time in scheduling timezone
    return datetime.now(ZoneInfo(SCHEDULE_TZ))

def _next_daily_slot(after_dt_local: datetime) -> datetime:
    base = after_dt_local.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    if base < after_dt_local:
        base += timedelta(days=1)
    return base

def _next_weekly_slot(after_dt_local: datetime) -> datetime:
    base = after_dt_local.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    days_ahead = (SCHEDULE_WEEKDAY - base.weekday()) % 7
    candidate = base + timedelta(days=days_ahead)
    if candidate < after_dt_local:
        candidate += timedelta(days=7)
    return candidate

def _load_next_slot_local() -> datetime | None:
    if not os.path.exists(NEXT_SLOT_FILE):
        return None
    try:
        with open(NEXT_SLOT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return datetime.fromisoformat(data["next_local"]).replace(tzinfo=ZoneInfo(SCHEDULE_TZ))
    except Exception:
        return None

def _save_next_slot_local(dt_local: datetime) -> None:
    with open(NEXT_SLOT_FILE, "w", encoding="utf-8") as f:
        json.dump({"next_local": dt_local.replace(tzinfo=None).isoformat()}, f)

def get_and_advance_next_slot_local() -> datetime:
    """
    Returns the next available local slot per SCHEDULE_MODE, and advances by 1 day (daily) or 7 days (weekly).
    If the stored slot is missing/past, initialize from now().
    """
    now_local = _local_now()
    nxt = _load_next_slot_local()

    if SCHEDULE_MODE == "daily":
        step = timedelta(days=1)
        if not nxt or nxt < now_local:
            nxt = _next_daily_slot(now_local)
    else:  # "weekly"
        step = timedelta(days=7)
        if not nxt or nxt < now_local:
            nxt = _next_weekly_slot(now_local)
    _save_next_slot_local(nxt + step)  # advance pointer
    return nxt

def to_rfc3339_utc(dt_local: datetime) -> str:
    dt_utc = dt_local.astimezone(ZoneInfo("UTC"))
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

############## UPLOAD ################
def upload_video(youtube, path, title, description, tags, publish_at_iso=None):
    body = {
        "snippet": {
            "title": title,
            "description": description or "",
            "tags": tags or [],
            "categoryId": CATEGORY_ID,
            "defaultLanguage": DEFAULT_LANG,
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": MADE_FOR_KIDS,
        },
    }
    if publish_at_iso:
        body["status"]["publishAt"] = publish_at_iso
    
    media = MediaFileUpload(path, chunksize=8*1024*1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", 
        body=body, 
        media_body=media
        # notifySubscribers=False  # uncomment to suppress notifications
        )
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log(f"progress {int(status.progress()*100)}% for {os.path.basename(path)}")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                log(f"Transient HTTP {e.resp.status}; 10s sonra tekrar denenecek...")
                time.sleep(10)
                continue
            raise
    return response["id"]

def main():
    os.makedirs(QUEUE_DIR, exist_ok=True)
    os.makedirs(DONE_DIR,  exist_ok=True)

    creds = get_creds()
    if not creds:
        log("Token.json is not valid")
        sys.exit(1)
    yt = build("youtube", "v3", credentials=creds)

    processed = 0
    while True:
        src = pick_next_file()
        if not src:
            if processed == 0:
                log("Queue is empty. No new video.")
            else:
                log(f"All done. Processed {processed} video(s).")
            return

        try:
            log(f"Render is starting: {src}")
            rendered_path, meta = render_with_text(src)

            title = meta["title"]
            if meta.get("hashtags"):
                title = f"{title} {meta['hashtags']}".strip() # CSV-provided title (with fallback)
            description = ""               # <-- keep description empty as you wanted
            tags = TAGS                    # or move tags to CSV later if you like

            publish_at_iso = None
            if SCHEDULE_ENABLED:
                slot_local = get_and_advance_next_slot_local()
                publish_at_iso = to_rfc3339_utc(slot_local)
                log(f"Scheduling for {slot_local.isoformat()} ({SCHEDULE_TZ}) -> {publish_at_iso} UTC")

            vid = upload_video(yt, rendered_path, title, description, tags, publish_at_iso=publish_at_iso)
            log(f"Done. ID: {vid}  URL: https://www.youtube.com/watch?v={vid}")

            # move originals & renders
            shutil.move(src, os.path.join(DONE_DIR, os.path.basename(src)))
            shutil.move(rendered_path, os.path.join(DONE_DIR, os.path.basename(rendered_path)))

            processed += 1

        except Exception as e:
            log(f"Error while processing {src}: {e}")
            # Move the problematic file aside or break; your choice
            break

if __name__ == "__main__":
    main()