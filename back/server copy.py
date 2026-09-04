import os
import json
import re
import subprocess
import signal
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config.json: {e}")
        return None

@app.get("/api/config")
def get_config():
    config = load_config()
    if not config:
        raise HTTPException(status_code=500, detail="Could not load config")
    return config

@app.get("/api/timeline")
def get_timeline(cam_name: str = Query(..., description="Name of the camera")):
    config = load_config()
    if not config or 'videos_folder' not in config:
        raise HTTPException(status_code=500, detail="videos_folder not configured")

    # Ensure path is absolute relative to root execution
    # config['videos_folder'] is "../data" relative to the config folder, so we can join it with the config dir path
    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    videos_folder = os.path.abspath(os.path.join(config_dir, config['videos_folder']))
    
    if not os.path.exists(videos_folder):
        raise HTTPException(status_code=500, detail=f"videos_folder does not exist: {videos_folder}")

    files_info = []
    
    # Regex to match: {cam_name}_YYYY-MM-DD_HH-MM-SS.mkv
    pattern_str = f"^{cam_name}_(\\d{{4}}-\\d{{2}}-\\d{{2}}_\\d{{2}}-\\d{{2}}-\\d{{2}})\\.mkv$"
    pattern = re.compile(pattern_str)

    # Walk through the directory (including date folders)
    for root_dir, _, files in os.walk(videos_folder):
        for file in files:
            match = pattern.match(file)
            if match:
                date_str = match.group(1)
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")
                    timestamp = int(dt.timestamp())
                    
                    # Store relative path from videos_folder
                    rel_path = os.path.relpath(os.path.join(root_dir, file), videos_folder)
                    # Convert to forward slashes for web URLs
                    rel_path = rel_path.replace('\\', '/')
                    
                    files_info.append({
                        "filepath": rel_path,
                        "timestamp": timestamp,
                        "filename": file
                    })
                except ValueError:
                    pass

    # Sort files chronologically
    files_info.sort(key=lambda x: x["timestamp"])

    # Remove the last one as it is the currently recording file
    if len(files_info) > 0:
        files_info.pop()

    return {"files": files_info}


@app.get("/api/video/{filepath:path}")
def serve_video(filepath: str):
    config = load_config()
    if not config or 'videos_folder' not in config:
        raise HTTPException(status_code=500, detail="videos_folder not configured")
        
    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    videos_folder = os.path.abspath(os.path.join(config_dir, config['videos_folder']))
    full_path = os.path.join(videos_folder, filepath)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Video not found")

    # Remux MKV to fragmented MP4 on-the-fly via ffmpeg:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", full_path,
        "-c:v", "copy",                    # Vídeo directo sin tocar (0% CPU)
        "-c:a", "aac",                    # Fuerza codificador AAC estándar
        "-profile:a", "aac_low",          # Perfil AAC-LC (compatible con 100% de navegadores)
        "-sample_fmt", "fltp",            # Formato de muestra interno estándar
        "-ar", "44100",                    # Resamplea los 16kHz raros de D-Link a 44.1kHz estándar
        "-ac", "2",                       # Fuerza 2 canales estéreo
        "-b:a", "128k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1"
    ]

    def stream_ffmpeg():
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # On Windows, use CREATE_NEW_PROCESS_GROUP for clean termination
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        try:
            while True:
                chunk = process.stdout.read(65536)  # 64KB chunks
                if not chunk:
                    break
                yield chunk
        finally:
            process.stdout.close()
            process.terminate()
            process.wait()

    return StreamingResponse(stream_ffmpeg(), media_type="video/mp4", headers={"Accept-Ranges": "bytes","Cache-Control": "no-cache"}
)

if __name__ == "__main__":
    import uvicorn
    print("Starting FastAPI backend server for Camera Recorder...")
    uvicorn.run("server:app", host="0.0.0.0", port=5000, reload=True)
