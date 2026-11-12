"""Respaldo y restauración de las tablas del catálogo, sin ejecutar SQL del ZIP."""
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import time
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from imagenes import guardar_foto

MAX_ZIP = 100 * 1024 * 1024
MAX_EXPANDED = 300 * 1024 * 1024
MAX_DATABASE = 32 * 1024 * 1024
MAX_MEMBERS = 20000
TTL = 30 * 60
COLUMNS = {
    'catalogo_categorias': ('id', 'nombre'),
    'catalogo_productos': ('id', 'nombre', 'descripcion', 'precio', 'stock', 'categoria_id', 'enlace_ml', 'activo', 'destacado', 'fecha_creacion'),
    'catalogo_fotos': ('id', 'producto_id', 'archivo', 'orden'),
    'catalogo_configuracion': ('clave', 'valor'),
    'catalogo_portadas': ('id', 'archivo', 'titulo', 'orden', 'activo'),
    'catalogo_ml_items': ('item_id','producto_id','seller_id','estado','actualizado'),
    'catalogo_ml_fotos': ('id','producto_id','url','orden'),
}


class StorageLock:
    """Un mismo cerrojo para escritores, descargas y restauraciones, incluso entre procesos."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self, timeout=25):
        self.file = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.release()
                    raise TimeoutError('Hay otra operación en curso. Espera y vuelve a intentar.')
                time.sleep(0.05)

    def __enter__(self):
        if self.file is None:
            self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def release(self):
        if self.file is not None:
            self.file.close()  # Cerrar libera el bloqueo del sistema operativo.
            self.file = None


def safe_photo(name):
    return isinstance(name, str) and '..' not in name and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}\.(?:webp|png|jpg|jpeg)', name) is not None


def stage_path(data, token):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('No hay una revisión pendiente. Selecciona nuevamente el respaldo.')
    return Path(data) / '.restauraciones' / token


def discard_stage(data, token):
    if token:
        shutil.rmtree(stage_path(data, token), ignore_errors=True)


def read_catalog(database):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        deadline = time.monotonic() + 8
        connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        schema = connection.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if any(row['type'] != 'table' or not row['sql'] or 'VIRTUAL TABLE' in row['sql'].upper() for row in schema):
            raise ValueError('La base contiene estructuras no compatibles con este catálogo.')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('La base de datos está dañada.')
        from reportes import TABLES
        rows = {}
        for table, columns in {**COLUMNS, **TABLES}.items():
            if table in TABLES and not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                continue
            actual = tuple(row['name'] for row in connection.execute(f'PRAGMA table_info({table})'))
            if table in {'catalogo_portadas','catalogo_ml_items','catalogo_ml_fotos'} and not actual:
                rows[table] = []  # Compatibilidad con respaldos anteriores al menú de portadas.
                continue
            if actual != columns:
                raise ValueError('El respaldo no corresponde a esta versión del catálogo.')
            limit = 100000 if table in TABLES else (16000 if table in {'catalogo_fotos','catalogo_ml_fotos'} else 5000)
            result = connection.execute(f'SELECT * FROM {table} LIMIT ?', (limit + 1,)).fetchall()
            if len(result) > limit:
                raise ValueError('El respaldo supera la cantidad de registros admitida.')
            rows[table] = [dict(row) for row in result]
    except sqlite3.Error as error:
        raise ValueError('No se pudo leer una base SQLite válida y compatible.') from error
    finally:
        connection.close()
    validate_rows(rows)
    return rows


def validate_rows(rows):
    def integer(value, minimum=0, maximum=2**63-1):
        return type(value) is int and minimum <= value <= maximum
    def text(value, maximum, minimum=0):
        return isinstance(value, str) and minimum <= len(value.strip()) <= maximum
    categories = rows['catalogo_categorias']
    products = rows['catalogo_productos']
    photos = rows['catalogo_fotos']
    ids = {row['id'] for row in categories}
    names = [row['nombre'].casefold() for row in categories if isinstance(row['nombre'], str)]
    if len(ids) != len(categories) or len(set(names)) != len(categories) or any(not integer(c['id'],1) or not text(c['nombre'],80,1) for c in categories):
        raise ValueError('Las categorías del respaldo no son válidas.')
    product_ids = {row['id'] for row in products}
    if len(product_ids) != len(products):
        raise ValueError('El respaldo contiene productos duplicados.')
    for p in products:
        valid = integer(p['id'],1) and text(p['nombre'],120,1) and text(p['descripcion'],4000) and integer(p['precio'],0,999999999) and integer(p['stock'],0,999999) and p['categoria_id'] in ids and p['activo'] in (0,1) and p['destacado'] in (0,1) and text(p['fecha_creacion'],64,1) and text(p['enlace_ml'],2000)
        if not valid:
            raise ValueError('Uno de los productos contiene datos no válidos.')
        if p['enlace_ml']:
            try:
                link = urlsplit(p['enlace_ml'])
                allowed = link.scheme == 'https' and not link.username and not link.password and link.port in (None,443) and (link.hostname in {'mercadolibre.cl','mercadolibre.com'} or (link.hostname or '').endswith('.mercadolibre.cl'))
            except ValueError:
                allowed = False
            if not allowed:
                raise ValueError('El respaldo contiene un enlace de Mercado Libre no válido.')
    files, photo_ids, counts = set(), set(), {}
    for photo in photos:
        name = photo['archivo']
        if not integer(photo['id'],1) or photo['id'] in photo_ids or photo['producto_id'] not in product_ids or not safe_photo(name) or name in files or not integer(photo['orden'],-2**31,2**31-1):
            raise ValueError('La galería del respaldo no es válida.')
        photo_ids.add(photo['id'])
        files.add(name)
        counts[photo['producto_id']] = counts.get(photo['producto_id'],0) + 1
        if counts[photo['producto_id']] > 8:
            raise ValueError('Un producto tiene más de 8 fotos.')
    covers = rows.get('catalogo_portadas', [])
    cover_ids = set()
    if len(covers) > 10:
        raise ValueError('El respaldo contiene más de 10 fotos de portada.')
    for cover in covers:
        if not integer(cover['id'],1) or cover['id'] in cover_ids or not safe_photo(cover['archivo']) or cover['archivo'] in files or not text(cover['titulo'],100) or not integer(cover['orden'],-2**31,2**31-1) or type(cover['activo']) is not int or cover['activo'] not in (0,1):
            raise ValueError('Las fotos de portada del respaldo no son válidas.')
        cover_ids.add(cover['id'])
        files.add(cover['archivo'])
    linked_products, linked_ids = set(), set()
    for item in rows.get('catalogo_ml_items', []):
        if not isinstance(item['item_id'],str) or not re.fullmatch(r'MLC[0-9]{6,20}',item['item_id']) or item['item_id'] in linked_ids or item['producto_id'] not in product_ids or item['producto_id'] in linked_products or not isinstance(item['seller_id'],str) or not re.fullmatch(r'[0-9]{1,25}',item['seller_id']) or not text(item['estado'],40) or not text(item['actualizado'],64,1):
            raise ValueError('Las publicaciones vinculadas a Mercado Libre no son válidas.')
        linked_ids.add(item['item_id'])
        linked_products.add(item['producto_id'])
    remote_ids, remote_counts = set(), {}
    for photo in rows.get('catalogo_ml_fotos', []):
        try:
            link = urlsplit(photo['url'])
            valid_url = link.scheme == 'https' and link.hostname == 'http2.mlstatic.com' and link.port in (None,443) and not link.username and not link.password and link.path.startswith('/D_') and not link.fragment and len(photo['url']) <= 2000
        except (ValueError,TypeError):
            valid_url = False
        if not integer(photo['id'],1) or photo['id'] in remote_ids or photo['producto_id'] not in linked_products or not valid_url or not integer(photo['orden'],0,49):
            raise ValueError('Las fotos remotas de Mercado Libre no son válidas.')
        remote_ids.add(photo['id'])
        remote_counts[photo['producto_id']] = remote_counts.get(photo['producto_id'],0) + 1
        if remote_counts[photo['producto_id']] > 50:
            raise ValueError('La publicación supera las 50 fotos remotas.')
    from reportes import validate_backup
    validate_backup(rows)
    config = {r['clave']:r['valor'] for r in rows['catalogo_configuracion']}
    if set(config) != {'nombre','descripcion','whatsapp'} or len(config) != len(rows['catalogo_configuracion']) or not text(config['nombre'],60,1) or not text(config['descripcion'],220) or not isinstance(config['whatsapp'],str) or (config['whatsapp'] and not re.fullmatch(r'[1-9][0-9]{7,14}',config['whatsapp'])):
        raise ValueError('La configuración del catálogo no es válida.')


def review_backup(stream, data):
    base = Path(data) / '.restauraciones'
    base.mkdir(exist_ok=True)
    for old in base.iterdir():
        if old.is_dir() and time.time() - old.stat().st_mtime > TTL:
            shutil.rmtree(old, ignore_errors=True)
    token = secrets.token_hex(16)
    folder = stage_path(data, token)
    folder.mkdir()
    (folder / 'fotos').mkdir()
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise ValueError('El contenido del ZIP supera el límite de 300 MB o de archivos.')
            members = {}
            for info in infos:
                name = info.filename
                parts = PurePosixPath(name).parts
                if name in members or '\\' in name or name.startswith('/') or '..' in parts or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError('El ZIP contiene rutas, archivos duplicados o enlaces no permitidos.')
                if info.is_dir():
                    if name not in {'data/','data/uploads/'}:
                        raise ValueError('El ZIP tiene carpetas no compatibles.')
                    continue
                if name != 'data/practica.db' and not (name.startswith('data/uploads/') and safe_photo(name.removeprefix('data/uploads/'))):
                    raise ValueError('Usa el ZIP generado con Descargar respaldo, sin modificar su estructura.')
                members[name] = info
            if 'data/practica.db' not in members or members['data/practica.db'].file_size > MAX_DATABASE:
                raise ValueError('Falta data/practica.db o la base supera los 32 MB.')
            database = folder / 'practica.db'
            with archive.open(members['data/practica.db']) as source, database.open('wb') as target:
                shutil.copyfileobj(source, target, 65536)
            rows = read_catalog(database)
            for photo in rows['catalogo_fotos'] + rows.get('catalogo_portadas', []):
                member = members.get('data/uploads/' + photo['archivo'])
                if member is None:
                    raise ValueError('Falta una foto referenciada por la base. El respaldo está incompleto.')
                if member.file_size > 6 * 1024 * 1024:
                    raise ValueError('Una foto del respaldo supera los 6 MB.')
                with archive.open(member) as source:
                    photo['archivo'] = guardar_foto(source, folder / 'fotos')
        summary = {'ventas':len(rows.get('negocio_ventas',[])), 'gastos':len(rows.get('negocio_gastos',[])), 'incluye_reportes':'negocio_ventas' in rows, 'productos':len(rows['catalogo_productos']), 'categorias':len(rows['catalogo_categorias']), 'fotos':len(rows['catalogo_fotos']), 'portadas':len(rows.get('catalogo_portadas', [])), 'fotos_ml':len(rows.get('catalogo_ml_fotos', [])), 'publicaciones_ml':len(rows.get('catalogo_ml_items', [])), 'nombre':dict((r['clave'],r['valor']) for r in rows['catalogo_configuracion'])['nombre']}
        manifest = {'rows':rows,'summary':summary,'created':time.time()}
        (folder / 'revision.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        return token, summary
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, KeyError, TypeError, EOFError, zlib.error) as error:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(error,ValueError):
            raise
        raise ValueError('No se pudo revisar el respaldo. Selecciona un ZIP válido generado por el catálogo.') from error


def load_review(data, token):
    folder = stage_path(data, token)
    try:
        manifest = json.loads((folder / 'revision.json').read_text(encoding='utf-8'))
        if time.time() - manifest['created'] > TTL:
            raise ValueError('La revisión venció. Selecciona nuevamente el respaldo.')
        validate_rows(manifest['rows'])
        for photo in manifest['rows']['catalogo_fotos'] + manifest['rows'].get('catalogo_portadas', []):
            if not (folder / 'fotos' / photo['archivo']).is_file():
                raise ValueError('La revisión está incompleta. Sube nuevamente el ZIP.')
        return manifest
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as error:
        raise ValueError('No hay una revisión válida pendiente. Sube nuevamente el ZIP.') from error


def make_backup(database, uploads, destination):
    """Debe llamarse con StorageLock adquirido para coordinar fotos y SQLite."""
    snapshot = Path(destination).with_suffix('.sqlite.tmp')
    source = sqlite3.connect(database, timeout=20)
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
        filenames = [row[0] for row in target.execute('SELECT archivo FROM catalogo_fotos')]
        if target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalogo_portadas'").fetchone():
            filenames += [row[0] for row in target.execute('SELECT archivo FROM catalogo_portadas')]
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'data/practica.db')
            for filename in filenames:
                if not safe_photo(filename) or not (Path(uploads)/filename).is_file():
                    raise ValueError('Falta una foto del catálogo actual. No se pudo crear el respaldo de seguridad.')
                archive.write(Path(uploads)/filename,'data/uploads/'+filename)
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise
    finally:
        source.close()
        target.close()
        snapshot.unlink(missing_ok=True)


def apply_review(database, data, token):
    """Restaura únicamente datos de catálogo en una transacción; no reemplaza .env ni personas."""
    data = Path(data)
    folder = stage_path(data,token)
    manifest = load_review(data,token)
    rows = manifest['rows']
    backups = data / 'respaldos_automaticos'
    backups.mkdir(exist_ok=True)
    name = 'antes_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + secrets.token_hex(4) + '.zip'
    make_backup(database,data/'uploads',backups/name)
    placed = []
    original_files = []
    conn = sqlite3.connect(database,timeout=20)
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        for photo in rows['catalogo_fotos'] + rows.get('catalogo_portadas', []):
            name_photo = photo['archivo']
            destination = data / 'uploads' / name_photo
            # Nombres aleatorios nuevos: nunca se sobrescribe una foto anterior.
            with destination.open('xb') as target, (folder/'fotos'/name_photo).open('rb') as source:
                placed.append(destination)
                shutil.copyfileobj(source,target)
        conn.execute('BEGIN IMMEDIATE')
        original_files = [r[0] for r in conn.execute('SELECT archivo FROM catalogo_fotos UNION SELECT archivo FROM catalogo_portadas')]
        from reportes import TABLES
        financial_order=('negocio_ventas','negocio_lineas','negocio_envios','negocio_costos','negocio_gastos','catalogo_metricas','negocio_envio_detalle','negocio_flex_cargos','negocio_flex_control')
        for table in reversed(financial_order):
            if table in rows:
                conn.execute(f'DELETE FROM {table}')
        for table in ('catalogo_ml_fotos','catalogo_ml_items','catalogo_portadas','catalogo_fotos','catalogo_productos','catalogo_categorias','catalogo_configuracion'):
            conn.execute(f'DELETE FROM {table}')
        for table in ('catalogo_categorias','catalogo_productos','catalogo_fotos','catalogo_configuracion','catalogo_portadas','catalogo_ml_items','catalogo_ml_fotos'):
            columns = COLUMNS[table]
            placeholders = ','.join('?' for _ in columns)
            conn.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", [[row[c] for c in columns] for row in rows.get(table, [])])
        for table in financial_order:
            if table in rows:
                columns=TABLES[table]
                placeholders=','.join('?' for _ in columns)
                conn.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",[[row[c] for c in columns] for row in rows[table]])
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Hay relaciones de datos inconsistentes. No se aplicó la restauración.')
        conn.commit()
    except Exception:
        conn.rollback()
        for file in placed:
            file.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    # El ZIP conserva la copia anterior; si un archivo está abierto en Windows se conserva.
    for old_name in original_files:
        if safe_photo(old_name):
            try:
                (data / 'uploads' / old_name).unlink(missing_ok=True)
            except OSError:
                pass
    discard_stage(data,token)
    return name,manifest['summary']


def previous_backups(data):
    folder = Path(data)/'respaldos_automaticos'
    return sorted([p.name for p in folder.glob('antes_*.zip') if re.fullmatch(r'antes_\d{8}T\d{6}_[a-f0-9]{8}\.zip',p.name)],reverse=True)[:10]
