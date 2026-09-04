import os
import json
import re
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
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

    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    videos_folder = os.path.abspath(os.path.join(config_dir, config['videos_folder']))
    
    if not os.path.exists(videos_folder):
        raise HTTPException(status_code=500, detail=f"videos_folder does not exist: {videos_folder}")

    files_info = []
    
    # Regex ajustada para buscar archivos .mp4
    pattern_str = f"^{cam_name}_(\\d{{4}}-\\d{{2}}-\\d{{2}}_\\d{{2}}-\\d{{2}}-\\d{{2}})\\.mp4$"
    pattern = re.compile(pattern_str)

    for root_dir, _, files in os.walk(videos_folder):
        for file in files:
            match = pattern.match(file)
            if match:
                date_str = match.group(1)
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")
                    timestamp = int(dt.timestamp())
                    
                    rel_path = os.path.relpath(os.path.join(root_dir, file), videos_folder)
                    rel_path = rel_path.replace('\\', '/')
                    
                    files_info.append({
                        "filepath": rel_path,
                        "timestamp": timestamp,
                        "filename": file
                    })
                except ValueError:
                    pass

    files_info.sort(key=lambda x: x["timestamp"])

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

    # Servir el MP4 directamente. FileResponse gestiona de forma nativa los Range Requests (seeking)
    return FileResponse(full_path, media_type="video/mp4")

if __name__ == "__main__":
    import uvicorn
    print("Starting FastAPI backend server for Camera Recorder...")
    uvicorn.run("server:app", host="0.0.0.0", port=5000, reload=True)