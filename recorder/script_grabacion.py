#!/usr/bin/env python3
import os
import sys
import time
import signal
import datetime
import threading
import subprocess
from collections import deque

RECORDINGS_DIR = "/data"
LOG_BUFFERS = {}

CAMERAS = {
    "balcon": "http://localhost:1984/api/stream.mp4?src=balcon",
    "comedor": "http://localhost:1984/api/stream.mp4?src=comedor",
    "estudio": "http://localhost:1984/api/stream.mp4?src=estudio",
    "terraza": "http://localhost:1984/api/stream.mp4?src=terraza",
}

def log(msg):
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")
    print(f"{timestamp} {msg}", flush=True)

NFS_SHARE = "192.168.68.100:/volume1/cameras"

def ensure_directory():
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

    # Crear directorio de hoy y de mañana para prevenir fallos a las 00:00h
    today_dir = os.path.join(RECORDINGS_DIR, datetime.datetime.now().strftime("%Y-%m-%d"))
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    tomorrow_dir = os.path.join(RECORDINGS_DIR, tomorrow.strftime("%Y-%m-%d"))
    
    os.makedirs(today_dir, exist_ok=True)
    os.makedirs(tomorrow_dir, exist_ok=True)

def start_recording(cam_name, stream_url):
    output_pattern = os.path.join(
        RECORDINGS_DIR, "%Y-%m-%d", f"{cam_name}_%Y-%m-%d_%H-%M-%S.mp4"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        # --- LÍMITES STRICTOS DE MEMORIA Y BÚFER ---
        "-probesize", "32K",            # Solo analiza 32KB para iniciar (evita bufer en RAM)
        "-analyzeduration", "0",        # Tiempo de análisis 0
        "-fflags", "+nobuffer+discardcorrupt", # Si hay frames corruptos los tira, no los guarda
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "2",
        # ---------------------------------------------
        "-i", stream_url,
        "-c", "copy",
        "-max_delay", "500000",         # Búfer máximo de red de 0.5s
        "-f", "segment",
        "-segment_time", "900",
        "-segment_atclocktime", "1",
        "-strftime", "1",
        "-reset_timestamps", "1",
        "-movflags", "+frag_keyframe+empty_moov",
        output_pattern,
    ]

    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1
    )

    LOG_BUFFERS[cam_name] = deque(maxlen=20)

    def _consume_stderr(p, name):
        try:
            if p.stderr:
                for line in iter(p.stderr.readline, ""):
                    if not line:
                        break
                    buf = LOG_BUFFERS.get(name)
                    if buf is not None:
                        buf.append(line.strip())
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
    log("Iniciando servicio unificado de grabación NVR...")

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

    last_dir_check = time.time()

    while running:
        time.sleep(5)

        # Mantenimiento de directorios cada 1 hora (asegura la carpeta del día siguiente)
        if time.time() - last_dir_check > 3600:
            ensure_directory()
            last_dir_check = time.time()

        for name, url in CAMERAS.items():
            proc = processes.get(name)

            # 1. Si el proceso ha muerto por error/corte
            if proc is not None:
                retcode = proc.poll()
                elapsed = time.time() - start_times.get(name, time.time())

                now = datetime.datetime.now()
                if retcode is not None:
                    duration = int(time.time() - start_times.get(name, time.time()))
                    last_errors = list(LOG_BUFFERS.get(name, []))
                    err_msg = f" | Último error FFmpeg: '{last_errors[-1]}'" if last_errors else ""
                    
                    log(f"[ALERTA] Caída detectada en {name} (Código: {retcode}, Duración: {duration}s){err_msg}.")

                    # Limpiar explícitamente el buffer antiguo
                    LOG_BUFFERS[name].clear()
                    time.sleep(5)
                    
                    log(f"Lanzando grabación para {name}...")
                    processes[name] = start_recording(name, url)
                    start_times[name] = time.time()
                # 2. Rotación preventiva: solo en el minuto :00 (entre el segundo 00 y el 10)
                # y si el proceso lleva al menos 3h 50m activo (13800s)
                elif elapsed >= 13800 and now.minute == 0 and now.second <= 10:
                    log(f"[INFO] Rotación limpia alineada a la hora para {name}...")
                    
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()

                    LOG_BUFFERS[name].clear()
                    time.sleep(1)

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