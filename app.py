"""Catálogo Flask. Mantiene practica.db y la tabla personas del proyecto original."""
import os
import re
import secrets
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_file, send_from_directory, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from imagenes import descargar_foto, guardar_foto

load_dotenv(Path(__file__).with_name('.env'))


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),
        ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH', ''),
        DATA_DIR=os.environ.get('DATA_DIR', str(Path(__file__).parent / 'data')),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1' if os.environ.get('RENDER') else '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if test_config:
        app.config.update(test_config)
    data = Path(app.config['DATA_DIR'])
    uploads = data / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    app.config['DATABASE'] = str(data / 'practica.db')
    if os.environ.get('TRUST_RENDER_PROXY') == '1':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    limiter = Limiter(get_remote_address, app=app, storage_uri='memory://')

    @contextmanager
    def db():
        conn = sqlite3.connect(app.config['DATABASE'], timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS catalogo_categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE);
            CREATE TABLE IF NOT EXISTS catalogo_productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT '',
                precio INTEGER NOT NULL CHECK(precio >= 0),
                stock INTEGER NOT NULL CHECK(stock >= 0),
                categoria_id INTEGER NOT NULL REFERENCES catalogo_categorias(id),
                enlace_ml TEXT NOT NULL DEFAULT '',
                activo INTEGER NOT NULL DEFAULT 1,
                destacado INTEGER NOT NULL DEFAULT 0,
                fecha_creacion TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS catalogo_fotos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL REFERENCES catalogo_productos(id) ON DELETE CASCADE,
                archivo TEXT NOT NULL UNIQUE, orden INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS catalogo_configuracion (
                clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
        ''')
        if conn.execute('SELECT COUNT(*) FROM catalogo_categorias').fetchone()[0] == 0:
            for category in ('Vestuario', 'Accesorios', 'Disfraces'):
                conn.execute('INSERT INTO catalogo_categorias(nombre) VALUES (?)', (category,))
        for key, value in {'nombre': 'Catálogo', 'whatsapp': '', 'descripcion': 'Prendas, accesorios y detalles para encontrar tu próximo favorito.'}.items():
            conn.execute('INSERT OR IGNORE INTO catalogo_configuracion VALUES (?,?)', (key, value))

    def settings():
        with db() as conn:
            return dict(conn.execute('SELECT clave, valor FROM catalogo_configuracion').fetchall())

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    @app.before_request
    def csrf_check():
        if request.method == 'POST':
            expected = session.get('csrf', '')
            actual = request.form.get('csrf_token', '')
            if not expected or not secrets.compare_digest(actual, expected):
                abort(400, 'La sesión venció. Recarga la página y vuelve a intentarlo.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if request.path.startswith('/admin'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.template_filter('clp')
    def clp(value):
        return '$' + f'{int(value):,}'.replace(',', '.')

    def wa_link(product=None):
        phone = settings()['whatsapp']
        if not phone:
            return None
        message = 'Hola, quiero hacer una consulta sobre el catálogo.'
        if product is not None:
            message = f"Hola, me interesa {product['nombre']} (CAT-{product['id']:04d}), precio {clp(product['precio'])}. ¿Está disponible? " + url_for('product', product_id=product['id'], _external=True)
        return f'https://wa.me/{phone}?' + urlencode({'text': message})

    @app.context_processor
    def context():
        return {'config': settings(), 'csrf_token': csrf, 'wa_link': wa_link, 'is_admin': bool(session.get('admin'))}

    def private(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapped

    def categories():
        with db() as conn:
            return conn.execute('SELECT * FROM catalogo_categorias ORDER BY nombre').fetchall()

    def find_product(product_id, public=False):
        with db() as conn:
            item = conn.execute('''SELECT p.*, c.nombre categoria FROM catalogo_productos p
                JOIN catalogo_categorias c ON c.id=p.categoria_id WHERE p.id=?''', (product_id,)).fetchone()
            photos = conn.execute('SELECT * FROM catalogo_fotos WHERE producto_id=? ORDER BY orden,id', (product_id,)).fetchall()
        if item is None or (public and not item['activo']):
            abort(404)
        return item, photos

    @app.get('/')
    def home():
        return redirect(url_for('catalog'))

    @app.get('/catalogo')
    def catalog():
        q = request.args.get('q', '').strip()[:100]
        category = request.args.get('categoria', '', type=str)
        sort = request.args.get('orden', 'novedades')
        order = {'menor': 'p.precio ASC,p.id DESC', 'mayor': 'p.precio DESC,p.id DESC', 'novedades': 'p.destacado DESC,p.id DESC'}.get(sort, 'p.destacado DESC,p.id DESC')
        with db() as conn:
            products = conn.execute(f'''SELECT p.*, c.nombre categoria,
                (SELECT archivo FROM catalogo_fotos f WHERE f.producto_id=p.id ORDER BY orden,id LIMIT 1) foto,
                (SELECT COUNT(*) FROM catalogo_fotos f WHERE f.producto_id=p.id) fotos
                FROM catalogo_productos p JOIN catalogo_categorias c ON c.id=p.categoria_id
                WHERE p.activo=1 AND (p.nombre LIKE ? OR p.descripcion LIKE ?)
                AND (?='' OR CAST(p.categoria_id AS TEXT)=?) ORDER BY {order}''', (f'%{q}%', f'%{q}%', category, category)).fetchall()
        return render_template('catalog.html', products=products, categories=categories(), query=q, category=category, sort=sort)

    @app.get('/catalogo/producto/<int:product_id>')
    def product(product_id):
        item, photos = find_product(product_id, public=True)
        return render_template('product.html', product=item, photos=photos)

    @app.get('/fotos/<name>')
    def photo(name):
        with db() as conn:
            exists = conn.execute('''SELECT 1 FROM catalogo_fotos f JOIN catalogo_productos p
                ON p.id=f.producto_id WHERE f.archivo=? AND (p.activo=1 OR ?=1)''', (name, int(bool(session.get('admin'))))).fetchone()
        if not exists:
            abort(404)
        return send_from_directory(uploads, name, max_age=86400)

    @app.route('/admin/login', methods=['GET', 'POST'])
    @limiter.limit('5 per minute', methods=['POST'])
    def login():
        configured = bool(app.config['ADMIN_PASSWORD_HASH'])
        if request.method == 'POST':
            if configured and check_password_hash(app.config['ADMIN_PASSWORD_HASH'], request.form.get('password', '')):
                session.clear()
                session['admin'] = True
                session.permanent = True
                return redirect(url_for('admin'))
            flash('No se pudo ingresar. Revisa la contraseña o la configuración de acceso.', 'error')
        return render_template('login.html', configured=configured)

    @app.post('/admin/salir')
    @private
    def logout():
        session.clear()
        return redirect(url_for('catalog'))

    @app.get('/admin')
    @private
    def admin():
        with db() as conn:
            products = conn.execute('''SELECT p.*, c.nombre categoria,
                (SELECT archivo FROM catalogo_fotos f WHERE producto_id=p.id ORDER BY orden,id LIMIT 1) foto
                FROM catalogo_productos p JOIN catalogo_categorias c ON c.id=p.categoria_id ORDER BY p.id DESC''').fetchall()
        return render_template('admin.html', products=products)

    @app.route('/admin/categorias', methods=['GET', 'POST'])
    @private
    def admin_categories():
        if request.method == 'POST':
            name = request.form.get('nombre', '').strip()
            category_id = request.form.get('id', type=int)
            if not 1 <= len(name) <= 80:
                flash('Escribe una categoría de hasta 80 caracteres.', 'error')
            else:
                try:
                    with db() as conn:
                        if category_id:
                            conn.execute('UPDATE catalogo_categorias SET nombre=? WHERE id=?', (name, category_id))
                        else:
                            conn.execute('INSERT INTO catalogo_categorias(nombre) VALUES (?)', (name,))
                    flash('Categoría guardada.', 'success')
                except sqlite3.IntegrityError:
                    flash('Ya existe una categoría con ese nombre.', 'error')
            return redirect(url_for('admin_categories'))
        return render_template('categories.html', categories=categories())

    @app.route('/admin/configuracion', methods=['GET', 'POST'])
    @private
    def configuration():
        if request.method == 'POST':
            name = request.form.get('nombre', '').strip()
            description = request.form.get('descripcion', '').strip()
            phone = re.sub(r'[\s+()-]', '', request.form.get('whatsapp', ''))
            if not 1 <= len(name) <= 60 or len(description) > 220 or (phone and not re.fullmatch(r'[1-9][0-9]{7,14}', phone)):
                flash('Revisa el nombre y el teléfono con código de país (ej.: 56912345678).', 'error')
                return render_template('configuration.html', values=request.form), 400
            with db() as conn:
                for key, value in {'nombre': name, 'descripcion': description, 'whatsapp': phone}.items():
                    conn.execute('UPDATE catalogo_configuracion SET valor=? WHERE clave=?', (value, key))
            flash('Configuración guardada.', 'success')
            return redirect(url_for('configuration'))
        return render_template('configuration.html', values=settings())

    @app.route('/admin/producto/nuevo', defaults={'product_id': None}, methods=['GET', 'POST'])
    @app.route('/admin/producto/<int:product_id>', methods=['GET', 'POST'])
    @private
    def edit(product_id):
        item, photos = find_product(product_id) if product_id else (None, [])
        if request.method == 'GET':
            return render_template('edit.html', product=item, values=dict(item) if item else {'activo': 1}, photos=photos, categories=categories())
        values = request.form.to_dict()
        values.update(activo=int('activo' in values), destacado=int('destacado' in values))
        staged = []
        try:
            name = values.get('nombre', '').strip()
            description = values.get('descripcion', '').strip()
            price, stock, category_id = int(values.get('precio', '')), int(values.get('stock', '')), int(values.get('categoria_id', ''))
            if not 1 <= len(name) <= 120 or len(description) > 4000 or not 0 <= price <= 999999999 or not 0 <= stock <= 999999:
                raise ValueError('Revisa nombre, descripción, precio y stock. Usa números enteros sin puntos.')
            if category_id not in {c['id'] for c in categories()}:
                raise ValueError('Selecciona una categoría válida.')
            ml = values.get('enlace_ml', '').strip()
            if ml:
                parts = urlsplit(ml)
                if len(ml) > 2000 or parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443) or not (parts.hostname in {'mercadolibre.cl','mercadolibre.com'} or (parts.hostname or '').endswith('.mercadolibre.cl')):
                    raise ValueError('El enlace de Mercado Libre debe ser HTTPS y pertenecer a mercadolibre.cl o mercadolibre.com.')
            files = [f for f in request.files.getlist('fotos') if f.filename]
            urls = [line.strip() for line in values.get('foto_urls', '').splitlines() if line.strip()]
            if len(files) + len(urls) + len(photos) > 8:
                raise ValueError('Cada producto admite hasta 8 fotos. Quita alguna foto antes de agregar más.')
            for upload in files:
                if len(upload.stream.read(6 * 1024 * 1024 + 1)) > 6 * 1024 * 1024:
                    raise ValueError('Cada foto puede pesar hasta 6 MB.')
                upload.stream.seek(0)
                staged.append(guardar_foto(upload.stream, uploads))
            for url in urls:
                with descargar_foto(url) as stream:
                    staged.append(guardar_foto(stream, uploads))
            with db() as conn:
                fields = (name, description, price, stock, category_id, ml, values['activo'], values['destacado'])
                if product_id:
                    conn.execute('''UPDATE catalogo_productos SET nombre=?,descripcion=?,precio=?,stock=?,categoria_id=?,enlace_ml=?,activo=?,destacado=? WHERE id=?''', (*fields, product_id))
                else:
                    cursor = conn.execute('''INSERT INTO catalogo_productos(nombre,descripcion,precio,stock,categoria_id,enlace_ml,activo,destacado) VALUES (?,?,?,?,?,?,?,?)''', fields)
                    product_id = cursor.lastrowid
                maximum = conn.execute('SELECT COALESCE(MAX(orden),-1) FROM catalogo_fotos WHERE producto_id=?', (product_id,)).fetchone()[0]
                for offset, filename in enumerate(staged, 1):
                    conn.execute('INSERT INTO catalogo_fotos(producto_id,archivo,orden) VALUES (?,?,?)', (product_id, filename, maximum + offset))
            flash('Producto guardado.', 'success')
            return redirect(url_for('admin'))
        except (ValueError, sqlite3.Error) as error:
            for filename in staged:
                (uploads / filename).unlink(missing_ok=True)
            flash(str(error) if isinstance(error, ValueError) else 'No se pudo guardar. Inténtalo nuevamente.', 'error')
            return render_template('edit.html', product=item, values=values, photos=photos, categories=categories()), 400

    @app.post('/admin/foto/<int:photo_id>/<action>')
    @private
    def manage_photo(photo_id, action):
        if action not in {'portada', 'quitar'}:
            abort(404)
        with db() as conn:
            photo = conn.execute('SELECT * FROM catalogo_fotos WHERE id=?', (photo_id,)).fetchone()
            if photo is None:
                abort(404)
            if action == 'quitar':
                conn.execute('DELETE FROM catalogo_fotos WHERE id=?', (photo_id,))
            else:
                minimum = conn.execute('SELECT MIN(orden) FROM catalogo_fotos WHERE producto_id=?', (photo['producto_id'],)).fetchone()[0]
                conn.execute('UPDATE catalogo_fotos SET orden=? WHERE id=?', (minimum - 1, photo_id))
        if action == 'quitar':
            (uploads / photo['archivo']).unlink(missing_ok=True)
        flash('Foto actualizada.', 'success')
        return redirect(url_for('edit', product_id=photo['producto_id']))

    @app.get('/admin/respaldo')
    @private
    def backup():
        # Snapshot consistente de SQLite, incluidas las tablas de la práctica anterior.
        temporary = tempfile.TemporaryDirectory()
        try:
            snapshot = Path(temporary.name) / 'practica.db'
            with db() as source:
                target = sqlite3.connect(snapshot)
                try:
                    source.backup(target)
                finally:
                    target.close()
            archive_path = Path(temporary.name) / 'respaldo_catalogo.zip'
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, 'data/practica.db')
                for file in uploads.iterdir():
                    if file.is_file():
                        archive.write(file, 'data/uploads/' + file.name)
            response = send_file(archive_path, as_attachment=True, download_name='respaldo_catalogo.zip', mimetype='application/zip')
            response.direct_passthrough = False
            response.call_on_close(temporary.cleanup)
            return response
        except Exception:
            temporary.cleanup()
            raise

    @app.get('/health')
    def health():
        with db() as conn:
            conn.execute('SELECT 1').fetchone()
        return {'status': 'ok'}

    @app.errorhandler(413)
    def large(error):
        return render_template('error.html', message='La carga supera los 50 MB. Usa fotos de hasta 6 MB cada una.'), 413

    @app.errorhandler(429)
    def limited(error):
        return render_template('error.html', message='Demasiados intentos. Espera un minuto y vuelve a intentar.'), 429

    @app.errorhandler(404)
    def missing(error):
        return render_template('error.html', message='No encontramos esa página o el producto ya no está publicado.'), 404

    return app


app = create_app()

if __name__ == '__main__':
    app.run()
