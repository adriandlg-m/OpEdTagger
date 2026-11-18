# src/downloader.py
import os
import re
import time
from pathlib import Path
from curl_cffi import requests
from rich.console import Console
from rich.prompt import Prompt

console = Console()

def sanitize_query_from_folder(folder_name: str) -> str:
    """Limpia el nombre de la carpeta de Jellyfin (ej. 'DAN DA DAN (2024)') para buscar en la API."""
    clean = re.sub(r'\(\d{4}\)', '', folder_name)
    clean = re.sub(r'\[.*?\]', '', clean)
    return clean.strip()

def download_with_retry(link: str, file_path: Path, headers: dict, max_attempts: int = 3) -> bool:
    """
    Intenta descargar o reanudar un archivo webm mediante peticiones con rango HTTP (Range Requests).
    Si el archivo parcial ya existe en disco, continúa descargando desde el último byte guardado.
    """
    attempt = 0
    while attempt < max_attempts:
        try:
            attempt += 1
            req_headers = headers.copy()
            file_size = 0

            # Verificar si existe un archivo parcial para reanudar la descarga
            if file_path.exists():
                file_size = file_path.stat().st_size
                req_headers["Range"] = f"bytes={file_size}-"
                console.print(f"    -> Reanudando descarga (Intento {attempt}/{max_attempts}): {file_path.name} desde el byte {file_size}...")
            else:
                console.print(f"    -> Descargando (Intento {attempt}/{max_attempts}): {file_path.name}...")

            # Timeout generoso de 60 segundos para evitar cortes prematuros en archivos pesados
            vid_response = requests.get(link, headers=req_headers, impersonate="chrome120", timeout=60, stream=True)
            
            # Código 206 = Partial Content (Soporta reanudación por rangos)
            # Código 200 = OK (El servidor envía el archivo completo desde el inicio)
            if vid_response.status_code == 206:
                mode = "ab"  # Adjuntar bytes al final del archivo existente
            elif vid_response.status_code == 200:
                mode = "wb"  # Sobrescribir desde cero si el servidor no aceptó el rango
                file_size = 0
            elif vid_response.status_code == 416:  # Range Not Satisfiable (el archivo ya está completo)
                console.print(f"       [green][✔] El archivo ya está completamente descargado.[/green]")
                return True
            else:
                console.print(f"       [yellow]⚠ Error HTTP {vid_response.status_code} en intento {attempt} para {file_path.name}.[/yellow]")
                if attempt < max_attempts:
                    time.sleep(2 ** attempt)
                continue

            # Escritura en disco por bloques optimizada
            with open(file_path, mode) as f:
                for chunk in vid_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        
            console.print(f"       [green][✔] Descargado/Reanudado con éxito.[/green]")
            return True

        except Exception as e:
            console.print(f"       [yellow]⚠ Excepción de red en intento {attempt} para {file_path.name}: {e}[/yellow]")
            
        if attempt < max_attempts:
            sleep_time = 2 ** attempt  # Backoff exponencial (2s, 4s, 8s...)
            console.print(f"       [dim]Reintentando en {sleep_time} segundos...[/dim]")
            time.sleep(sleep_time)

    # Si se agotan los intentos, se abre menú interactivo de control
    while True:
        console.print(f"\n[bold yellow]⚠ No se pudo completar '{file_path.name}' tras {max_attempts} intentos.[/bold yellow]")
        console.print(" [1] Reintentar nuevamente")
        console.print(" [2] Omitir este archivo y continuar")
        console.print(" [3] Cancelar el proceso por completo")
        
        choice = Prompt.ask("Selecciona una opción", choices=["1", "2", "3"], default="2")
        
        if choice == "1":
            return download_with_retry(link, file_path, headers, max_attempts=max_attempts)
        elif choice == "2":
            console.print(f"[yellow]⚠ Archivo '{file_path.name}' omitido. Continuando...[/yellow]")
            return False
        elif choice == "3":
            console.print("[red]✖ Operación cancelada por el usuario.[/red]")
            raise InterruptedError("Proceso cancelado por el usuario debido a fallos de red.")

def sync_anime_templates(folder_name: str, template_base_path: str = "template") -> bool:
    """
    Verifica los temas de la serie en la API de AnimeThemes, descarga los .webm faltantes
    con reintentos y soporte de reanudación, y los organiza en template/{folder_name}/video_files/.
    """
    url = "https://rs.animethemes.moe/graphql"
    query_term = sanitize_query_from_folder(folder_name)
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://animethemes.moe",
        "Referer": "https://animethemes.moe/"
    }
    
    console.print(f"[*] Consultando API de AnimeThemes para '{query_term}'...")
    
    query = """
    query ($searchTerm: String!) {
      search(search: $searchTerm, first: 10, page: 1) {
        anime {
          data {
            slug
            title {
              romaji
              english
            }
            themes {
              type
              sequence
              slug
              entries {
                videos {
                  nodes {
                    link
                    resolution
                    filename
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    
    payload = {
        "query": query,
        "variables": {"searchTerm": query_term}
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, impersonate="chrome120")
        if response.status_code != 200:
            console.print(f"[bold red]✘ Error HTTP {response.status_code} al consultar AnimeThemes.[/bold red]")
            return False
            
        result = response.json()
        anime_list = result.get("data", {}).get("search", {}).get("anime", {}).get("data", [])
        
        if not anime_list:
            console.print(f"[yellow]⚠ No se encontraron resultados en AnimeThemes para '{query_term}'.[/yellow]")
            return False
            
        query_clean = query_term.replace(" ", "").lower()
        matched_animes = [
            anime for anime in anime_list 
            if query_clean in (anime.get("slug") or "").lower() or 
               query_clean in (anime.get("title", {}).get("romaji") or "").replace(" ", "").lower()
        ]
        
        if not matched_animes:
            matched_animes = anime_list
            
        output_dir = Path(template_base_path) / folder_name / "video_files"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        all_ops = []
        all_eds = []
        
        # Unificar temporadas de la franquicia
        for matched_anime in matched_animes:
            for theme in matched_anime.get("themes", []):
                theme_type = theme.get("type") # OP o ED
                
                best_video = None
                best_score = -1
                
                for entry in theme.get("entries", []):
                    for video_node in entry.get("videos", {}).get("nodes", []):
                        link = video_node.get("link")
                        if not link:
                            continue
                        filename = video_node.get("filename") or link.split("/")[-1]
                        resolution = video_node.get("resolution") or 0
                        
                        is_nc = 1 if ("NCBD" in filename.upper() or "NC" in filename.upper()) else 0
                        score = (is_nc * 10000) + resolution
                        
                        if score > best_score:
                            best_score = score
                            best_video = {
                                "link": link,
                                "resolution": resolution
                            }
                
                if best_video:
                    item = {
                        "link": best_video["link"],
                        "resolution": best_video["resolution"]
                    }
                    if theme_type == "OP":
                        all_ops.append(item)
                    elif theme_type == "ED":
                        all_eds.append(item)

        tasks = []
        for idx, op in enumerate(all_ops, start=1):
            tasks.append((op["link"], "OP", idx, op["resolution"]))
            
        for idx, ed in enumerate(all_eds, start=1):
            tasks.append((ed["link"], "ED", idx, ed["resolution"]))
            
        if not tasks:
            console.print(f"[yellow]⚠ No se encontraron temas (OP/ED) en la API para esta serie.[/yellow]")
            return False
            
        console.print(f"[*] Verificando archivos .webm para '{folder_name}' ({len(tasks)} temas en API)...")
        
        for link, t_type, t_seq, resolution in tasks:
            formatted_filename = f"{folder_name} - {t_type}{t_seq}.webm"
            file_path = output_dir / formatted_filename
            
            if file_path.exists() and file_path.stat().st_size > 0:
                pass
                
            download_with_retry(link, file_path, headers)
                
        return True
        
    except InterruptedError:
        raise
    except Exception as e:
        console.print(f"[bold red]✘ Error durante la sincronización de plantillas: {e}[/bold red]")
        return False