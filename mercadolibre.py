"""Integración de lectura Mercado Libre Chile: OAuth PKCE, cola durable y fotos remotas.
Las credenciales de la cuenta viven en un SQLite privado separado, fuera del respaldo.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from imagenes import SinRedirecciones
from respaldos import StorageLock

ITEM = re.compile(r'MLC[0-9]{6,20}')
API = 'https://api.mercadolibre.com'


class MeliError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def photo_url(value):
    """Solo imágenes del CDN conocido. Nunca se usa una URL arbitraria del aviso."""
    try:
        p = urlsplit(value)
        if p.scheme in ('http','https') and p.hostname == 'http2.mlstatic.com' and p.port in (None,80,443) and not p.username and not p.password and p.path.startswith('/D_') and not p.fragment:
            return 'https://http2.mlstatic.com' + p.path + ('?' + p.query if p.query else '')
    except (TypeError, ValueError):
        pass
    return None


def api_request(path, token=None, form=None, extra_headers=None):
    if not path.startswith('/') or path.startswith('//'):
        raise MeliError('Recurso de Mercado Libre no válido.')
    headers = {'Accept':'application/json','User-Agent':'YAProductosCatalogo/2.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if extra_headers:
        headers.update(extra_headers)
    body = urlencode(form).encode() if form is not None else None
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with build_opener(SinRedirecciones()).open(Request(API+path,data=body,headers=headers),timeout=12) as response:
            content = response.read(4*1024*1024+1)
            if len(content) > 4*1024*1024:
                raise MeliError('La respuesta de Mercado Libre es demasiado grande.')
            value = json.loads(content)
            if not isinstance(value,dict):
                raise MeliError('Respuesta de Mercado Libre no compatible.')
            return value
    except HTTPError as error:
        # No guardar cuerpos, códigos OAuth ni tokens en mensajes o registros.
        messages={400:'Mercado Libre rechazó la solicitud. Revisa la configuración o vuelve a conectar la cuenta.',401:'La autorización venció o fue revocada. Vuelve a conectar tu cuenta.',403:'Mercado Libre rechazó el acceso. Revisa los permisos de la aplicación; activa Venta y envíos para órdenes y Facturación de una venta para movimientos Flex; vuelve a autorizar la misma cuenta.',404:'La publicación ya no está disponible en Mercado Libre.',429:'Mercado Libre limitó las consultas. Se volverá a intentar más tarde.'}
        raise MeliError(messages.get(error.code,'Mercado Libre no respondió correctamente. Se volverá a intentar.'), error.code) from None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as error:
        raise MeliError('No se pudo consultar Mercado Libre. Reintenta en unos minutos.') from None


def install(app, db, private):
    app.config.setdefault('ML_CLIENT_ID',os.environ.get('ML_CLIENT_ID',''))
    app.config.setdefault('ML_CLIENT_SECRET',os.environ.get('ML_CLIENT_SECRET',''))
    app.config.setdefault('ML_REDIRECT_URI',os.environ.get('ML_REDIRECT_URI',''))
    app.config.setdefault('ML_EXPECTED_SELLER_ID',os.environ.get('ML_EXPECTED_SELLER_ID','').strip())
    data=Path(app.config['DATA_DIR'])
    internal=data/'mercadolibre_privado.sqlite'
    @contextmanager
    def state_db(timeout=2):
        c=sqlite3.connect(internal,timeout=timeout)
        c.row_factory=sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    with state_db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS estado(clave TEXT PRIMARY KEY,valor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pendientes(item_id TEXT PRIMARY KEY,version INTEGER NOT NULL DEFAULT 1,intentos INTEGER NOT NULL DEFAULT 0,proximo REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
        ''')
    if os.name != 'nt':
        internal.chmod(0o600)
    with db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS catalogo_ml_items(item_id TEXT PRIMARY KEY,producto_id INTEGER NOT NULL UNIQUE REFERENCES catalogo_productos(id) ON DELETE CASCADE,seller_id TEXT NOT NULL,estado TEXT NOT NULL,actualizado TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalogo_ml_fotos(id INTEGER PRIMARY KEY AUTOINCREMENT,producto_id INTEGER NOT NULL REFERENCES catalogo_productos(id) ON DELETE CASCADE,url TEXT NOT NULL,orden INTEGER NOT NULL);
        ''')

    def get(key, default=''):
        with state_db() as c:
            r=c.execute('SELECT valor FROM estado WHERE clave=?',(key,)).fetchone()
        return r[0] if r else default
    def put(key,value):
        with state_db() as c:
            c.execute('INSERT OR REPLACE INTO estado VALUES (?,?)',(key,str(value)))
    def configured():
        return all(app.config[k] for k in ('ML_CLIENT_ID','ML_CLIENT_SECRET','ML_REDIRECT_URI'))
    def valid_redirect():
        p=urlsplit(app.config['ML_REDIRECT_URI'])
        return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password and not p.query and not p.fragment
    def save_tokens(result):
        if not result.get('access_token') or not result.get('refresh_token') or not str(result.get('user_id','')).isdigit():
            raise MeliError('La autorización no incluyó los datos necesarios. Activa Refresh Token y vuelve a conectar.')
        expected=str(app.config['ML_EXPECTED_SELLER_ID']).strip()
        if expected and str(result['user_id'])!=expected:
            raise MeliError('La cuenta autorizada no coincide con ML_EXPECTED_SELLER_ID. Inicia sesión con el vendedor correcto.')
        current=get('seller')
        if current and current!=str(result['user_id']):
            raise MeliError('Este catálogo está vinculado a otra cuenta. Usa Cambiar cuenta desde el panel antes de autorizar otro vendedor.')
        with state_db() as c:
            for key,value in {'access':result['access_token'],'refresh':result['refresh_token'],'expires':time.time()+int(result.get('expires_in',21600)),'seller':result['user_id'],'error':''}.items():
                c.execute('INSERT OR REPLACE INTO estado VALUES (?,?)',(key,str(value)))
    def access_token(force=False):
        with StorageLock(data/'.mercadolibre_auth.lock').acquire():
            if not get('refresh'):
                raise MeliError('Primero conecta tu cuenta de Mercado Libre.')
            if force or float(get('expires','0')) < time.time()+120:
                result=api_request('/oauth/token',form={'grant_type':'refresh_token','client_id':app.config['ML_CLIENT_ID'],'client_secret':app.config['ML_CLIENT_SECRET'],'refresh_token':get('refresh')})
                save_tokens(result)
            return get('access')
    def enqueue(ids,connection=None):
        valid=[i for i in ids if isinstance(i,str) and ITEM.fullmatch(i)]
        def insert(c):
            c.executemany('''INSERT INTO pendientes(item_id) VALUES (?) ON CONFLICT(item_id) DO UPDATE SET version=version+1,intentos=0,proximo=0,error='' ''',[(i,) for i in valid])
        if connection is not None:
            insert(connection)
        else:
            with state_db() as c:
                insert(c)
        wake.set()

    def import_item(item_id):
        token=access_token()
        try:
            item=api_request('/items/'+item_id,token)
        except MeliError as error:
            if error.status not in (404,410):
                raise
            with StorageLock(data/'.catalogo.lock').acquire():
                with db() as c:
                    c.execute('UPDATE catalogo_productos SET activo=0 WHERE id IN (SELECT producto_id FROM catalogo_ml_items WHERE item_id=? AND seller_id=?)',(item_id,get('seller')))
                    c.execute("UPDATE catalogo_ml_items SET estado='deleted' WHERE item_id=? AND seller_id=?",(item_id,get('seller')))
            return
        if item.get('id')!=item_id or str(item.get('seller_id'))!=get('seller') or item.get('site_id')!='MLC':
            raise MeliError('La publicación no pertenece a la cuenta chilena conectada.')
        try:
            description=api_request('/items/'+item_id+'/description',token).get('plain_text','')
        except MeliError as error:
            if error.status != 404:
                raise
            description=''
        if not isinstance(description,str):
            raise MeliError('Descripción de publicación no compatible.')
        try:
            price=int(Decimal(str(item['price'])).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            stock=int(item.get('available_quantity',0))
            title=item['title'].strip()
            if item.get('currency_id')!='CLP' or not 0<=price<=999999999 or not 0<=stock<=999999 or not title:
                raise ValueError()
        except (ValueError,TypeError,KeyError,InvalidOperation):
            raise MeliError('La publicación debe tener título, precio en CLP y disponibilidad válidos.') from None
        permalink=item.get('permalink','')
        try:
            link=urlsplit(permalink)
            if link.scheme not in ('http','https') or not (link.hostname=='mercadolibre.cl' or (link.hostname or '').endswith('.mercadolibre.cl')) or link.username or link.password or link.port not in (None,80,443):
                raise ValueError()
            permalink='https://'+link.netloc+link.path+('?' + link.query if link.query else '')
        except ValueError:
            raise MeliError('La publicación no incluye un enlace de compra válido.') from None
        photos=[]
        for picture in item.get('pictures',[]):
            url=photo_url(picture.get('secure_url') or picture.get('url'))
            if not url:
                raise MeliError('Una foto no usa el servidor de imágenes esperado de Mercado Libre.')
            if url not in photos:
                photos.append(url)
        if len(photos)>50:
            raise MeliError('La publicación supera las 50 fotos admitidas por esta integración.')
        if any(len(url)>2000 for url in photos):
            raise MeliError('Una dirección de imagen supera el largo admitido.')
        active=int(item.get('status')=='active')
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        # Coordina escritura con productos, portadas y restauraciones del catálogo.
        with StorageLock(data/'.catalogo.lock').acquire():
            with db() as c:
                row=c.execute('SELECT producto_id FROM catalogo_ml_items WHERE item_id=?',(item_id,)).fetchone()
                fields=(title[:120],description[:4000],price,stock,permalink,active)
                if row:
                    product_id=row[0]
                    c.execute('UPDATE catalogo_productos SET nombre=?,descripcion=?,precio=?,stock=?,enlace_ml=?,activo=? WHERE id=?',(*fields,product_id))
                else:
                    c.execute("INSERT OR IGNORE INTO catalogo_categorias(nombre) VALUES ('Mercado Libre')")
                    category_id=c.execute("SELECT id FROM catalogo_categorias WHERE nombre='Mercado Libre' COLLATE NOCASE").fetchone()[0]
                    product_id=c.execute('INSERT INTO catalogo_productos(nombre,descripcion,precio,stock,enlace_ml,activo,categoria_id) VALUES (?,?,?,?,?,?,?)',(*fields,category_id)).lastrowid
                c.execute('INSERT OR REPLACE INTO catalogo_ml_items VALUES (?,?,?,?,?)',(item_id,product_id,get('seller'),str(item.get('status',''))[:40],now))
                c.execute('DELETE FROM catalogo_ml_fotos WHERE producto_id=?',(product_id,))
                c.executemany('INSERT INTO catalogo_ml_fotos(producto_id,url,orden) VALUES (?,?,?)',[(product_id,url,i) for i,url in enumerate(photos)])
        put('last',now)

    def scan():
        seller=get('seller')
        scroll=''
        # Solo lista IDs; los detalles se consultan después, fuera de la respuesta web.
        for page in range(101):
            query={'search_type':'scan','limit':100}
            if scroll:
                query['scroll_id']=scroll
            result=api_request('/users/'+seller+'/items/search?'+urlencode(query),access_token())
            ids=result.get('results') or []
            if not isinstance(ids,list) or any(not isinstance(i,str) or not ITEM.fullmatch(i) for i in ids):
                raise MeliError('La lista de publicaciones contiene identificadores no compatibles.')
            if not ids:
                put('scan_requested','0')
                put('scan_last',datetime.now(timezone.utc).isoformat(timespec='seconds'))
                return
            enqueue(ids)
            scroll=result.get('scroll_id')
            if not scroll:
                # Algunas cuentas devuelven todos los resultados sin cursor.
                if len(ids)>=100:
                    raise MeliError('Mercado Libre no devolvió el cursor para continuar. Vuelve a sincronizar.')
                put('scan_requested','0')
                put('scan_last',datetime.now(timezone.utc).isoformat(timespec='seconds'))
                return
        raise MeliError('La importación admite hasta 10.000 publicaciones.')

    def process_tick():
        if not configured() or not get('refresh'):
            return False
        reports=app.extensions.get('reportes')
        if reports and reports['tick']():
            return True
        if get('scan_requested')=='1' and float(get('scan_retry','0'))<=time.time():
            try:
                scan()
                put('error','')
            except (MeliError,TimeoutError):
                put('error','No se completó la búsqueda. Revisa permisos y conexión; reintentaremos en 5 minutos.')
                put('scan_retry',time.time()+300)
        with state_db() as c:
            job=c.execute('SELECT * FROM pendientes WHERE proximo<=? ORDER BY proximo,item_id LIMIT 1',(time.time(),)).fetchone()
        if not job:
            return False
        try:
            import_item(job['item_id'])
            with state_db() as c:
                c.execute('DELETE FROM pendientes WHERE item_id=? AND version=?',(job['item_id'],job['version']))
            put('error','')
        except (MeliError,TimeoutError,OSError,sqlite3.Error) as error:
            message=str(error) if isinstance(error,MeliError) else 'No se pudo guardar la publicación. Se volverá a intentar.'
            with state_db() as c:
                c.execute('UPDATE pendientes SET intentos=intentos+1,proximo=?,error=? WHERE item_id=? AND version=?',(time.time()+min(3600,30*2**min(job['intentos'],7)),message,job['item_id'],job['version']))
            put('error',message)
        return True

    def tick():
        # Coordina al consumidor con el cambio explícito de cuenta.
        lock=StorageLock(data/'.mercadolibre_operation.lock')
        try:
            lock.acquire(timeout=0)
        except TimeoutError:
            return False
        try:
            return process_tick()
        finally:
            lock.release()

    wake=threading.Event()
    thread_guard=threading.Lock()
    thread_state={'pid':None}
    def loop():
        # Un consumidor por carpeta de datos, aunque se inicien varios workers.
        lock=StorageLock(data/'.mercadolibre_worker.lock')
        try:
            lock.acquire(timeout=0)
        except TimeoutError:
            thread_state['pid']=None
            return
        try:
            with app.app_context():
                while True:
                    try:
                        busy=tick()
                    except Exception:
                        # Sin valores sensibles en el registro.
                        app.logger.warning('No se pudo completar el ciclo de sincronización de Mercado Libre.')
                        busy=False
                    wake.wait(0.5 if busy else 20)
                    wake.clear()
        finally:
            lock.release()
            thread_state['pid']=None
    @app.before_request
    def start_worker():
        if app.testing or not configured():
            return
        with thread_guard:
            if thread_state['pid']!=os.getpid():
                thread_state['pid']=os.getpid()
                threading.Thread(target=loop,daemon=True,name='mercadolibre-sync').start()

    bp=Blueprint('meli',__name__)
    @bp.get('/admin/mercadolibre')
    @private
    def dashboard():
        with state_db() as c:
            pending=c.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0]
            failures=c.execute("SELECT item_id,error FROM pendientes WHERE error!='' ORDER BY item_id LIMIT 10").fetchall()
        with db() as c:
            imported=c.execute('SELECT COUNT(*) FROM catalogo_ml_items WHERE seller_id=?',(get('seller'),)).fetchone()[0]
            has_imports=bool(c.execute('SELECT 1 FROM catalogo_ml_items LIMIT 1').fetchone())
        return render_template('mercadolibre.html',ready=configured(),connected=bool(get('refresh')),has_imports=has_imports,seller=get('seller'),nickname=get('nickname'),expected_seller=app.config['ML_EXPECTED_SELLER_ID'],last=get('last'),pending=pending,failures=failures,imported=imported,error=get('error'),scanning=get('scan_requested')=='1',redirect_uri=app.config['ML_REDIRECT_URI'])

    @bp.post('/admin/mercadolibre/conectar')
    @private
    def connect():
        if not configured() or not valid_redirect():
            flash('Configura ML_CLIENT_ID, ML_CLIENT_SECRET y ML_REDIRECT_URI HTTPS en Render.','error')
            return redirect(url_for('meli.dashboard'))
        state=secrets.token_urlsafe(32)
        verifier=secrets.token_urlsafe(64)
        session['ml_oauth']={'state':state,'verifier':verifier,'created':time.time(),'generation':get('generation','0')}
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query={'response_type':'code','client_id':app.config['ML_CLIENT_ID'],'redirect_uri':app.config['ML_REDIRECT_URI'],'state':state,'code_challenge':challenge,'code_challenge_method':'S256'}
        return redirect('https://auth.mercadolibre.cl/authorization?'+urlencode(query))

    @bp.get('/mercadolibre/callback')
    @private
    def callback():
        oauth=session.pop('ml_oauth',{})
        supplied=request.args.get('state','')
        if not oauth or time.time()-oauth.get('created',0)>600 or not secrets.compare_digest(oauth.get('state',''),supplied):
            abort(400,'La autorización venció o no corresponde a esta sesión. Vuelve a conectar desde administración.')
        if request.args.get('error') or not request.args.get('code'):
            flash('No se autorizó el acceso a Mercado Libre. Puedes volver a intentarlo.','error')
            return redirect(url_for('meli.dashboard'))
        try:
            with StorageLock(data/'.mercadolibre_auth.lock').acquire():
                if oauth.get('generation','0')!=get('generation','0'):
                    raise MeliError('La conexión cambió mientras autorizabas. Vuelve a conectar desde el panel.')
                result=api_request('/oauth/token',form={'grant_type':'authorization_code','client_id':app.config['ML_CLIENT_ID'],'client_secret':app.config['ML_CLIENT_SECRET'],'code':request.args['code'],'redirect_uri':app.config['ML_REDIRECT_URI'],'code_verifier':oauth['verifier']})
                # Verificar el sitio antes de persistir una autorización.
                user=api_request('/users/me',result.get('access_token'))
                if user.get('site_id')!='MLC' or str(user.get('id'))!=str(result.get('user_id')):
                    raise MeliError('Esta integración está preparada para una cuenta de Mercado Libre Chile.')
                save_tokens(result)
                put('nickname',str(user.get('nickname',''))[:100])
            flash('Cuenta conectada. Pulsa Importar y actualizar productos.','success')
        except (MeliError,TimeoutError) as error:
            flash(str(error),'error')
        return redirect(url_for('meli.dashboard'))

    @bp.post('/admin/mercadolibre/desconectar')
    @private
    def disconnect():
        if request.form.get('confirmar')!='si':
            flash('Marca la confirmación para desconectar y ocultar los productos importados.','error')
            return redirect(url_for('meli.dashboard'))
        operation=StorageLock(data/'.mercadolibre_operation.lock')
        try:
            operation.acquire(timeout=1)
        except TimeoutError:
            flash('Hay una sincronización en curso. Espera a que termine y vuelve a pulsar Cambiar cuenta.','error')
            return redirect(url_for('meli.dashboard'))
        try:
            with StorageLock(data/'.mercadolibre_auth.lock').acquire():
                generation=str(int(get('generation','0'))+1)
                with StorageLock(data/'.catalogo.lock').acquire():
                    with db() as c:
                        # Ambas bases se actualizan en una sola transacción SQLite.
                        c.execute('ATTACH DATABASE ? AS ml_private',(str(internal),))
                        c.execute('UPDATE catalogo_productos SET activo=0 WHERE id IN (SELECT producto_id FROM catalogo_ml_items)')
                        c.execute('DELETE FROM ml_private.pendientes')
                        if app.extensions.get('reportes'):
                            c.execute('DELETE FROM ml_private.reportes_pendientes')
                        c.execute('DELETE FROM ml_private.estado')
                        c.execute("INSERT INTO ml_private.estado VALUES ('generation',?)",(generation,))
            session.pop('ml_oauth',None)
            flash('Cuenta desconectada del catálogo. Los productos importados quedaron ocultos; los manuales se conservaron. Inicia sesión en Mercado Libre con la cuenta correcta y pulsa Conectar mi cuenta.','success')
        except (sqlite3.Error,OSError,TimeoutError):
            flash('No se pudo completar el cambio de cuenta. Vuelve a intentarlo.','error')
        finally:
            operation.release()
        return redirect(url_for('meli.dashboard'))

    @bp.post('/admin/mercadolibre/sincronizar')
    @private
    def sync():
        if not get('refresh'):
            flash('Primero conecta tu cuenta.','error')
        else:
            with state_db() as c:
                for key,value in [('scan_requested','1'),('scan_retry','0')]:
                    c.execute('INSERT OR REPLACE INTO estado VALUES (?,?)',(key,value))
            wake.set()
            flash('Importación solicitada. Se procesa en segundo plano. Actualiza esta pantalla para ver el avance.','success')
        return redirect(url_for('meli.dashboard'))

    @bp.route('/mercadolibre/notificaciones',methods=['GET','POST'])
    def notifications():
        if request.method=='GET':
            return {'estado':'listo','servicio':'notificaciones de Mercado Libre'}
        if not app.config['ML_CLIENT_ID']:
            return {'error':'integracion sin configurar'},503
        body=request.get_json(silent=True)
        if not isinstance(body,dict):
            return {'error':'JSON requerido'},400
        # El aviso no es una fuente confiable de datos: solo agenda una consulta
        # autenticada a la API. Nunca se descarga body.resource como URL libre.
        if str(body.get('application_id'))!=str(app.config['ML_CLIENT_ID']):
            return {'error':'aplicacion no autorizada'},403
        if body.get('topic') in ('orders','orders_v2','shipments') and app.extensions.get('reportes'):
            return app.extensions['reportes']['notification'](body)
        if body.get('topic')!='items':
            return {'estado':'ignorado'}
        resource=body.get('resource','')
        match=re.fullmatch(r'/items/(MLC[0-9]{6,20})',resource) if isinstance(resource,str) else None
        if not match:
            return {'error':'recurso no valido'},400
        if not get('seller'):
            return {'error':'cuenta aun no conectada'},503
        if str(body.get('user_id'))!=get('seller'):
            return {'error':'cuenta no autorizada'},403
        try:
            with state_db(timeout=0.1) as c:
                c.execute('BEGIN IMMEDIATE')
                current_seller=c.execute("SELECT valor FROM estado WHERE clave='seller'").fetchone()
                if not current_seller or str(body.get('user_id'))!=current_seller[0]:
                    return {'error':'cuenta no autorizada'},403
                if c.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0]>=10000 and not c.execute('SELECT 1 FROM pendientes WHERE item_id=?',(match[1],)).fetchone():
                    return {'error':'cola temporalmente llena'},503
                enqueue([match[1]],c)
        except sqlite3.OperationalError:
            return {'error':'reintentar'},503
        return {'estado':'recibido'}

    app.register_blueprint(bp)
    app.extensions['mercadolibre']={'tick':tick,'import_item':import_item,'enqueue':enqueue,'state_db':state_db,'get':get,'put':put,'access_token':access_token,'scan':scan}
