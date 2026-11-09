"""Descarga limitada a las fotos del CDN de Mercado Libre; no acepta publicaciones."""
from io import BytesIO
import secrets
import socket
import warnings
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 6 * 1024 * 1024


class SinRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def descargar_foto(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname == 'http2.mlstatic.com'
                 and parts.port in (None, 443) and not parts.username and not parts.password
                 and parts.path.startswith('/D_') and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Pega la dirección directa de una foto: https://http2.mlstatic.com/D_...')
    req = Request(url, headers={'User-Agent': 'CatalogoFlask/1.0', 'Accept': 'image/webp,image/png,image/jpeg'})
    try:
        with build_opener(SinRedirecciones()).open(req, timeout=12) as response:
            content = bytearray()
            while True:
                chunk = response.read(min(65536, MAX_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError('La foto del enlace supera los 6 MB. Usa una versión más pequeña.')
    except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
        raise ValueError('No pudimos descargar la foto. Revisa el enlace o sube la imagen desde tu computador.') from error
    return BytesIO(content)


def guardar_foto(stream, uploads):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(stream) as picture:
                if picture.format not in {'JPEG', 'PNG', 'WEBP'} or picture.width * picture.height > 20000000:
                    raise ValueError()
                picture.load()
                picture = ImageOps.exif_transpose(picture)
                picture.thumbnail((1400, 1400))
                if picture.mode in {'RGBA', 'LA'} or (picture.mode == 'P' and 'transparency' in picture.info):
                    rgba = picture.convert('RGBA')
                    white = Image.new('RGBA', rgba.size, 'white')
                    picture = Image.alpha_composite(white, rgba)
                filename = secrets.token_hex(16) + '.webp'
                # RGB y recodificación: no conserva archivos remotos ejecutables ni metadatos.
                picture.convert('RGB').save(uploads / filename, 'WEBP', quality=88)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('La foto debe ser JPG, PNG o WebP y tener hasta 20 megapíxeles.') from error
    return filename
