"""Clasificación local de publicaciones. No modifica categorías en Mercado Libre."""
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from flask import Blueprint, flash, redirect, render_template, request, url_for
from mercadolibre import MeliError, api_request

COLUMNS=('item_id','seller_id','auto_categoria_id','manual','categoria_ml','ruta_ml','origen','actualizado')
SCHEMA='''CREATE TABLE IF NOT EXISTS catalogo_ml_clasificacion (
 item_id TEXT PRIMARY KEY,seller_id TEXT NOT NULL,auto_categoria_id INTEGER NOT NULL,
 manual INTEGER NOT NULL DEFAULT 0,categoria_ml TEXT NOT NULL DEFAULT '',ruta_ml TEXT NOT NULL DEFAULT '',
 origen TEXT NOT NULL,actualizado TEXT NOT NULL);'''
GENERIC={'mercado libre','por clasificar'}


def normalize(value):
    return ' '.join(''.join(ch for ch in unicodedata.normalize('NFKD',str(value or '').casefold()) if not unicodedata.combining(ch)).split())


def classify(title,path=''):
    """Reglas específicas antes de familias generales. Sin certeza: Por clasificar."""
    t=normalize(title);p=normalize(path)
    def has(pattern,source=t):return bool(re.search(pattern,source))
    if has(r'\bcapas?\b') and has(r'\b(medieval\w*|miedeval\w*|viking\w*|caballer\w*|templari\w*)\b'):return 'Capas medievales'
    if has(r'\b(trajes?|conjuntos?|disfraces?|disfraz|vestidos?)\b') and has(r'\b(huas[oa]s?|haus[oa]s?)\b'):return 'Trajes de huaso y huasa'
    if has(r'\b(disfraz|disfraces)\b'):return 'Disfraces'
    if has(r'\bvestidos?\b') and has(r'\b(nina|ninas|infantil\w*|bebe\w*)\b'):return 'Vestidos de niña'
    if has(r'\bconjuntos?\b') and has(r'\b(nino|ninos|infantil\w*|bebe\w*)\b'):return 'Conjuntos de niño'
    if has(r'\b(sombreros?|gorros?)\b') and has(r'\b(texan\w*|vaquer\w*|cowboy)\b'):return 'Sombreros texanos'
    if has(r'\bpoleron(?:es)?\b'):return 'Polerones unisex' if has(r'\bunisex\b') else 'Polerones'
    if has(r'\bpoleras?\b') and has(r'\bmangas?\s+largas?\b'):return 'Poleras manga larga'
    if has(r'\b(poleras?|camisetas?)\b') and has(r'\bunisex\b'):return 'Poleras unisex'
    if has(r'\b(boleros?|boloeros?)\b'):return 'Boleros elegantes'
    if has(r'\bblazers?\b'):return 'Blazers'
    if has(r'\b(chal|chales|estolas?)\b'):return 'Chales de fiesta'
    if has(r'\bponchos?\b'):return 'Ponchos'
    if has(r'\bblusas?\b'):return 'Blusas'
    if has(r'\bvestidos?\b'):return 'Vestidos'
    if has(r'\bgorros?\b'):return 'Gorros'
    if has(r'\bsombreros?\b'):return 'Sombreros'
    if has(r'\b(poleras?|camisetas?)\b'):return 'Poleras'
    if has(r'\b(rompecabezas|puzzles?)\b'):return 'Rompecabezas'
    if has(r'\bjuegos?\b'):return 'Juegos'
    # La taxonomía del marketplace es respaldo, sin inventar una subfamilia por descripción.
    for pattern,label in [(r'\b(rompecabezas|puzzles?)\b','Rompecabezas'),(r'\b(disfraces|disfraz)\b','Disfraces'),
        (r'\bblusas?\b','Blusas'),(r'\bblazers?\b','Blazers'),(r'\bboleros?\b','Boleros elegantes'),
        (r'\b(chal|chales|estolas?)\b','Chales de fiesta'),(r'\bponchos?\b','Ponchos'),
        (r'\bgorros?\b','Gorros'),(r'\bsombreros?\b','Sombreros'),(r'\bpoleron(?:es)?\b','Polerones'),
        (r'\b(poleras?|camisetas?)\b','Poleras'),(r'\bvestidos?\b','Vestidos'),(r'\bjuegos?\b','Juegos')]:
        if has(pattern,p):return label
    return 'Por clasificar'


def install(app,db,private):
    ml=app.extensions['mercadolibre']
    with db() as c:c.executescript(SCHEMA)
    with ml['state_db']() as c:c.execute('CREATE TABLE IF NOT EXISTS categorias_cache(id TEXT PRIMARY KEY,ruta TEXT NOT NULL,actualizado REAL NOT NULL)')

    def prepare(item,token):
        title=str(item.get('title',''));category_id=str(item.get('category_id',''))
        if not re.fullmatch(r'MLC[0-9]{1,20}',category_id):category_id=''
        ruta='';origin='titulo';label=classify(title)
        # Los títulos específicos no requieren otra llamada a la API.
        if category_id and label=='Por clasificar':
            with ml['state_db']() as c:cached=c.execute('SELECT * FROM categorias_cache WHERE id=?',(category_id,)).fetchone()
            if cached and cached['actualizado']>time.time()-7*86400:ruta=cached['ruta']
            else:
                try:
                    value=api_request('/categories/'+category_id,token)
                    if value.get('id')==category_id:
                        names=[r.get('name','') for r in value.get('path_from_root',[]) if isinstance(r,dict)]
                        ruta=' > '.join(n for n in names if isinstance(n,str))[:1000]
                        if not ruta:ruta=str(value.get('name',''))[:1000]
                        with ml['state_db']() as c:c.execute('INSERT OR REPLACE INTO categorias_cache VALUES (?,?,?)',(category_id,ruta,time.time()))
                except (MeliError,sqlite3.Error,TimeoutError):
                    ruta='' # Una categoría inaccesible no impide importar el producto.
            label=classify(title,ruta);origin='mercadolibre' if label!='Por clasificar' else 'pendiente'
        if label=='Por clasificar':origin='pendiente'
        return {'label':label,'categoria_ml':category_id,'ruta_ml':ruta,'origen':origin}

    def eligible(c,item_id,current_name,current_id):
        state=c.execute('SELECT * FROM catalogo_ml_clasificacion WHERE item_id=?',(item_id,)).fetchone()
        if state:
            return not state['manual'] and state['auto_categoria_id']==current_id
        return normalize(current_name) in GENERIC

    def apply(c,product_id,item_id,seller,proposal):
        current=c.execute('SELECT p.categoria_id,c.nombre FROM catalogo_productos p JOIN catalogo_categorias c ON c.id=p.categoria_id WHERE p.id=?',(product_id,)).fetchone()
        if not current or not eligible(c,item_id,current['nombre'],current['categoria_id']):return False
        # Si un administrador renombró la categoría automática se respeta su nombre.
        state=c.execute('SELECT * FROM catalogo_ml_clasificacion WHERE item_id=?',(item_id,)).fetchone()
        if state and state['origen']=='manual':return False
        label=proposal['label']
        c.execute('INSERT OR IGNORE INTO catalogo_categorias(nombre) VALUES (?)',(label,))
        cat=c.execute('SELECT id FROM catalogo_categorias WHERE nombre=? COLLATE NOCASE',(label,)).fetchone()[0]
        changed=cat!=current['categoria_id']
        c.execute('UPDATE catalogo_productos SET categoria_id=? WHERE id=?',(cat,product_id))
        c.execute('''INSERT INTO catalogo_ml_clasificacion VALUES (?,?,?,0,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
          seller_id=excluded.seller_id,auto_categoria_id=excluded.auto_categoria_id,manual=0,categoria_ml=excluded.categoria_ml,
          ruta_ml=excluded.ruta_ml,origen=excluded.origen,actualizado=excluded.actualizado''',
          (item_id,seller,cat,proposal['categoria_ml'],proposal['ruta_ml'],proposal['origen'],datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return changed

    def mark_manual(c,product_id):
        r=c.execute('SELECT i.item_id,i.seller_id,p.categoria_id FROM catalogo_ml_items i JOIN catalogo_productos p ON p.id=i.producto_id WHERE p.id=?',(product_id,)).fetchone()
        if r:c.execute('''INSERT INTO catalogo_ml_clasificacion VALUES (?,?,?,1,'','','manual',?) ON CONFLICT(item_id) DO UPDATE SET manual=1,origen='manual',actualizado=excluded.actualizado''',(r['item_id'],r['seller_id'],r['categoria_id'],datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def plan(c):
        rows=c.execute('''SELECT i.item_id,i.seller_id,p.id,p.nombre,p.categoria_id,c.nombre actual,COALESCE(a.ruta_ml,'') ruta_ml,COALESCE(a.categoria_ml,'') categoria_ml
          FROM catalogo_ml_items i JOIN catalogo_productos p ON p.id=i.producto_id JOIN catalogo_categorias c ON c.id=p.categoria_id
          LEFT JOIN catalogo_ml_clasificacion a ON a.item_id=i.item_id WHERE i.seller_id=? ORDER BY p.id''',(ml['get']('seller'),)).fetchall()
        result=[];manual=0
        for r in rows:
            if not eligible(c,r['item_id'],r['actual'],r['categoria_id']):manual+=1;continue
            label=classify(r['nombre'],r['ruta_ml'])
            result.append({**dict(r),'label':label,'origen':'titulo' if classify(r['nombre'])!='Por clasificar' else ('mercadolibre' if label!='Por clasificar' else 'pendiente')})
        return result,manual

    bp=Blueprint('clasificacion',__name__)
    @bp.route('/admin/categorias/automaticas',methods=['GET','POST'])
    @private
    def automatic():
        with db() as c:
            plans,manual=plan(c)
            if request.method=='POST':
                changed=0
                for r in plans:changed+=apply(c,r['id'],r['item_id'],r['seller_id'],r)
        if request.method=='POST':
            flash(f'Clasificación terminada: {changed} productos cambiaron de categoría. Se conservaron {manual} categorías manuales. Revisa los productos Por clasificar.','success')
            return redirect(url_for('clasificacion.automatic'))
        counts=Counter(r['label'] for r in plans)
        return render_template('clasificacion.html',plans=plans[:100],total=len(plans),counts=sorted(counts.items()),manual=manual)

    app.register_blueprint(bp)
    app.extensions['clasificacion']={'prepare':prepare,'apply':apply,'mark_manual':mark_manual}
