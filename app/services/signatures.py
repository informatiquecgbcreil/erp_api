"""Validation des signatures de canevas avant stockage et génération Word."""
import base64
from io import BytesIO
from pathlib import Path
import uuid


def save_signature(data, directory, prefix):
    if not data:
        return None
    if len(data) > 180_000 or not data.startswith("data:image/png;base64,"):
        raise ValueError("La signature doit être une petite image PNG.")
    try:
        binary = base64.b64decode(data.split(",", 1)[1], validate=True)
        from PIL import Image
        with Image.open(BytesIO(binary)) as picture:
            if picture.format != "PNG" or not (1 <= picture.width <= 2048 and 1 <= picture.height <= 1024):
                raise ValueError()
            picture.verify()
    except Exception:
        raise ValueError("Signature invalide. Effacez-la et signez à nouveau.") from None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (prefix + "_" + uuid.uuid4().hex + ".png")
    target.write_bytes(binary)
    return str(target)
