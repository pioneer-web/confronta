import shutil
import zipfile
from pathlib import Path
from .exceptions import SecurityValidationError


def extract_zip_safely(zip_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (destination / info.filename).resolve()
            if destination not in target.parents:
                raise SecurityValidationError(f'Tentativa de extração fora da área autorizada: {info.filename}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, 'r') as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst)
    return destination
