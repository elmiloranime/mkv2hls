import os
import json
import subprocess
from colorama import Fore, Style
import datetime
import logging
import unicodedata
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

logging.basicConfig(
    filename='conversion.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)

def print_colored(text, color=Fore.WHITE):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mensaje = f"{current_time} - {text}"
    print(f"{color}{mensaje}{Style.RESET_ALL}")
    logging.info(text)

def print_error(text):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mensaje = f"{current_time} - ERROR: {text}"
    print(f"{Fore.RED}{mensaje}{Style.RESET_ALL}")
    logging.error(text)

def remove_file(file_path):
    try:
        os.remove(file_path)
        logging.info(f"Eliminado archivo: {file_path}")
        return True
    except Exception as e:
        logging.error(f"Error al eliminar {file_path}: {e}")
        return False

def verificar_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logging.info("FFmpeg y FFprobe están instalados y accesibles.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_error("FFmpeg o FFprobe no están instalados o no están en el PATH.")
        logging.critical("FFmpeg o FFprobe no están instalados o no están en el PATH.")
        exit(1)

def verificar_h264_nvenc():
    comando = ['ffmpeg', '-codecs']
    try:
        result = subprocess.run(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        if 'h264_nvenc' in result.stdout:
            logging.info("h264_nvenc está disponible en FFmpeg.")
            return True
        else:
            logging.warning("h264_nvenc no está disponible en FFmpeg.")
            return False
    except subprocess.CalledProcessError:
        logging.warning("No se pudo verificar h264_nvenc en FFmpeg.")
        return False

def sanitize_filename(name):
    normalized = unicodedata.normalize('NFKD', name)
    ascii_bytes = normalized.encode('ASCII', 'ignore')
    ascii_str = ascii_bytes.decode('ASCII').replace(' ', '_')
    ascii_str = "".join(c for c in ascii_str if c.isalnum() or c in ("-", "_")).rstrip()
    return ascii_str

def obtener_duracion(ruta_mkv):
    comando = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        ruta_mkv
    ]
    try:
        duracion = float(subprocess.check_output(comando, stderr=subprocess.PIPE))
        logging.debug(f"Duración de {ruta_mkv}: {duracion} segundos.")
        return duracion
    except subprocess.CalledProcessError:
        logging.error(f"No se pudo obtener la duración de {ruta_mkv}.")
        return None

def generar_info_json(file_path, output_dir):
    comando = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-i", file_path
    ]
    try:
        result = subprocess.run(comando, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        info_json_path = os.path.join(output_dir, "info.json")
        with open(info_json_path, "w", encoding='utf-8') as f:
            json.dump(info, f, indent=4)
        logging.info(f"Generado info.json para {file_path}.")
        return info
    except subprocess.CalledProcessError as e:
        print_error(f"Error al generar info.json para {file_path}: {e.stderr}")
        logging.error(f"Error al generar info.json para {file_path}: {e.stderr}")
        return None

def ejecutar_comando_con_progreso(comando, descripcion, progress, task_id, duracion):
    try:
        proceso = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stderr_output = ""
        for line in proceso.stderr:
            stderr_output += line
            if 'time=' in line:
                try:
                    time_str = line.strip().split('time=')[1].split(' ')[0]
                    h, m, s = time_str.split(':')
                    tiempo = float(h) * 3600 + float(m) * 60 + float(s)
                    progress.update(task_id, completed=tiempo if tiempo <= duracion else duracion)
                except Exception as e:
                    logging.debug(f"Error al parsear tiempo de la línea: {line.strip()} - {e}")
        proceso.wait()
        progress.update(task_id, completed=duracion)
        if proceso.returncode != 0:
            print_error(f"Comando FFmpeg falló: {' '.join(comando)}\nErrores: {stderr_output}")
            logging.error(f"Comando FFmpeg falló: {' '.join(comando)}\nErrores: {stderr_output}")
            return False
        return True
    except Exception as e:
        print_error(f"Excepción al ejecutar comando FFmpeg: {e}")
        logging.error(f"Excepción al ejecutar comando FFmpeg: {e}")
        return False

def obtener_resolucion_original(file_path, track_id):
    comando = [
        "ffprobe",
        "-v", "error",
        "-select_streams", f"v:{track_id}",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        file_path
    ]
    try:
        output = subprocess.check_output(comando, stderr=subprocess.PIPE).decode().strip()
        width, height = map(int, output.split(','))
        logging.debug(f"Resolución original del video {track_id}: {width}x{height}")
        return width, height
    except subprocess.CalledProcessError:
        logging.error(f"No se pudo obtener la resolución del video {track_id}.")
        return None, None

def extraer_pista(file_path, stream, track_type, track_name, output_dir, usar_cuda, progress, track_id, duracion_total):
    sanitized_name = sanitize_filename(track_name)
    if track_type == "video":
        resoluciones = [240, 360, 480, 720, 1080, 2160]
        original_width, original_height = obtener_resolucion_original(file_path, track_id)
        if original_height:
            resoluciones = [res for res in resoluciones if res <= original_height]
        else:
            logging.warning(f"No se pudo obtener la altura del video {track_id}. Usando resoluciones por defecto.")
        track_output_dir = os.path.join(output_dir, f"{track_type}_{track_id}")
        os.makedirs(track_output_dir, exist_ok=True)
        hls_playlists = []
        for res in resoluciones:
            if original_width and original_height:
                aspect_ratio = original_width / original_height
                width = int(round(res * aspect_ratio / 2) * 2)
                if width <= 0:
                    width = -2
            else:
                width = -2
            hls_playlist = os.path.join(track_output_dir, f"{res}p.m3u8")
            bitrate_mapping = {
                240: 400,
                360: 800,
                480: 1200,
                720: 2500,
                1080: 5000,
                2160: 12000
            }
            bitrate = bitrate_mapping.get(res, res * 1000)
            scale_filter = f"scale={width}:{res}"
            cmd = [
                "ffmpeg",
                "-y",
                "-i", file_path,
                "-map", f"0:v:{track_id}",
                "-c:v", "h264_nvenc" if usar_cuda else "libx264",
                "-preset", "fast",
            ]
            if usar_cuda:
                cmd += [
                    "-rc:v", "vbr_hq",
                    "-b:v", f"{bitrate}k",
                    "-maxrate", f"{bitrate}k",
                    "-bufsize", f"{bitrate * 2}k",
                ]
            else:
                cmd += [
                    "-b:v", f"{bitrate}k",
                ]
            cmd += [
                "-vf", scale_filter,
                "-pix_fmt", "yuv420p",
                "-f", "hls",
                "-hls_time", "10",
                "-hls_playlist_type", "vod",
                "-hls_segment_filename", os.path.join(track_output_dir, f"segment_{res}p_%03d.ts"),
                hls_playlist
            ]
            duracion = duracion_total or 100
            task_id_progress = progress.add_task(f"Video {track_id} a {res}p", total=duracion)
            logging.debug(f"Ejecutando comando para video {track_id} a {res}p: {' '.join(cmd)}")
            success = ejecutar_comando_con_progreso(cmd, f"Video {track_id} a {res}p", progress, task_id_progress, duracion)
            if success:
                hls_playlists.append((os.path.relpath(hls_playlist, output_dir).replace(os.path.sep, '/'), width, res, bitrate * 1000))
                logging.info(f"HLS para video {track_id} a {res}p creado en {hls_playlist}.")
            else:
                logging.error(f"Falló la creación de HLS para video {track_id} a {res}p.")
        return hls_playlists
    elif track_type == "audio":
        track_output_dir = os.path.join(output_dir, f"{track_type}_{track_id}")
        os.makedirs(track_output_dir, exist_ok=True)
        hls_playlist = os.path.join(track_output_dir, "audio.m3u8")
        cmd = [
            "ffmpeg",
            "-y",
            "-i", file_path,
            "-map", f"0:a:{track_id}",
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "hls",
            "-hls_time", "10",
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", os.path.join(track_output_dir, "segment_audio_%03d.ts"),
            hls_playlist
        ]
        task_id_progress = progress.add_task(f"Audio {track_id}", total=duracion_total or 100)
        logging.debug(f"Ejecutando comando para audio {track_id}: {' '.join(cmd)}")
        success = ejecutar_comando_con_progreso(cmd, f"Audio {track_id}", progress, task_id_progress, duracion_total or 100)
        if success:
            language = stream.get("tags", {}).get("language") or "und"
            name = stream.get("tags", {}).get("title") or language or f"Audio_{track_id}"
            # Eliminamos la llamada a fix_encoding(name)
            logging.info(f"HLS para audio {track_id} creado en {hls_playlist} con nombre '{name}' y lenguaje '{language}'.")
            default = 'YES' if stream.get("disposition", {}).get("default") == 1 else 'NO'
            return (os.path.relpath(hls_playlist, output_dir).replace(os.path.sep, '/'), name, language, default)
        else:
            logging.error(f"Falló la creación de HLS para audio {track_id}.")
            return None
    elif track_type == "subtitle":
        track_output_dir = os.path.join(output_dir, f"{track_type}_{track_id}")
        os.makedirs(track_output_dir, exist_ok=True)
        vtt_file = os.path.join(track_output_dir, "subtitle.vtt")
        cmd = [
            "ffmpeg",
            "-y",
            "-i", file_path,
            "-map", f"0:s:{track_id}",
            "-c:s", "webvtt",
            "-f", "webvtt",
            vtt_file
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logging.info(f"Subtítulo {track_id} extraído en {vtt_file}.")
            subtitle_playlist = os.path.join(track_output_dir, "subtitle.m3u8")
            with open(subtitle_playlist, "w", encoding='utf-8') as sub_m3u8:
                sub_m3u8.write("#EXTM3U\n#EXT-X-VERSION:3\n")
                sub_m3u8.write("#EXT-X-TARGETDURATION:10\n")
                sub_m3u8.write("#EXT-X-MEDIA-SEQUENCE:0\n")
                sub_m3u8.write("#EXTINF:10.0,\n")
                sub_m3u8.write("subtitle.vtt\n")
                sub_m3u8.write("#EXT-X-ENDLIST\n")
            logging.info(f"Playlist de subtítulos creado en {subtitle_playlist}.")
            language = stream.get("tags", {}).get("language") or "und"
            name = stream.get("tags", {}).get("title") or language or f"Subtitle_{track_id}"
            # Eliminamos la llamada a fix_encoding(name)
            return (os.path.relpath(subtitle_playlist, output_dir).replace(os.path.sep, '/'), name, language)
        except subprocess.CalledProcessError as e:
            print_error(f"Error al extraer subtítulo {track_id}: {e.stderr}")
            logging.error(f"Error al extraer subtítulo {track_id}: {e.stderr}")
            return None
    return None

def generar_master_playlist(output_dir, video_playlists, audio_playlists, subtitle_playlists):
    master_playlist_path = os.path.join(output_dir, "master.m3u8")
    try:
        with open(master_playlist_path, "w", encoding='utf-8') as master_file:
            master_file.write("#EXTM3U\n#EXT-X-VERSION:3\n\n")
            for idx, (audio_playlist, name, language, default) in enumerate(audio_playlists):
                master_file.write(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{name}",LANGUAGE="{language}",DEFAULT={default},AUTOSELECT=YES,URI="{audio_playlist}"\n')
            for idx, (subtitle_playlist, name, language) in enumerate(subtitle_playlists):
                master_file.write(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{name}",LANGUAGE="{language}",DEFAULT=NO,AUTOSELECT=YES,URI="{subtitle_playlist}"\n')
            master_file.write("\n")
            for video_playlist in video_playlists:
                playlist_path, width, height, bandwidth = video_playlist
                master_file.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height},AUDIO="audio",SUBTITLES="subs"\n{playlist_path}\n')
        logging.info(f"Master playlist creado en {master_playlist_path}.")
        print_colored(f"Master playlist creado en {master_playlist_path}", Fore.GREEN)
    except Exception as e:
        print_error(f"Error al crear master.m3u8: {e}")
        logging.error(f"Error al crear master.m3u8: {e}")

def procesar_archivo(archivo_mkv, eliminar_archivos=False, usar_cuda=False, progress=None):
    directorio_actual = os.path.dirname(os.path.abspath(archivo_mkv))
    nombre_base = os.path.splitext(os.path.basename(archivo_mkv))[0]
    ruta_mkv = os.path.join(directorio_actual, archivo_mkv)
    output_dir = os.path.join(directorio_actual, nombre_base)
    os.makedirs(output_dir, exist_ok=True)
    print_colored(f"Processing file: {archivo_mkv}...", Fore.YELLOW)
    logging.info(f"Procesando archivo: {archivo_mkv}.")
    info = generar_info_json(ruta_mkv, output_dir)
    if not info:
        print_error(f"No se pudo generar info.json para {archivo_mkv}.")
        return
    streams = info.get("streams", [])
    video_playlists = []
    audio_playlists = []
    subtitle_playlists = []
    video_counter = 0
    audio_counter = 0
    subtitle_counter = 0
    duracion_total = float(info.get("format", {}).get("duration", 100))
    for stream in streams:
        track_type = stream.get("codec_type")
        if track_type == "video":
            track_id = video_counter
            video_counter += 1
        elif track_type == "audio":
            track_id = audio_counter
            audio_counter += 1
        elif track_type == "subtitle":
            track_id = subtitle_counter
            subtitle_counter += 1
        else:
            logging.warning(f"Pista con tipo {track_type} no soportada. Saltando.")
            continue
        track_name = stream.get("tags", {}).get("title") or stream.get("tags", {}).get("language") or "unknown"
        hls_playlist = extraer_pista(
            file_path=ruta_mkv,
            stream=stream,
            track_type=track_type,
            track_name=track_name,
            output_dir=output_dir,
            usar_cuda=usar_cuda,
            progress=progress,
            track_id=track_id,
            duracion_total=duracion_total
        )
        if hls_playlist:
            if track_type == "video":
                video_playlists.extend(hls_playlist)
            elif track_type == "audio":
                audio_playlists.append(hls_playlist)
            elif track_type == "subtitle":
                subtitle_playlists.append(hls_playlist)
    generar_master_playlist(output_dir, video_playlists, audio_playlists, subtitle_playlists)
    if eliminar_archivos:
        archivos_eliminar = [ruta_mkv]
        for playlist in [p[0] for p in video_playlists] + [p[0] for p in audio_playlists] + [p[0] for p in subtitle_playlists]:
            if os.path.splitext(playlist)[1].lower() not in ['.vtt', '.m3u8']:
                segmento_dir = os.path.join(output_dir, os.path.dirname(playlist))
                try:
                    for f in os.listdir(segmento_dir):
                        if f.endswith(".ts"):
                            archivos_eliminar.append(os.path.join(segmento_dir, f))
                except FileNotFoundError:
                    logging.warning(f"Directorio de segmentos no encontrado: {segmento_dir}")
        for archivo in archivos_eliminar:
            remove_file(archivo)
        print_colored("Archivos intermedios eliminados.", Fore.GREEN)
        logging.info("Archivos intermedios eliminados.")
    else:
        print_colored("Eliminación de archivos intermedios deshabilitada.", Fore.YELLOW)
        logging.info("Eliminación de archivos intermedios deshabilitada.")
    print_colored(f"Processing completed for {archivo_mkv}", Fore.GREEN)
    logging.info(f"Procesamiento completado para {archivo_mkv}.")

def main():
    verificar_ffmpeg()
    usar_cuda = verificar_h264_nvenc()
    if usar_cuda:
        print_colored("CUDA detected. Using h264_nvenc for video encoding.", Fore.GREEN)
        logging.info("CUDA detected. Using h264_nvenc for video encoding.")
    else:
        print_colored("CUDA not detected. Using libx264 for video encoding.", Fore.YELLOW)
        logging.info("CUDA not detected. Using libx264 for video encoding.")
    directorio_actual = os.getcwd()
    archivos_mkv = [f for f in os.listdir(directorio_actual) if f.lower().endswith(".mkv")]
    if not archivos_mkv:
        print_colored("No se encontraron archivos MKV en el directorio actual.", Fore.YELLOW)
        logging.info("No se encontraron archivos MKV para procesar.")
        return
    with Progress(
        TextColumn("[bold blue]{task.description}", justify="right"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        transient=True
    ) as progress:
        for archivo in archivos_mkv:
            try:
                procesar_archivo(archivo, eliminar_archivos=False, usar_cuda=usar_cuda, progress=progress)
            except Exception as e:
                print_error(f"Error al procesar el archivo {archivo}: {e}")
                logging.error(f"Error al procesar el archivo {archivo}: {e}")
    print_colored("MKV file queue completed, all files have been converted to HLS.", Fore.GREEN)
    logging.info("Todos los archivos MKV han sido convertidos a HLS.")

if __name__ == "__main__":
    main()
