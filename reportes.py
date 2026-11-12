"""Estadísticas propias y gestión económica. Nunca escribe en Mercado Libre."""
import csv
import hashlib
import io
import re
import sqlite3
import time
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from mercadolibre import MeliError, api_request
from respaldos import StorageLock

ZONE = ZoneInfo('America/Santiago')
# Importes monetarios en centésimas de CLP para no perder decimales de comisiones.
TABLES = {
 'negocio_ventas': ('id','seller_id','fecha','estado','moneda','total','comision','envio_id','actualizado'),
 'negocio_lineas': ('venta_id','numero','item_id','variante','nombre','cantidad','precio','costo'),
 'negocio_envios': ('seller_id','id','costo','actualizado'),
 'negocio_costos': ('seller_id','item_id','variante','costo'),
 'negocio_gastos': ('id','fecha','categoria','descripcion','monto'),
 'catalogo_metricas': ('clave','fecha','tipo','producto_id','detalle','cantidad'),
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS negocio_ventas (
 id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, fecha TEXT NOT NULL, estado TEXT NOT NULL,
 moneda TEXT NOT NULL, total INTEGER NOT NULL CHECK(total>=0), comision INTEGER,
 envio_id TEXT NOT NULL, actualizado TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS negocio_lineas (
 venta_id TEXT NOT NULL REFERENCES negocio_ventas(id) ON DELETE CASCADE, numero INTEGER NOT NULL,
 item_id TEXT NOT NULL, variante TEXT NOT NULL, nombre TEXT NOT NULL, cantidad INTEGER NOT NULL CHECK(cantidad>0),
 precio INTEGER NOT NULL CHECK(precio>=0), costo INTEGER CHECK(costo>=0), PRIMARY KEY(venta_id,numero));
CREATE TABLE IF NOT EXISTS negocio_envios (
 seller_id TEXT NOT NULL, id TEXT NOT NULL, costo INTEGER CHECK(costo>=0), actualizado TEXT NOT NULL,
 PRIMARY KEY(seller_id,id));
CREATE TABLE IF NOT EXISTS negocio_costos (
 seller_id TEXT NOT NULL, item_id TEXT NOT NULL, variante TEXT NOT NULL, costo INTEGER NOT NULL CHECK(costo>=0),
 PRIMARY KEY(seller_id,item_id,variante));
CREATE TABLE IF NOT EXISTS negocio_gastos (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, categoria TEXT NOT NULL,
 descripcion TEXT NOT NULL, monto INTEGER NOT NULL CHECK(monto>0));
CREATE TABLE IF NOT EXISTS catalogo_metricas (
 clave TEXT PRIMARY KEY, fecha TEXT NOT NULL, tipo TEXT NOT NULL, producto_id INTEGER NOT NULL DEFAULT 0,
 detalle TEXT NOT NULL, cantidad INTEGER NOT NULL CHECK(cantidad>=0));
'''
GASTOS = ('Página web y dominio','Personal','Publicidad','Embalaje','Envíos adicionales','Devoluciones y ajustes','Otros')
from desglose_envios import TABLES as SHIPPING_TABLES, SCHEMA as SHIPPING_SCHEMA, validate as validate_shipping
TABLES.update(SHIPPING_TABLES)
SCHEMA+=SHIPPING_SCHEMA


def money(value):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d>Decimal('1000000000000'):
            raise ValueError()
        return int((d*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError('Ingresa un monto válido, sin puntos de miles y con punto para decimales.') from None


def day(value):
    return date.fromisoformat(value).isoformat()


def local_day(value):
    result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('La fecha de la venta no incluye zona horaria.')
    return result.astimezone(ZONE).date().isoformat()


def validate_backup(rows):
    """Validar datos de tablas conocidas, sin ejecutar estructuras SQL del archivo."""
    validate_shipping(rows)
    sales={r['id'] for r in rows.get('negocio_ventas',[])}
    def ident(v): return isinstance(v,str) and re.fullmatch(r'[0-9]{1,25}',v)
    def amount(v,nullable=False): return (nullable and v is None) or (type(v) is int and 0<=v<=10**16)
    for table in TABLES:
        for r in rows.get(table,[]):
            if set(r)!=set(TABLES[table]): raise ValueError('Columnas de reportes incompatibles.')
            if any(isinstance(v,str) and len(v)>300 for v in r.values()): raise ValueError('Dato de reporte demasiado largo.')
            if 'fecha' in r: day(r['fecha'])
            if 'seller_id' in r and not ident(r['seller_id']): raise ValueError('Vendedor no válido en reporte.')
            for key in ('costo','comision','total','precio','monto'):
                if key in r and not amount(r[key],key in ('costo','comision')): raise ValueError('Monto de reporte inválido.')
            if table=='negocio_ventas' and (not ident(r['id']) or r['moneda']!='CLP' or (r['envio_id'] and not ident(r['envio_id']))): raise ValueError('Venta inválida en respaldo.')
            if table=='negocio_lineas' and (r['venta_id'] not in sales or not re.fullmatch(r'MLC[0-9]{6,20}',r['item_id']) or (r['variante'] and not ident(r['variante'])) or type(r['numero']) is not int or not 0<=r['numero']<1000 or type(r['cantidad']) is not int or not 1<=r['cantidad']<=999999): raise ValueError('Detalle de venta inválido.')
            if table=='negocio_costos' and (not re.fullmatch(r'MLC[0-9]{6,20}',r['item_id']) or (r['variante'] and not ident(r['variante'])) or r['costo'] is None): raise ValueError('Costo inválido.')
            if table=='negocio_envios' and not ident(r['id']): raise ValueError('Envío inválido.')
            if table=='negocio_gastos' and (type(r['id']) is not int or r['id']<1 or r['categoria'] not in GASTOS or r['monto']<=0): raise ValueError('Gasto inválido.')
            if table=='catalogo_metricas' and (r['tipo'] not in ('visita','busqueda','ml','wa') or type(r['cantidad']) is not int or r['cantidad']<0 or type(r['producto_id']) is not int or r['producto_id']<0): raise ValueError('Estadística inválida.')


def install(app,db,private,wa_link,limiter):
    ml=app.extensions['mercadolibre']
    data=Path(app.config['DATA_DIR'])
    with db() as c: c.executescript(SCHEMA)
    with ml['state_db']() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS reportes_pendientes (
          clave TEXT PRIMARY KEY,tipo TEXT NOT NULL,recurso TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
          intentos INTEGER NOT NULL DEFAULT 0,proximo REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '')''')
    bp=Blueprint('reportes',__name__)
    recent=OrderedDict()
    recent_guard=threading.Lock()

    def queue(kind,resource,c=None):
        resource=str(resource)
        key=kind+':'+resource
        def put(conn):
            if conn.execute('SELECT COUNT(*) FROM reportes_pendientes').fetchone()[0]>=20000 and not conn.execute('SELECT 1 FROM reportes_pendientes WHERE clave=?',(key,)).fetchone():
                raise ValueError('La cola de ventas está llena. Reintenta más tarde.')
            conn.execute('''INSERT INTO reportes_pendientes(clave,tipo,recurso) VALUES (?,?,?)
              ON CONFLICT(clave) DO UPDATE SET version=version+1,intentos=0,proximo=0,error='' ''',(key,kind,resource))
        if c is not None: put(c)
        else:
            with ml['state_db']() as conn: put(conn)

    def import_order(oid):
        order=api_request('/orders/'+oid,ml['access_token']())
        seller=ml['get']('seller')
        if str(order.get('id'))!=oid or str((order.get('seller') or {}).get('id'))!=seller:
            raise ValueError('La venta no pertenece al vendedor conectado.')
        if order.get('currency_id')!='CLP': raise ValueError('Esta versión admite ventas en CLP.')
        fecha=local_day(order.get('date_closed') or order.get('date_created'))
        lines=order.get('order_items')
        if not isinstance(lines,list) or not 1<=len(lines)<=999: raise ValueError('Detalle de venta no compatible.')
        parsed=[]; fees=0; complete=True
        for i,line in enumerate(lines):
            item=line.get('item') or {}; item_id=str(item.get('id','')); variant=str(item.get('variation_id') or '')
            if not re.fullmatch(r'MLC[0-9]{6,20}',item_id) or (variant and not variant.isdigit()): raise ValueError('Producto de venta no compatible.')
            qty=line.get('quantity')
            if type(qty) is not int or not 1<=qty<=999999: raise ValueError('Cantidad de venta no compatible.')
            price=money(line.get('unit_price'))
            if line.get('sale_fee') is None: complete=False
            else: fees+=money(line['sale_fee'])*qty
            title=str(item.get('title') or item_id)[:250]
            parsed.append((i,item_id,variant,title,qty,price))
        ship=str((order.get('shipping') or {}).get('id') or '')
        if ship and not re.fullmatch(r'[0-9]{1,25}',ship): raise ValueError('Identificador de envío inválido.')
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with StorageLock(data/'.catalogo.lock').acquire():
            with db() as c:
                previous={r['numero']:dict(r) for r in c.execute('SELECT * FROM negocio_lineas WHERE venta_id=?',(oid,))}
                c.execute('''INSERT INTO negocio_ventas VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                  fecha=excluded.fecha,estado=excluded.estado,total=excluded.total,comision=excluded.comision,
                  envio_id=excluded.envio_id,actualizado=excluded.actualizado''',
                  (oid,seller,fecha,str(order.get('status',''))[:40],'CLP',money(order.get('total_amount')),fees if complete else None,ship,now))
                c.execute('DELETE FROM negocio_lineas WHERE venta_id=?',(oid,))
                for i,item_id,variant,title,qty,price in parsed:
                    old=previous.get(i)
                    cost=old['costo'] if old and old['item_id']==item_id and old['variante']==variant else None
                    if cost is None:
                        saved=c.execute('SELECT costo FROM negocio_costos WHERE seller_id=? AND item_id=? AND variante=?',(seller,item_id,variant)).fetchone()
                        cost=saved[0] if saved else None
                    c.execute('INSERT INTO negocio_lineas VALUES (?,?,?,?,?,?,?,?)',(oid,i,item_id,variant,title,qty,price,cost))
                if ship:
                    c.execute('INSERT OR IGNORE INTO negocio_envios VALUES (?,?,NULL,?)',(seller,ship,now))
        if ship: queue('envio',ship)

    def start_scan(start,end):
        with ml['state_db']() as c:
            for k,v in {'ventas_desde':start,'ventas_hasta':end,'ventas_dia':start,'ventas_offset':0,'ventas_scan':1,'ventas_retry':0,'ventas_error':'','ventas_enabled':1}.items():
                c.execute('INSERT OR REPLACE INTO estado VALUES (?,?)',(k,str(v)))

    def scan_page():
        current=day(ml['get']('ventas_dia')); end=day(ml['get']('ventas_hasta')); offset=int(ml['get']('ventas_offset','0'))
        d=date.fromisoformat(current)
        start_dt=datetime.combine(d,datetime.min.time(),ZONE)
        end_dt=datetime.combine(d+timedelta(days=1),datetime.min.time(),ZONE)-timedelta(milliseconds=1)
        params={'seller':ml['get']('seller'),'order.date_created.from':start_dt.isoformat(timespec='milliseconds'),
         'order.date_created.to':end_dt.isoformat(timespec='milliseconds'),'sort':'date_desc','limit':50,'offset':offset}
        result=api_request('/orders/search?'+urlencode(params),ml['access_token']())
        orders=result.get('results'); total=(result.get('paging') or {}).get('total')
        if not isinstance(orders,list) or type(total) is not int or total<0: raise ValueError('Respuesta de ventas no compatible.')
        if total>10000: raise ValueError('Más de 10.000 ventas en un día: requiere importación con intervalos menores.')
        ids=[str(o.get('id','')) for o in orders]
        if any(not re.fullmatch(r'[0-9]{1,25}',oid) for oid in ids): raise ValueError('Identificador de venta inválido.')
        if not ids and offset<total: raise ValueError('Mercado Libre no devolvió la siguiente página de ventas.')
        with ml['state_db']() as c:
            for oid in ids: queue('venta',oid,c)
            if offset+len(ids)<total:
                c.execute("INSERT OR REPLACE INTO estado VALUES ('ventas_offset',?)",(str(offset+len(ids)),))
            elif current<end:
                c.execute("INSERT OR REPLACE INTO estado VALUES ('ventas_dia',?)",((d+timedelta(days=1)).isoformat(),))
                c.execute("INSERT OR REPLACE INTO estado VALUES ('ventas_offset','0')")
            else:
                for k,v in {'ventas_scan':'0','ventas_ultima':datetime.now(timezone.utc).isoformat(timespec='seconds'),'ventas_auto':time.time()}.items():
                    c.execute('INSERT OR REPLACE INTO estado VALUES (?,?)',(k,str(v)))

    def tick():
        if not ml['get']('refresh'): return False
        with ml['state_db']() as c:
            job=c.execute('SELECT * FROM reportes_pendientes WHERE proximo<=? ORDER BY proximo,clave LIMIT 1',(time.time(),)).fetchone()
        if job:
            try:
                handler={'venta':import_order,'envio':import_shipping,'facturacion':shipping_features['import_billing']}.get(job['tipo'])
                if not handler:raise ValueError('Tarea no compatible.')
                handler(job['recurso'])
                with ml['state_db']() as c: c.execute('DELETE FROM reportes_pendientes WHERE clave=? AND version=?',(job['clave'],job['version']))
            except (MeliError,ValueError,sqlite3.Error,TimeoutError,OSError) as e:
                message=str(e) if isinstance(e,(MeliError,ValueError)) else 'No se pudo guardar la venta o el envío.'
                with ml['state_db']() as c:
                    c.execute('UPDATE reportes_pendientes SET intentos=intentos+1,proximo=?,error=? WHERE clave=? AND version=?',
                     (time.time()+min(3600,30*2**min(job['intentos'],7)),message[:280],job['clave'],job['version']))
            return True
        if ml['get']('ventas_scan')=='1' and float(ml['get']('ventas_retry','0'))<=time.time():
            try: scan_page(); ml['put']('ventas_error','')
            except (MeliError,ValueError,sqlite3.Error,TimeoutError,OSError) as e:
                ml['put']('ventas_error',str(e)[:280] if isinstance(e,(MeliError,ValueError)) else 'Error temporal al importar ventas.')
                ml['put']('ventas_retry',time.time()+300)
                return False
            return True
        if ml['get']('ventas_enabled')=='1' and ml['get']('ventas_scan')!='1' and time.time()-float(ml['get']('ventas_auto','0'))>1800:
            today=datetime.now(ZONE).date()
            start_scan((today-timedelta(days=29)).isoformat(),today.isoformat())
        return False

    def notification(body):
        topic=body.get('topic'); resource=body.get('resource','')
        kind='venta' if topic in ('orders','orders_v2') else 'envio'
        match=re.fullmatch(r'/orders/([0-9]{1,25})' if kind=='venta' else r'/shipments/([0-9]{1,25})',resource) if isinstance(resource,str) else None
        if not match: return {'error':'recurso no válido'},400
        try:
            with ml['state_db'](timeout=0.1) as c:
                c.execute('BEGIN IMMEDIATE')
                seller=c.execute("SELECT valor FROM estado WHERE clave='seller'").fetchone()
                if not seller: return {'error':'cuenta aún no conectada'},503
                if str(body.get('user_id'))!=seller[0]: return {'error':'cuenta no autorizada'},403
                queue(kind,match[1],c)
        except (sqlite3.Error,ValueError): return {'error':'reintentar'},503
        return {'estado':'recibido'}

    @app.template_filter('dinero')
    def currency(cents):
        if cents is None: return 'Pendiente'
        n=Decimal(str(cents))/100
        return '$'+format(n,',.2f').replace(',','X').replace('.',',').replace('X','.')

    def period():
        today=datetime.now(ZONE).date()
        try:
            start=day(request.args.get('desde',today.replace(day=1).isoformat()))
            end=day(request.args.get('hasta',today.isoformat()))
            if start>end: raise ValueError()
            return start,end
        except ValueError: abort(400,'Revisa las fechas del período.')

    def report(start,end):
        seller=ml['get']('seller')
        with db() as c:
            sales=[dict(r) for r in c.execute('SELECT * FROM negocio_ventas WHERE seller_id=? AND fecha BETWEEN ? AND ? ORDER BY fecha DESC,id DESC',(seller,start,end))]
            paid=[r for r in sales if r['estado']=='paid']
            lines=[dict(r) for r in c.execute('''SELECT l.*,v.fecha,v.total total_orden FROM negocio_lineas l JOIN negocio_ventas v ON v.id=l.venta_id
              WHERE v.seller_id=? AND v.fecha BETWEEN ? AND ? AND v.estado='paid' ''',(seller,start,end))]
            shipments=[dict(r) for r in c.execute('''SELECT DISTINCT e.* FROM negocio_envios e JOIN negocio_ventas v ON v.envio_id=e.id AND v.seller_id=e.seller_id
              WHERE v.seller_id=? AND v.fecha BETWEEN ? AND ? AND v.estado='paid' ''',(seller,start,end))]
            all_paid=[dict(r) for r in c.execute("SELECT id,total,envio_id FROM negocio_ventas WHERE seller_id=? AND estado='paid'",(seller,))]
            expenses=[dict(r) for r in c.execute('SELECT * FROM negocio_gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC,id DESC',(start,end))]
            metrics=[dict(r) for r in c.execute('SELECT * FROM catalogo_metricas WHERE fecha BETWEEN ? AND ?',(start,end))]
            financial=shipping_features['economic_details'](c,seller,sales,all_paid)
        gross=sum(r['total'] for r in paid); fees=sum(r['comision'] or 0 for r in paid)
        costs=sum(r['cantidad']*r['costo'] for r in lines if r['costo'] is not None)
        shipping=financial['regular']+financial['carrier']; credit=financial['credit']; expense=sum(r['monto'] for r in expenses)
        missing_cost=sum(r['cantidad'] for r in lines if r['costo'] is None)
        missing_fees=sum(r['comision'] is None for r in paid); missing_ship=financial['missing_ship']; missing_flex=financial['missing_flex']
        incomplete=bool(missing_cost or missing_fees or missing_ship or missing_flex)
        top={}; daily={}; categories={}
        for r in lines:
            key=(r['item_id'],r['variante'])
            item=top.setdefault(key,{'nombre':r['nombre'],'item_id':key[0],'variante':key[1],'unidades':0,'importe':0})
            item['unidades']+=r['cantidad']; item['importe']+=r['cantidad']*r['precio']
        for r in paid: daily[r['fecha']]=daily.get(r['fecha'],0)+r['total']
        for r in expenses: categories[r['categoria']]=categories.get(r['categoria'],0)+r['monto']
        rankings={kind:[] for kind in ('visita','busqueda','ml','wa')}
        for kind in rankings:
            group={}
            for r in metrics:
                if r['tipo']==kind:
                    key=(r['producto_id'],r['detalle']); group[key]=group.get(key,0)+r['cantidad']
            rankings[kind]=[{'detalle':key[1],'cantidad':value} for key,value in sorted(group.items(),key=lambda p:p[1],reverse=True)[:15]]
        return dict(sales=sales,paid=paid,expenses=expenses,gross=gross,fees=fees,costs=costs,shipping=shipping,expense=expense,
         result=None if incomplete else gross-fees-costs-shipping+credit-expense,missing_cost=missing_cost,missing_fees=missing_fees,missing_ship=missing_ship,missing_flex=missing_flex,flex_credit=credit,carrier=financial['carrier'],regular_shipping=financial['regular'],sale_details=financial['rows'],
         top=sorted(top.values(),key=lambda r:r['unidades'],reverse=True)[:20],daily=sorted(daily.items()),expense_categories=categories,
         rankings=rankings,units=sum(r['cantidad'] for r in lines),metric_totals={k:sum(r['cantidad'] for r in metrics if r['tipo']==k) for k in rankings})

    @bp.get('/admin/reportes')
    @private
    def dashboard():
        start,end=period()
        with ml['state_db']() as c:
            pending=c.execute('SELECT COUNT(*) FROM reportes_pendientes').fetchone()[0]
            errors=c.execute("SELECT recurso,error FROM reportes_pendientes WHERE error!='' LIMIT 8").fetchall()
        return render_template('reportes.html',desde=start,hasta=end,today=datetime.now(ZONE).date().isoformat(),r=report(start,end),
          seller=ml['get']('seller'),pending=pending,errors=errors,scanning=ml['get']('ventas_scan')=='1',scan_day=ml['get']('ventas_dia'),
          scan_error=ml['get']('ventas_error'),last_scan=ml['get']('ventas_ultima'))

    @bp.post('/admin/reportes/importar')
    @private
    def sync():
        try:
            start=day(request.form.get('desde','')); end=day(request.form.get('hasta',''))
            if start>end or (date.fromisoformat(end)-date.fromisoformat(start)).days>366: raise ValueError('Importa un máximo de 366 días por vez.')
            if not ml['get']('refresh'): raise ValueError('Conecta tu cuenta de Mercado Libre primero.')
            # La petición web solo agenda. La API se consulta fuera de esta respuesta.
            try:
                with StorageLock(data/'.mercadolibre_operation.lock').acquire(timeout=0):
                    start_scan(start,end)
                    with ml['state_db']() as c: c.execute("UPDATE reportes_pendientes SET proximo=0,intentos=0,error='' ")
            except TimeoutError:
                raise ValueError('Hay una sincronización en curso. Espera unos segundos y vuelve a solicitar la importación.')
            flash('Importación de ventas solicitada. Se procesa en segundo plano.','success')
        except ValueError as e: flash(str(e) or 'Revisa las fechas.','error')
        return redirect(url_for('reportes.dashboard'))

    @bp.route('/admin/costos',methods=['GET','POST'])
    @private
    def costs_page():
        seller=ml['get']('seller')
        if request.method=='POST':
            try:
                selection=request.form.get('producto','')
                if '|' not in selection: raise ValueError('Selecciona un producto válido.')
                item,variant=selection.split('|',1)
                value=money(request.form.get('costo',''))
                if not seller or not re.fullmatch(r'MLC[0-9]{6,20}',item) or (variant and not re.fullmatch(r'[0-9]{1,25}',variant)): raise ValueError('Selecciona un producto válido.')
                with db() as c:
                    known=c.execute('SELECT 1 FROM catalogo_ml_items WHERE seller_id=? AND item_id=? UNION SELECT 1 FROM negocio_lineas l JOIN negocio_ventas v ON v.id=l.venta_id WHERE v.seller_id=? AND l.item_id=? LIMIT 1',(seller,item,seller,item)).fetchone()
                    if not known: raise ValueError('Producto no encontrado en esta cuenta.')
                    c.execute('INSERT OR REPLACE INTO negocio_costos VALUES (?,?,?,?)',(seller,item,variant,value))
                    if request.form.get('aplicar')=='si':
                        c.execute('''UPDATE negocio_lineas SET costo=? WHERE item_id=? AND variante=? AND costo IS NULL AND venta_id IN
                          (SELECT id FROM negocio_ventas WHERE seller_id=?)''',(value,item,variant,seller))
                flash('Costo guardado. Los costos históricos ya asignados se conservan.','success')
            except ValueError as e: flash(str(e),'error')
            return redirect(url_for('reportes.costs_page'))
        with db() as c:
            options=c.execute('''SELECT DISTINCT l.item_id,l.variante,l.nombre FROM negocio_lineas l JOIN negocio_ventas v ON v.id=l.venta_id WHERE v.seller_id=?
              UNION SELECT i.item_id,'' variante,p.nombre FROM catalogo_ml_items i JOIN catalogo_productos p ON p.id=i.producto_id WHERE i.seller_id=? ORDER BY nombre''',(seller,seller)).fetchall()
            costs=c.execute('SELECT * FROM negocio_costos WHERE seller_id=? ORDER BY item_id,variante',(seller,)).fetchall()
        return render_template('costos.html',options=options,costs=costs)

    @bp.route('/admin/gastos',methods=['GET','POST'])
    @private
    def expenses_page():
        if request.method=='POST':
            try:
                fecha=day(request.form.get('fecha','')); category=request.form.get('categoria',''); description=request.form.get('descripcion','').strip()[:250]
                amount=money(request.form.get('monto',''))
                if category not in GASTOS or amount<=0 or not description: raise ValueError('Completa fecha, categoría, descripción y monto positivo.')
                with db() as c: c.execute('INSERT INTO negocio_gastos(fecha,categoria,descripcion,monto) VALUES (?,?,?,?)',(fecha,category,description,amount))
                flash('Gasto registrado.','success')
            except ValueError as e: flash(str(e) or 'Fecha no válida.','error')
            return redirect(url_for('reportes.expenses_page'))
        start,end=period()
        return render_template('gastos.html',desde=start,hasta=end,today=datetime.now(ZONE).date().isoformat(),categories=GASTOS,r=report(start,end))

    @bp.post('/admin/gastos/<int:expense_id>/eliminar')
    @private
    def remove_expense(expense_id):
        with db() as c: c.execute('DELETE FROM negocio_gastos WHERE id=?',(expense_id,))
        flash('Gasto eliminado.','success')
        return redirect(url_for('reportes.expenses_page'))

    def safe_csv(value):
        if isinstance(value,(int,Decimal)):
            return str(value)
        text=str(value if value is not None else '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text

    @bp.get('/admin/reportes/descargar/<kind>')
    @private
    def export(kind):
        start,end=period(); r=report(start,end)
        if kind=='ventas':
            header=('Venta','Fecha Chile','Estado','Moneda','Venta CLP','Comisión CLP','Comisión efectiva %','Modalidad','Envío ID','Cliente pagó envío completo CLP','Tu cargo de envío asignado CLP','Bonificación Flex asignada CLP','Repartidor asignado CLP','Neto operativo estimado CLP','Costo producto conocido CLP','Ganancia estimada antes de gastos generales CLP')
            def pesos(value):return '' if value is None else Decimal(value)/100
            rows=[(s['id'],s['fecha'],s['estado'],'CLP',pesos(s['total']),pesos(s['comision']),s['comision_pct'],s['tipo'],s['envio_id'],pesos(s['envio_cliente']),pesos(s['envio_vendedor']),pesos(s['flex_credit']),pesos(s['repartidor']),pesos(s['neto_estimado']),pesos(s['costo_producto']),pesos(s['ganancia'])) for s in r['sale_details']]
        elif kind=='gastos':
            header=('Fecha','Categoría','Descripción','Monto CLP'); rows=[(s['fecha'],s['categoria'],s['descripcion'],Decimal(s['monto'])/100) for s in r['expenses']]
        elif kind=='productos':
            header=('Publicación','Variante','Producto','Unidades pagadas','Importe líneas CLP'); rows=[(s['item_id'],s['variante'],s['nombre'],s['unidades'],Decimal(s['importe'])/100) for s in r['top']]
            # Exportar todas las líneas agrupadas, sin el límite visual de 20 productos.
            with db() as c:
                rows=[(s[0],s[1],s[2],s[3],Decimal(s[4])/100) for s in c.execute('''SELECT l.item_id,l.variante,MAX(l.nombre),SUM(l.cantidad),SUM(l.cantidad*l.precio)
                  FROM negocio_lineas l JOIN negocio_ventas v ON v.id=l.venta_id WHERE v.seller_id=? AND v.fecha BETWEEN ? AND ? AND v.estado='paid'
                  GROUP BY l.item_id,l.variante ORDER BY SUM(l.cantidad) DESC''',(ml['get']('seller'),start,end))]
        elif kind=='resumen':
            header=('Concepto','Valor CLP o cantidad'); rows=[('Ventas pagadas',Decimal(r['gross'])/100),('Comisiones conocidas',Decimal(r['fees'])/100),('Mercadería con costo asignado',Decimal(r['costs'])/100),('Envíos ML conocidos',Decimal(r['regular_shipping'])/100),('Repartidor Flex conocido',Decimal(r['carrier'])/100),('Bonificación Flex neta registrada',Decimal(r['flex_credit'])/100),('Gastos',Decimal(r['expense'])/100),('Resultado estimado','INCOMPLETO' if r['result'] is None else Decimal(r['result'])/100),('Unidades sin costo',r['missing_cost']),('Ventas sin comisión',r['missing_fees']),('Envíos sin costo',r['missing_ship']),('Flex incompletos',r['missing_flex'])]
        else: abort(404)
        stream=io.StringIO(newline=''); writer=csv.writer(stream,delimiter=';'); writer.writerow(header)
        writer.writerows([[safe_csv(v) for v in row] for row in rows])
        return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{kind}_{start}_{end}.csv"'})

    def record(kind,product_id,detail):
        if session.get('admin') or request.method!='GET': return
        ua=request.headers.get('User-Agent','').lower()
        if any(x in ua for x in ('bot','spider','crawler','headless','preview','facebookexternalhit')): return
        now=time.monotonic(); detail=detail[:250]
        fingerprint=hashlib.sha256((request.remote_addr or '').encode()+ua.encode()).hexdigest()
        key=(fingerprint,kind,product_id,detail)
        # Conteos aproximados. No persistimos IPs, cookies de seguimiento ni identificadores personales.
        with recent_guard:
            if now-recent.get(key,float('-inf'))< (10 if kind in ('ml','wa') else 60): return
            recent[key]=now; recent.move_to_end(key)
            while len(recent)>5000: recent.popitem(last=False)
        today=datetime.now(ZONE).date().isoformat(); metric_key=hashlib.sha256(f'{today}|{kind}|{product_id}|{detail}'.encode()).hexdigest()
        try:
            with StorageLock(data/'.catalogo.lock').acquire(timeout=0.05):
                with sqlite3.connect(app.config['DATABASE'],timeout=0.1) as c:
                    c.execute('''INSERT INTO catalogo_metricas VALUES (?,?,?,?,?,1) ON CONFLICT(clave) DO UPDATE SET cantidad=cantidad+1''',(metric_key,today,kind,product_id,detail))
        except (OSError,sqlite3.Error,TimeoutError): pass  # La navegación nunca depende de las métricas.

    @app.after_request
    def measure(response):
        if response.status_code!=200: return response
        if request.endpoint=='product':
            pid=request.view_args['product_id']
            with db() as c: p=c.execute('SELECT nombre FROM catalogo_productos WHERE id=?',(pid,)).fetchone()
            if p: record('visita',pid,p[0])
        elif request.endpoint=='catalog':
            term=' '.join(request.args.get('q','').strip().casefold().split())[:100]
            if term: record('busqueda',0,term)
        return response

    @bp.get('/ir/<int:product_id>/<channel>')
    @limiter.limit('120 per minute')
    def outbound(product_id,channel):
        with db() as c: p=c.execute('SELECT * FROM catalogo_productos WHERE id=? AND activo=1',(product_id,)).fetchone()
        if not p or channel not in ('ml','wa'): abort(404)
        target=p['enlace_ml'] if channel=='ml' else wa_link(p)
        if not target: abort(404)
        parts=urlsplit(target)
        allowed=(parts.scheme=='https' and not parts.username and not parts.password and parts.port in (None,443) and
          ((channel=='wa' and parts.hostname=='wa.me') or (channel=='ml' and (parts.hostname in ('mercadolibre.cl','mercadolibre.com') or (parts.hostname or '').endswith('.mercadolibre.cl')))))
        if not allowed: abort(400)
        record(channel,product_id,p['nombre'])
        response=redirect(target); response.headers['Cache-Control']='no-store'
        return response

    from desglose_envios import install as install_shipping
    shipping_features=install_shipping(app,db,ml,queue,money,bp,private,period,lambda *a,**kw:api_request(*a,**kw))
    import_shipping=shipping_features['import_shipping']
    app.register_blueprint(bp)
    app.extensions['reportes']={'tick':tick,'notification':notification,'import_order':import_order,'import_shipping':import_shipping,'import_billing':shipping_features['import_billing'],'start_scan':start_scan,'report':report,'queue':queue}
