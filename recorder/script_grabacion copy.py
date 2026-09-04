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

# Configuración de cámaras RTSP
CAMERAS = {
    "balcon": "rtsp://localhost:8554/balcon",
    "comedor": "rtsp://localhost:8554/comedor",
    "estudio": "rtsp://localhost:8554/estudio",
    "terraza": "rtsp://localhost:8554/terraza",
}

def log(msg):
    """Escribe un mensaje de log con timestamp a la salida estándar."""
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")
    print(f"{timestamp} {msg}", flush=True)

# Reemplaza esta variable al inicio de tu script con los datos reales de tu NAS:
NFS_SHARE = "192.168.68.100:/volume1/cameras"  # <-- EJEMPLO: "192.168.68.50:/volume1/grabaciones"

def ensure_directory():
    """Intenta montar el recurso NFS explícito si no está montado."""
    if not os.path.ismount(RECORDINGS_DIR):
        log(f"[WARN] {RECORDINGS_DIR} no está montado. Intentando montar {NFS_SHARE}...")
        try:
            # Comando de montaje completo especificando protocolo NFS
            subprocess.run(
                ["mount", "-t", "nfs", NFS_SHARE, RECORDINGS_DIR], 
                check=True, 
                timeout=10
            )
            time.sleep(1)
        except Exception as e:
            log(f"[ERROR] Falló el montaje de {NFS_SHARE}: {e}")

    # Bloqueo de seguridad: si no ha montado, aborta para no escribir en disco local
    if not os.path.ismount(RECORDINGS_DIR):
        log(f"[CRÍTICO] {RECORDINGS_DIR} NO es un punto de montaje activo. Abortando script.")
        sys.exit(1)

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    log(f"[OK] Punto de montaje {RECORDINGS_DIR} activo y verificado.")

def start_recording(cam_name, rtsp_url):
    """Inicia el proceso hijo de FFmpeg consumiendo stderr en un buffer circular."""
    output_pattern = os.path.join(
        RECORDINGS_DIR, "%Y-%m-%d", f"{cam_name}_%Y-%m-%d_%H-%M-%S.mkv"
    )

    # Asegurar que existe el directorio específico del día antes de lanzar FFmpeg
    today_dir = os.path.join(RECORDINGS_DIR, datetime.datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(today_dir, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+nobuffer+genpts",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "3000000",
        "-i",
        rtsp_url,
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        "900",
        "-segment_atclocktime",
        "1",
        "-strftime",
        "1",
        "-strftime_mkdir",
        "1",
        "-reset_timestamps",
        "1",
        output_pattern,
    ]

    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1
    )

    # Inicializar o vaciar buffer circular en RAM (últimas 50 líneas)
    LOG_BUFFERS[cam_name] = deque(maxlen=50)

    # Hilo ligero para vaciar continuamente el pipe stderr de FFmpeg
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
    log("Iniciando servicio unificado de grabación NVR...")

    processes = {}
    start_times = {}

    # Manejo de señales para un apagado limpio
    running = True

    def signal_handler(signum, frame):
        nonlocal running
        log("Deteniendo servicio y finalizando grabaciones...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Lanzar la grabación inicial para todas las cámaras
    for name, url in CAMERAS.items():
        log(f"Lanzando grabación para {name}...")
        processes[name] = start_recording(name, url)
        start_times[name] = time.time()

    # Bucle principal de monitoreo
    while running:
        time.sleep(5)

        for name, url in CAMERAS.items():
            proc = processes.get(name)
            
            if proc is not None:
                retcode = proc.poll()
                
                # Si el proceso ha terminado inesperadamente
                if retcode is not None:
                    duration = int(time.time() - start_times.get(name, time.time()))
                    
                    # Extraer últimos errores del buffer
                    last_errors = list(LOG_BUFFERS.get(name, []))
                    err_msg = f" | Último error FFmpeg: '{last_errors[-1]}'" if last_errors else ""
                    
                    log(f"[ALERTA] Caída detectada en {name} (Código: {retcode}, Duración: {duration}s){err_msg}.")

                    time.sleep(5) # Añade 5 segundos de delay antes de reintentar la conexión
                    
                    # Reiniciar proceso de grabación
                    log(f"Lanzando grabación para {name}...")
                    processes[name] = start_recording(name, url)
                    start_times[name] = time.time()

    # Limpieza final al detener systemd
    for name, proc in processes.items():
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

if __name__ == "__main__":
    main()