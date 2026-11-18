"""Seguimiento de publicaciones y fotos observadas; nunca modifica Mercado Libre."""
import hashlib
import re
import uuid
from collections import Counter
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from flask import abort, flash, redirect, render_template, request, url_for
from mercadolibre import photo_url
from respaldos import StorageLock

ZONE=ZoneInfo('America/Santiago')
TABLES={
 'negocio_publicaciones':('seller_id','item_id','creada','actualizada_ml','huella','observada','primera_fecha','primera_orden','consultada_venta','vendidas','parent_item'),
 'negocio_publicacion_fotos':('seller_id','item_id','registro','orden','url','observada'),
}
SCHEMA='''
CREATE TABLE IF NOT EXISTS negocio_publicaciones (
 seller_id TEXT NOT NULL,item_id TEXT NOT NULL,creada TEXT,actualizada_ml TEXT,huella TEXT NOT NULL,observada TEXT NOT NULL,
 primera_fecha TEXT,primera_orden TEXT,consultada_venta TEXT,vendidas INTEGER,parent_item TEXT NOT NULL,PRIMARY KEY(seller_id,item_id));
CREATE TABLE IF NOT EXISTS negocio_publicacion_fotos (
 seller_id TEXT NOT NULL,item_id TEXT NOT NULL,registro TEXT NOT NULL,orden INTEGER NOT NULL,url TEXT NOT NULL,
 observada TEXT NOT NULL,PRIMARY KEY(seller_id,item_id,registro,orden));
'''


def moment(value):
    if not isinstance(value,str) or not value:return None
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        return d.astimezone(timezone.utc).isoformat(timespec='microseconds') if d.tzinfo is not None else None
    except ValueError:return None


def validate(rows):
    meta={(r['seller_id'],r['item_id']) for r in rows.get('negocio_publicaciones',[])}
    for table in TABLES:
        for r in rows.get(table,[]):
            if not re.fullmatch(r'MLC[0-9]{6,20}',r['item_id']):raise ValueError('Publicación inválida en historial.')
            if table=='negocio_publicaciones':
                if not re.fullmatch(r'[a-f0-9]{64}',r['huella']) or not moment(r['observada']):raise ValueError('Historial de publicación inválido.')
                for key in ('creada','actualizada_ml','primera_fecha','consultada_venta'):
                    if r[key] is not None and not moment(r[key]):raise ValueError('Fecha de publicación inválida.')
                if r['primera_orden'] is not None and not re.fullmatch(r'[0-9]{1,25}',r['primera_orden']):raise ValueError('Primera orden inválida.')
                if bool(r['primera_orden'])!=bool(r['primera_fecha']):raise ValueError('Primera venta incompleta.')
                if r['vendidas'] is not None and (type(r['vendidas']) is not int or not 0<=r['vendidas']<=10**9):raise ValueError('Cantidad vendida inválida.')
                if r['parent_item'] and not re.fullmatch(r'MLC[0-9]{6,20}',r['parent_item']):raise ValueError('Publicación de origen inválida.')
            else:
                if (r['seller_id'],r['item_id']) not in meta or not re.fullmatch(r'[a-f0-9]{32}',r['registro']) or type(r['orden']) is not int or not 0<=r['orden']<50 or not moment(r['observada']) or photo_url(r['url'])!=r['url']:raise ValueError('Foto de historial inválida.')


def install(app,db,ml,queue,bp,private,period,api):
    data=Path(app.config['DATA_DIR'])

    def record_item(c,item,photos):
        seller=str(item['seller_id']);iid=item['id'];now=datetime.now(timezone.utc).isoformat()
        old=c.execute('SELECT * FROM negocio_publicaciones WHERE seller_id=? AND item_id=?',(seller,iid)).fetchone()
        fingerprint=hashlib.sha256('\n'.join(photos).encode()).hexdigest()
        sold=item.get('sold_quantity');sold=sold if type(sold) is int and 0<=sold<=10**9 else None
        parent=str(item.get('parent_item_id') or '')
        if parent and not re.fullmatch(r'MLC[0-9]{6,20}',parent):parent=''
        c.execute('''INSERT INTO negocio_publicaciones VALUES (?,?,?,?,?,?,NULL,NULL,NULL,?,?)
          ON CONFLICT(seller_id,item_id) DO UPDATE SET creada=COALESCE(excluded.creada,creada),actualizada_ml=excluded.actualizada_ml,
          huella=excluded.huella,observada=excluded.observada,vendidas=excluded.vendidas,parent_item=excluded.parent_item''',
          (seller,iid,moment(item.get('date_created')),moment(item.get('last_updated')),fingerprint,now,sold,parent))
        if not old or old['huella']!=fingerprint:
            version=uuid.uuid4().hex
            c.executemany('INSERT INTO negocio_publicacion_fotos VALUES (?,?,?,?,?,?)',[(seller,iid,version,i,url,now) for i,url in enumerate(photos)])

    def record_order(c,order):
        seller=str((order.get('seller') or {}).get('id'));oid=str(order['id'])
        if order.get('status')!='paid':
            c.execute('UPDATE negocio_publicaciones SET primera_fecha=NULL,primera_orden=NULL,consultada_venta=NULL WHERE seller_id=? AND primera_orden=?',(seller,oid));return
        stamp=moment(order.get('date_closed'))
        if not stamp:return
        for line in order.get('order_items',[]):
            iid=str((line.get('item') or {}).get('id',''))
            c.execute('''UPDATE negocio_publicaciones SET primera_fecha=?,primera_orden=? WHERE seller_id=? AND item_id=?
              AND (primera_fecha IS NULL OR primera_fecha>?)''',(stamp,oid,seller,iid,stamp))

    def first_sale(iid):
        if not re.fullmatch(r'MLC[0-9]{6,20}',iid):raise ValueError('Publicación inválida.')
        seller=ml['get']('seller')
        with db() as c:
            known=c.execute('SELECT 1 FROM catalogo_ml_items i JOIN negocio_publicaciones m ON m.seller_id=i.seller_id AND m.item_id=i.item_id WHERE i.seller_id=? AND i.item_id=?',(seller,iid)).fetchone()
        if not known:raise ValueError('La publicación no pertenece al catálogo conectado.')
        query=urlencode({'seller':seller,'item':iid,'order.status':'paid','sort':'date_asc','limit':1})
        result=api('/orders/search?'+query,ml['access_token']())
        rows=result.get('results');paging=result.get('paging') or {}
        if not isinstance(rows,list) or type(paging.get('total')) is not int or paging['total']<0 or (paging['total']>0 and not rows):raise ValueError('La búsqueda de primeras ventas quedó incompleta.')
        first=None
        if rows:
            o=rows[0];oid=str(o.get('id',''));stamp=moment(o.get('date_closed'))
            if str((o.get('seller') or {}).get('id'))!=seller or o.get('status')!='paid' or not re.fullmatch(r'[0-9]{1,25}',oid) or not stamp or not any(str((l.get('item') or {}).get('id'))==iid for l in o.get('order_items',[])):
                raise ValueError('La primera venta devuelta no coincide con el vendedor y la publicación.')
            first=(stamp,oid)
        now=datetime.now(timezone.utc).isoformat()
        with StorageLock(data/'.catalogo.lock').acquire():
            with db() as c:
                if first:
                    c.execute('''UPDATE negocio_publicaciones SET primera_fecha=?,primera_orden=? WHERE seller_id=? AND item_id=?
                      AND (primera_fecha IS NULL OR primera_fecha>?)''',(*first,seller,iid,first[0]))
                c.execute('UPDATE negocio_publicaciones SET consultada_venta=? WHERE seller_id=? AND item_id=?',(now,seller,iid))

    def records(start,end):
        seller=ml['get']('seller');today=datetime.now(ZONE).date();counts=Counter();rows=[];unknown=0
        with db() as c:
            source=c.execute('''SELECT i.item_id,i.estado,p.nombre,p.enlace_ml,p.id producto_id,m.* FROM catalogo_ml_items i
              JOIN catalogo_productos p ON p.id=i.producto_id LEFT JOIN negocio_publicaciones m ON m.seller_id=i.seller_id AND m.item_id=i.item_id
              WHERE i.seller_id=?''',(seller,)).fetchall()
            photos={}
            for r in c.execute('SELECT i.item_id,f.url FROM catalogo_ml_fotos f JOIN catalogo_ml_items i ON i.producto_id=f.producto_id WHERE i.seller_id=? ORDER BY f.orden',(seller,)):photos.setdefault(r['item_id'],[]).append(r['url'])
        for raw in source:
            r=dict(raw)
            if not r['creada']:unknown+=1;continue
            created=datetime.fromisoformat(r['creada']);local=created.astimezone(ZONE).date()
            if not start<=local.isoformat()<=end:continue
            r.update(fotos=photos.get(r['item_id'],[]),fecha=local.isoformat(),dias_sin_venta=max(0,(today-local).days),demora=None)
            r['historial_limitado']=local<today-timedelta(days=365) or bool(r['parent_item'])
            if r['primera_fecha']:
                delta=(datetime.fromisoformat(r['primera_fecha'])-created).total_seconds()/86400
                if delta>=0:r['demora']=round(delta,2)
            counts[r['fecha']]+=1;rows.append(r)
        rows.sort(key=lambda r:r['creada'],reverse=True)
        sample=[r['demora'] for r in rows if r['demora'] is not None and r['consultada_venta'] and not r['historial_limitado']]
        days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1
        return dict(rows=rows,total=len(rows),average=round(len(rows)/days,2),days=days,unknown=unknown,daily=sorted(counts.items()),
          delay=round(sum(sample)/len(sample),2) if sample else None,sample=len(sample))

    def format_date(value):
        return datetime.fromisoformat(value).astimezone(ZONE).strftime('%d/%m/%Y %H:%M') if value else 'Pendiente'

    @bp.get('/admin/publicaciones')
    @private
    def publications_page():
        start,end=period();r=records(start,end)
        with ml['state_db']() as c:
            errors=c.execute("SELECT recurso,error FROM reportes_pendientes WHERE tipo='primera_venta' AND error!='' LIMIT 8").fetchall()
            pending=c.execute("SELECT COUNT(*) FROM reportes_pendientes WHERE tipo='primera_venta'").fetchone()[0]
        return render_template('publicaciones.html',r=r,desde=start,hasta=end,format_date=format_date,pending=pending,errors=errors)

    @bp.post('/admin/publicaciones/actualizar')
    @private
    def refresh_publications():
        start,end=period();seller=ml['get']('seller')
        if not seller or not ml['get']('refresh'):abort(400,'Conecta primero tu cuenta de Mercado Libre.')
        try:
            with StorageLock(data/'.mercadolibre_operation.lock').acquire(timeout=0):
                with db() as c:ids=[r[0] for r in c.execute('SELECT item_id FROM catalogo_ml_items WHERE seller_id=?',(seller,))]
                if request.form.get('modo')=='ventas':
                    ids=[r['item_id'] for r in records(start,end)['rows']]
                    with ml['state_db']() as c:
                        for iid in ids:queue('primera_venta',iid,c)
                    flash(f'Consulta de primeras ventas solicitada para {len(ids)} publicaciones del período.','success')
                else:
                    ml['enqueue'](ids);ml['put']('scan_requested','1')
                    flash(f'Actualización de fechas y fotos solicitada para {len(ids)} publicaciones; también se buscarán publicaciones nuevas.','success')
        except TimeoutError:flash('Hay una sincronización en curso. Espera unos segundos y vuelve a intentar.','error')
        except ValueError as e:flash(str(e),'error')
        return redirect(url_for('reportes.publications_page',desde=start,hasta=end))

    @bp.get('/admin/publicaciones/<iid>/fotos')
    @private
    def photo_history(iid):
        seller=ml['get']('seller')
        with db() as c:
            item=c.execute('SELECT p.nombre FROM catalogo_ml_items i JOIN catalogo_productos p ON p.id=i.producto_id WHERE i.seller_id=? AND i.item_id=?',(seller,iid)).fetchone()
            if not item:abort(404)
            photos=c.execute('SELECT * FROM negocio_publicacion_fotos WHERE seller_id=? AND item_id=? ORDER BY observada DESC,registro,orden',(seller,iid)).fetchall()
        versions={}
        for p in photos:versions.setdefault(p['registro'],{'observada':p['observada'],'urls':[]})['urls'].append(p['url'])
        return render_template('publicacion_fotos.html',nombre=item['nombre'],iid=iid,versions=list(versions.values()),format_date=format_date)

    app.extensions['publicaciones']={'record_item':record_item,'record_order':record_order,'first_sale':first_sale,'records':records}
    return app.extensions['publicaciones']
