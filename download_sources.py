import os.path
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Iterable
import zipfile
import httpx

from rich.progress import (
  BarColumn,
  DownloadColumn,
  Progress,
  TaskID,
  TextColumn,
  TimeRemainingColumn,
  TransferSpeedColumn,
)

progress = Progress(
  TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
  BarColumn(bar_width=None),
  "[progress.percentage]{task.percentage:>3.1f}%",
  "•",
  DownloadColumn(),
  "•",
  TransferSpeedColumn(),
  "•",
  TimeRemainingColumn(),
)

done_event = Event()


def copy_url(client: httpx.Client, task_id: TaskID, url: str, dest_dir: str) -> None:
  try:
    filename = url.split("/")[-1] or "archive.zip"

    with client.stream("GET", url, follow_redirects=True) as response:
      if response.status_code != 200:
        progress.console.log(f"[red]Error {response.status_code} for {url}[/red]")
        return

      total_length = response.headers.get("Content-Length")
      if total_length is not None:
        progress.update(task_id, total=int(total_length))

      temp_zip_path = os.path.join(dest_dir, f"temp_{filename}")

      with open(temp_zip_path, "wb") as dest_file:
        progress.start_task(task_id)
        for chunk in response.iter_bytes(chunk_size=32768):
          if done_event.is_set():
            try:
              os.remove(temp_zip_path)
            except OSError:
              pass
            return
          dest_file.write(chunk)
          progress.update(task_id, advance=len(chunk))

    if zipfile.is_zipfile(temp_zip_path):
      with zipfile.ZipFile(temp_zip_path, "r") as z:
        for file_info in z.infolist():
          if file_info.filename.endswith(".txt"):
            target = os.path.join(dest_dir, os.path.basename(file_info.filename))
            tmp_target = target + ".part"
            with z.open(file_info) as src, open(tmp_target, "wb") as out:
              shutil.copyfileobj(src, out)
            os.replace(tmp_target, target)
            progress.console.log(f"Extracted {file_info.filename} to {dest_dir}")
      os.remove(temp_zip_path)
    else:
      progress.console.log(
        f"[red]Downloaded file {temp_zip_path} is not a valid zip archive.[/red]"
      )

  except Exception as e:
    progress.console.log(f"[red]Failed to process {url}: {e}[/red]")


def download(urls: Iterable[str], dest_dir: str):
  """Download and extract multiple zip files to the given directory."""
  with progress:
    with httpx.Client() as client:
      with ThreadPoolExecutor(max_workers=4) as pool:
        for url in urls:
          filename = url.split("/")[-1] or "archive.zip"
          task_id = progress.add_task("download", filename=filename, start=False)
          pool.submit(copy_url, client, task_id, url, dest_dir)


if __name__ == "__main__":
  TARGET_URLS = [
    "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002321/valeursfoncieres-2025.txt.zip",
    "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002306/valeursfoncieres-2024.txt.zip",
    "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002251/valeursfoncieres-2023.txt.zip",
    "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002236/valeursfoncieres-2022.txt.zip",
  ]
  download(TARGET_URLS, "./")
