"""Validation des signatures de canevas avant stockage et génération Word."""
import base64
from io import BytesIO
from pathlib import Path
import uuid


def _sans_encre(picture) -> bool:
    """Vrai si le cadre est vierge : aucun pixel nettement plus foncé que le fond.

    Le cadre du kiosque est peint en blanc (ou laissé transparent) ; un trait,
    même léger, descend bien en dessous de ce seuil.
    """
    from PIL import Image
    image = picture.convert("RGBA")
    fond = Image.new("RGBA", image.size, (255, 255, 255, 255))
    gris = Image.alpha_composite(fond, image).convert("L")
    return gris.getextrema()[0] > 200


def save_signature(data, directory, prefix, *, vide_autorise=False):
    """Enregistre la signature et renvoie son chemin.

    Cadre vierge : erreur (kiosque, signature à distance), ou ``None`` avec
    ``vide_autorise=True`` (pointage par le personnel, signature facultative).
    """
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
        with Image.open(BytesIO(binary)) as picture:  # verify() rend l'image inutilisable
            vierge = _sans_encre(picture)
    except Exception:
        raise ValueError("Signature invalide. Effacez-la et signez à nouveau.") from None
    if vierge:
        if vide_autorise:
            return None
        raise ValueError("La signature est vide : signez dans le cadre avant de valider.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (prefix + "_" + uuid.uuid4().hex + ".png")
    target.write_bytes(binary)
    return str(target)
