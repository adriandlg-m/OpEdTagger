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
    clean = re.sub(r'\(\d{4}\)', '', folder_name)
    clean = re.sub(r'\[.*?\]', '', clean)
    return clean.strip()

def download_with_retry(link: str, file_path: Path, headers: dict, max_attempts: int = 3) -> bool:
    attempt = 0
    while attempt < max_attempts:
        try:
            attempt += 1
            req_headers = headers.copy()
            file_size = 0

            if file_path.exists():
                file_size = file_path.stat().st_size
                req_headers["Range"] = f"bytes={file_size}-"
                console.print(f"    -> Resuming download (Attempt {attempt}/{max_attempts}): {file_path.name} from byte {file_size}...")
            else:
                console.print(f"    -> Downloading (Attempt {attempt}/{max_attempts}): {file_path.name}...")

            vid_response = requests.get(link, headers=req_headers, impersonate="chrome120", timeout=60, stream=True)
            
            if vid_response.status_code == 206:
                mode = "ab"
            elif vid_response.status_code == 200:
                mode = "wb"
                file_size = 0
            elif vid_response.status_code == 416:
                console.print(f"       [green][✔] The file is already fully downloaded.[/green]")
                return True
            else:
                console.print(f"       [yellow]⚠ HTTP Error {vid_response.status_code} on attempt {attempt} for {file_path.name}.[/yellow]")
                if attempt < max_attempts:
                    time.sleep(2 ** attempt)
                continue

            with open(file_path, mode) as f:
                for chunk in vid_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        
            console.print(f"       [green][✔] Downloaded/Resumed successfully.[/green]")
            return True

        except Exception as e:
            console.print(f"       [yellow]⚠ Network exception on attempt {attempt} for {file_path.name}: {e}[/yellow]")
            
        if attempt < max_attempts:
            sleep_time = 2 ** attempt
            console.print(f"       [dim]Retrying in {sleep_time} seconds...[/dim]")
            time.sleep(sleep_time)

    while True:
        console.print(f"\n[bold yellow]⚠ Failed to complete '{file_path.name}' after {max_attempts} attempts.[/bold yellow]")
        console.print(" [1] Retry again")
        console.print(" [2] Skip this file and continue")
        console.print(" [3] Cancel the process entirely")
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="2")
        
        if choice == "1":
            return download_with_retry(link, file_path, headers, max_attempts=max_attempts)
        elif choice == "2":
            console.print(f"[yellow]⚠ File '{file_path.name}' skipped. Continuing...[/yellow]")
            return False
        elif choice == "3":
            console.print("[red]✖ Operation cancelled by user.[/red]")
            raise InterruptedError("Process cancelled by user due to network failures.")

def sync_anime_templates(folder_name: str, template_base_path: str = "template") -> bool:
    url = "https://rs.animethemes.moe/graphql"
    query_term = sanitize_query_from_folder(folder_name)
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://animethemes.moe",
        "Referer": "https://animethemes.moe/"
    }
    
    console.print(f"[*] Querying AnimeThemes API for '{query_term}'...")
    
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
            console.print(f"[bold red]✘ HTTP Error {response.status_code} while querying AnimeThemes.[/bold red]")
            return False
            
        result = response.json()
        anime_list = result.get("data", {}).get("search", {}).get("anime", {}).get("data", [])
        
        if not anime_list:
            console.print(f"[yellow]⚠ No results found on AnimeThemes for '{query_term}'.[/yellow]")
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
        
        for matched_anime in matched_animes:
            for theme in matched_anime.get("themes", []):
                theme_type = theme.get("type")
                
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
            console.print(f"[yellow]⚠ No themes (OP/ED) found in API for this series.[/yellow]")
            return False
            
        console.print(f"[*] Verifying .webm files for '{folder_name}' ({len(tasks)} themes in API)...")
        
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
        console.print(f"[bold red]✘ Error during template synchronization: {e}[/bold red]")
        return False