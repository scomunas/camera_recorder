#!/usr/bin/env python3
import os
import sys
import time
import signal
import datetime
import threading
import subprocess
from collections import deque

# Configuración de rutas y almacenamiento
RECORDINGS_DIR = "/data"
LOG_BUFFERS = {}

# Configuración 100% HTTP para todas las cámaras
CAMERAS = {
    "balcon": "http://localhost:1984/api/stream.mp4?src=balcon",
    "comedor": "http://localhost:1984/api/stream.mp4?src=comedor",
    "estudio": "http://localhost:1984/api/stream.mp4?src=estudio",
    "terraza": "http://localhost:1984/api/stream.mp4?src=terraza",
}

def log(msg):
    """Escribe un mensaje de log con timestamp a la salida estándar."""
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")
    print(f"{timestamp} {msg}", flush=True)

NFS_SHARE = "192.168.68.100:/volume1/cameras"

def ensure_directory():
    """Intenta montar el recurso NFS explícito si no está montado."""
    if not os.path.ismount(RECORDINGS_DIR):
        log(f"[WARN] {RECORDINGS_DIR} no está montado. Intentando montar {NFS_SHARE}...")
        try:
            subprocess.run(
                ["mount", "-t", "nfs", NFS_SHARE, RECORDINGS_DIR], 
                check=True, 
                timeout=10
            )
            time.sleep(1)
        except Exception as e:
            log(f"[ERROR] Falló el montaje de {NFS_SHARE}: {e}")

    if not os.path.ismount(RECORDINGS_DIR):
        log(f"[CRÍTICO] {RECORDINGS_DIR} NO es un punto de montaje activo. Abortando script.")
        sys.exit(1)

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    log(f"[OK] Punto de montaje {RECORDINGS_DIR} activo y verificado.")

def start_recording(cam_name, stream_url):
    """Inicia el proceso hijo de FFmpeg consumiendo stderr en un buffer circular."""
    output_pattern = os.path.join(
        RECORDINGS_DIR, "%Y-%m-%d", f"{cam_name}_%Y-%m-%d_%H-%M-%S.mp4"
    )

    today_dir = os.path.join(RECORDINGS_DIR, datetime.datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(today_dir, exist_ok=True)

    # Configuración limpia de FFmpeg para HTTP
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+nobuffer+genpts",
        "-i", stream_url,
        "-c", "copy",
        "-f", "segment",
        "-segment_time", "900",
        "-segment_atclocktime", "1",
        "-strftime", "1",
        "-strftime_mkdir", "1",
        "-reset_timestamps", "1",
        "-movflags", "+frag_keyframe+empty_moov",
        output_pattern,
    ]

    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1
    )

    LOG_BUFFERS[cam_name] = deque(maxlen=50)

    def _consume_stderr(p, name):
        try:
            if p.stderr:
                for line in iter(p.stderr.readline, ""):
                    if not line:
                        break
                    LOG_BUFFERS[name].append(line.strip())
        except Exception:
            pass
        finally:
            if p.stderr and not p.stderr.closed:
                p.stderr.close()

    thread = threading.Thread(
        target=_consume_stderr, args=(proc, cam_name), daemon=True
    )
    thread.start()

    return proc

def main():
    ensure_directory()
    log("Iniciando servicio unificado de grabación NVR (Modo HTTP)...")

    processes = {}
    start_times = {}
    running = True

    def signal_handler(signum, frame):
        nonlocal running
        log("Deteniendo servicio y finalizando grabaciones...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for name, url in CAMERAS.items():
        log(f"Lanzando grabación para {name}...")
        processes[name] = start_recording(name, url)
        start_times[name] = time.time()

    while running:
        time.sleep(5)

        for name, url in CAMERAS.items():
            proc = processes.get(name)
            
            if proc is not None:
                retcode = proc.poll()
                
                if retcode is not None:
                    duration = int(time.time() - start_times.get(name, time.time()))
                    
                    last_errors = list(LOG_BUFFERS.get(name, []))
                    err_msg = f" | Último error FFmpeg: '{last_errors[-1]}'" if last_errors else ""
                    
                    log(f"[ALERTA] Caída detectada en {name} (Código: {retcode}, Duración: {duration}s){err_msg}.")

                    time.sleep(5)
                    
                    log(f"Lanzando grabación para {name}...")
                    processes[name] = start_recording(name, url)
                    start_times[name] = time.time()

    for name, proc in processes.items():
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

if __name__ == "__main__":
    main()