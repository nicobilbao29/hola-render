"""Lectura de logística y bonificaciones Flex; costo externo del transportista separado."""
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from flask import abort, flash, redirect, render_template, request, url_for
from respaldos import StorageLock

TABLES={
 'negocio_envio_detalle':('seller_id','id','tipo','cliente','bruto','bonificacion_info','repartidor','actualizado'),
 'negocio_flex_cargos':('seller_id','id','envio_id','periodo','documento','tipo','importe','estado','descripcion','actualizado'),
 'negocio_flex_control':('seller_id','periodo','documento','completo','actualizado'),
}
SCHEMA='''
CREATE TABLE IF NOT EXISTS negocio_envio_detalle (
 seller_id TEXT NOT NULL,id TEXT NOT NULL,tipo TEXT NOT NULL,cliente INTEGER,bruto INTEGER,
 bonificacion_info INTEGER,repartidor INTEGER,actualizado TEXT NOT NULL,PRIMARY KEY(seller_id,id));
CREATE TABLE IF NOT EXISTS negocio_flex_cargos (
 seller_id TEXT NOT NULL,id TEXT NOT NULL,envio_id TEXT NOT NULL,periodo TEXT NOT NULL,documento TEXT NOT NULL,
 tipo TEXT NOT NULL,importe INTEGER NOT NULL CHECK(importe>=0),estado TEXT NOT NULL,descripcion TEXT NOT NULL,
 actualizado TEXT NOT NULL,PRIMARY KEY(seller_id,id));
CREATE TABLE IF NOT EXISTS negocio_flex_control (
 seller_id TEXT NOT NULL,periodo TEXT NOT NULL,documento TEXT NOT NULL,completo INTEGER NOT NULL,
 actualizado TEXT NOT NULL,PRIMARY KEY(seller_id,periodo,documento));
'''
REGULAR={'drop_off','xd_drop_off','cross_docking','fulfillment','xd_same_day','pick_up'}


def validate(rows):
    for table,columns in TABLES.items():
        for r in rows.get(table,[]):
            if set(r)!=set(columns) or not re.fullmatch(r'[0-9]{1,25}',str(r['seller_id'])):raise ValueError('Detalle de envío no válido.')
            if any(isinstance(v,str) and len(v)>300 for v in r.values()):raise ValueError('Detalle de envío demasiado largo.')
            for key in ('cliente','bruto','bonificacion_info','repartidor','importe'):
                if key in r and r[key] is not None and (type(r[key]) is not int or not 0<=r[key]<=10**16):raise ValueError('Importe de envío inválido.')
            if table!='negocio_flex_control' and not re.fullmatch(r'[0-9]{1,25}',str(r['id'])):raise ValueError('ID de envío o cargo no válido.')
            if table=='negocio_envio_detalle' and r['tipo'] not in ('flex','mercado_envios','otro','pendiente'):raise ValueError('Tipo de envío inválido.')
            if table in ('negocio_flex_cargos','negocio_flex_control'):
                if not re.fullmatch(r'\d{4}-\d{2}-01',r['periodo']) or r['documento'] not in ('BILL','CREDIT_NOTE'):raise ValueError('Período de facturación no válido.')
            if table=='negocio_flex_cargos' and (r['tipo'] not in ('BONUS','CHARGE') or not re.fullmatch(r'[0-9]{1,25}',r['envio_id'])):raise ValueError('Movimiento Flex no compatible.')
            if table=='negocio_flex_control' and (type(r['completo']) is not int or r['completo'] not in (0,1)):raise ValueError('Estado de facturación inválido.')


def allocation(amount,linked):
    """Céntimos asignados sin duplicar un envío compartido entre órdenes o períodos."""
    if amount is None:return {r['id']:None for r in linked}
    if not linked:return {}
    sign=-1 if amount<0 else 1;amount=abs(amount)
    denominator=sum(r['total'] for r in linked)
    weights=[r['total'] for r in linked] if denominator else [1]*len(linked)
    denominator=denominator or len(linked)
    parts=[amount*w//denominator for w in weights]
    for i in sorted(range(len(linked)),key=lambda i:-(amount*weights[i]%denominator))[:amount-sum(parts)]:parts[i]+=1
    return {r['id']:sign*parts[i] for i,r in enumerate(linked)}


def economic_details(c,seller,sales,all_paid):
    ids={r['envio_id'] for r in sales if r['envio_id']}
    shipments={r['id']:dict(r) for r in c.execute('SELECT e.*,d.tipo,d.cliente,d.bruto,d.bonificacion_info,d.repartidor FROM negocio_envios e LEFT JOIN negocio_envio_detalle d ON d.seller_id=e.seller_id AND d.id=e.id WHERE e.seller_id=?',(seller,)) if r['id'] in ids}
    credits={}
    for ship in ids:
        movements=c.execute('SELECT * FROM negocio_flex_cargos WHERE seller_id=? AND envio_id=?',(seller,ship)).fetchall()
        # Facturación registra bonificaciones/cargos; no certifica saldo liberado en Mercado Pago.
        complete=bool(movements)
        for key in {r['periodo'] for r in movements}:
            documents={r['documento'] for r in c.execute('SELECT documento FROM negocio_flex_control WHERE seller_id=? AND periodo=? AND completo=1',(seller,key))}
            complete=complete and documents=={'BILL','CREDIT_NOTE'}
        if complete and all(r['estado']=='PROCESSED' for r in movements):
            credits[ship]=sum(r['importe'] if r['tipo']=='BONUS' else -r['importe'] for r in movements)
    parts={}; totals=dict(regular=0,carrier=0,credit=0,missing_flex=0,missing_ship=0,missing_mode=0)
    for ship in ids:
        sh=shipments.get(ship,{})
        linked=sorted([r for r in all_paid if r['envio_id']==ship],key=lambda r:r['id'])
        typ=sh.get('tipo') or 'pendiente'
        if typ=='flex':
            seller_cost=0;carrier=sh.get('repartidor');credit=credits.get(ship)
        elif typ=='mercado_envios':
            seller_cost=sh.get('costo');carrier=0;credit=0
        else:seller_cost=None;carrier=0;credit=0
        parts[ship]={'tipo':typ,'cliente':sh.get('cliente'),'bruto':sh.get('bruto'),'bonificacion_info':sh.get('bonificacion_info'),
          'seller':allocation(seller_cost,linked),'carrier':allocation(carrier,linked),'credit':allocation(credit,linked),
          'credit_total':credit,'carrier_total':carrier}
    rows=[]
    for order in sales:
        item=dict(order);oid=order['id'];sh=parts.get(order['envio_id'])
        lines=[dict(r) for r in c.execute('SELECT * FROM negocio_lineas WHERE venta_id=? ORDER BY numero',(oid,))]
        missing=sum(r['cantidad'] for r in lines if r['costo'] is None)
        product_cost=sum(r['cantidad']*r['costo'] for r in lines if r['costo'] is not None)
        seller_cost=sh['seller'].get(oid) if sh else (None if order['envio_id'] else 0)
        carrier=sh['carrier'].get(oid) if sh else (None if order['envio_id'] else 0)
        credit=sh['credit'].get(oid) if sh else (None if order['envio_id'] else 0)
        known=order['comision'] is not None and seller_cost is not None and credit is not None
        platform_net=order['total']-order['comision']-seller_cost+credit if known else None
        gain=platform_net-product_cost-carrier if platform_net is not None and not missing and carrier is not None else None
        item.update(tipo=sh['tipo'] if sh else ('pendiente' if order['envio_id'] else 'sin_envio'),envio_cliente=sh['cliente'] if sh else None,
          envio_bruto=sh['bruto'] if sh else None,bonificacion_info=sh['bonificacion_info'] if sh else None,
          envio_vendedor=seller_cost,repartidor=carrier,flex_credit=credit,neto_estimado=platform_net,costo_producto=product_cost,
          unidades_sin_costo=missing,ganancia=gain,lineas=lines,comision_pct=Decimal(order['comision'])*100/Decimal(order['total']) if order['comision'] is not None and order['total'] else None,
          envio_pct=Decimal(seller_cost)*100/Decimal(order['total']) if seller_cost is not None and order['total'] else None)
        rows.append(item)
        if order['estado']=='paid':
            totals['regular']+=seller_cost or 0;totals['carrier']+=carrier or 0;totals['credit']+=credit or 0
            totals['missing_mode']+=item['tipo'] in ('pendiente','otro')
    paid_ship_ids={r['envio_id'] for r in sales if r['estado']=='paid' and r['envio_id']}
    for ship in paid_ship_ids:
        info=parts[ship]
        if info['tipo']=='flex':totals['missing_flex']+=info['credit_total'] is None or info['carrier_total'] is None
        elif any(v is None for v in info['seller'].values()):totals['missing_ship']+=1
    totals['rows']=rows
    return totals


def install(app,db,ml,queue,money,bp,private,period,api):
    data=Path(app.config['DATA_DIR'])
    with db() as c:c.executescript(SCHEMA)

    def import_shipping(ship):
        seller=ml['get']('seller');token=ml['access_token']()
        shipment=api('/shipments/'+ship,token,extra_headers={'x-format-new':'true'})
        if str(shipment.get('id'))!=ship or str(shipment.get('sender_id'))!=seller:
            raise ValueError('El envío no pertenece al vendedor conectado.')
        logistic=str(shipment.get('logistic_type') or (shipment.get('logistic') or {}).get('type') or '')
        typ='flex' if logistic=='self_service' else ('mercado_envios' if logistic in REGULAR else 'otro')
        costs=api('/shipments/'+ship+'/costs',token,extra_headers={'x-format-new':'true'})
        senders=costs.get('senders');receiver=costs.get('receiver') or {}
        if not isinstance(senders,list):raise ValueError('Los costos del envío todavía no están informados.')
        matching=[r for r in senders if str(r.get('user_id'))==seller]
        cost=sum(money(r['cost']) for r in matching) if matching and all(r.get('cost') is not None for r in matching) else None
        buyer=money(receiver['cost']) if receiver.get('cost') is not None else None
        gross=money(costs['gross_amount']) if costs.get('gross_amount') is not None else None
        # Solo informativo: no atribuir al marketplace el residual ni usar rate como tarifa de venta.
        discounts=[d for r in matching+[receiver] for d in (r.get('discounts') or []) if isinstance(d,dict)]
        bonus=sum(money(d['promoted_amount']) for d in discounts) if discounts and all(d.get('promoted_amount') is not None for d in discounts) else None
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with StorageLock(data/'.catalogo.lock').acquire():
            with db() as c:
                c.execute('INSERT INTO negocio_envios VALUES (?,?,?,?) ON CONFLICT(seller_id,id) DO UPDATE SET costo=excluded.costo,actualizado=excluded.actualizado',(seller,ship,cost,now))
                c.execute('''INSERT INTO negocio_envio_detalle VALUES (?,?,?,?,?,?,NULL,?) ON CONFLICT(seller_id,id) DO UPDATE SET
                  tipo=excluded.tipo,cliente=excluded.cliente,bruto=excluded.bruto,bonificacion_info=excluded.bonificacion_info,actualizado=excluded.actualizado''',(seller,ship,typ,buyer,gross,bonus,now))
        if cost is None and typ!='flex':raise ValueError('Mercado Libre aún no informa el cargo del vendedor; no se asumirá cero.')

    def import_billing(resource):
        key,document,cursor=resource.split('|')
        if not re.fullmatch(r'\d{4}-\d{2}-01',key) or document not in ('BILL','CREDIT_NOTE') or not cursor.isdigit():raise ValueError('Recurso de facturación inválido.')
        query=urlencode({'document_type':document,'limit':150,'from_id':cursor,'sort_by':'ID','order_by':'asc'})
        result=api('/billing/integration/periods/key/'+key+'/group/ML/flex/details?'+query,ml['access_token']())
        rows=result.get('results');total=result.get('total')
        if not isinstance(rows,list) or type(total) is not int or result.get('errors'):raise ValueError('Mercado Libre no devolvió un detalle Flex completo.')
        seller=ml['get']('seller');now=datetime.now(timezone.utc).isoformat(timespec='seconds');parsed=[]
        for row in rows:
            info=row.get('charge_info') or {}; shipping=row.get('shipping_info') or {}
            did=str(info.get('detail_id',''));sid=str(shipping.get('shipping_id',''));kind=str(info.get('detail_type','')).upper()
            if not re.fullmatch(r'[0-9]{1,25}',did) or not re.fullmatch(r'[0-9]{1,25}',sid) or kind not in ('BONUS','CHARGE'):
                raise ValueError('Movimiento Flex no reconocido. No se calculará un crédito con ese dato.')
            parsed.append((seller,did,sid,key,document,kind,money(info.get('detail_amount')),str(info.get('legal_document_status',''))[:40],str(info.get('transaction_detail',''))[:250],now))
        last=str(result.get('last_id') or (parsed[-1][1] if parsed else cursor))
        more=len(rows)>=150
        if more and (not last.isdigit() or int(last)<=int(cursor)):raise ValueError('Mercado Libre no entregó el cursor para continuar la facturación Flex.')
        with StorageLock(data/'.catalogo.lock').acquire():
            with db() as c:
                c.executemany('''INSERT INTO negocio_flex_cargos VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(seller_id,id) DO UPDATE SET
                 envio_id=excluded.envio_id,periodo=excluded.periodo,documento=excluded.documento,tipo=excluded.tipo,importe=excluded.importe,
                 estado=excluded.estado,descripcion=excluded.descripcion,actualizado=excluded.actualizado''',parsed)
                c.execute('INSERT OR REPLACE INTO negocio_flex_control VALUES (?,?,?,?,?)',(seller,key,document,int(not more),now))
        if more:queue('facturacion',key+'|'+document+'|'+last)

    @bp.post('/admin/reportes/flex/facturacion')
    @private
    def billing_sync():
        month=request.form.get('mes','')
        if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month):abort(400,'Selecciona un mes válido.')
        seller=ml['get']('seller')
        if not seller:abort(400,'Conecta primero tu cuenta.')
        key=month+'-01'
        with db() as c:checks=c.execute('SELECT actualizado FROM negocio_flex_control WHERE seller_id=? AND periodo=? AND completo=1',(seller,key)).fetchall()
        if len(checks)==2 and all(r[0][:10]==datetime.now(timezone.utc).date().isoformat() for r in checks):
            flash('Ese mes ya se consultó hoy. Los datos de facturación se revisan una vez al día.','success')
        else:
            with db() as c:
                for document in ('BILL','CREDIT_NOTE'):c.execute('INSERT OR REPLACE INTO negocio_flex_control VALUES (?,?,?,0,?)',(seller,key,document,datetime.now(timezone.utc).isoformat(timespec='seconds')))
            for document in ('BILL','CREDIT_NOTE'):queue('facturacion',key+'|'+document+'|0')
            flash('Consulta de bonificaciones y anulaciones Flex solicitada. Requiere permiso de Facturación de una venta.','success')
        return redirect(url_for('reportes.dashboard'))

    @bp.get('/admin/reportes/venta/<oid>')
    @private
    def sale_detail(oid):
        if not re.fullmatch(r'[0-9]{1,25}',oid):abort(404)
        seller=ml['get']('seller')
        with db() as c:
            order=c.execute('SELECT * FROM negocio_ventas WHERE id=? AND seller_id=?',(oid,seller)).fetchone()
            if not order:abort(404)
            all_paid=[dict(r) for r in c.execute("SELECT id,total,envio_id FROM negocio_ventas WHERE seller_id=? AND estado='paid'",(seller,))]
            detail=economic_details(c,seller,[dict(order)],all_paid)['rows'][0]
            movements=c.execute('SELECT * FROM negocio_flex_cargos WHERE seller_id=? AND envio_id=? ORDER BY periodo,id',(seller,order['envio_id'])).fetchall()
            shipment=c.execute('SELECT * FROM negocio_envio_detalle WHERE seller_id=? AND id=?',(seller,order['envio_id'])).fetchone()
        return render_template('venta_detalle.html',v=detail,movements=movements,shipment=shipment)

    @bp.post('/admin/reportes/flex/<ship>/repartidor')
    @private
    def carrier_cost(ship):
        try:value=money(request.form.get('costo',''))
        except ValueError as e:abort(400,str(e))
        seller=ml['get']('seller')
        with db() as c:
            existing=c.execute("SELECT 1 FROM negocio_envio_detalle WHERE seller_id=? AND id=? AND tipo='flex'",(seller,ship)).fetchone()
            if not existing:abort(404)
            c.execute('UPDATE negocio_envio_detalle SET repartidor=? WHERE seller_id=? AND id=?',(value,seller,ship))
        flash('Costo real del reparto guardado una vez para este envío.','success')
        oid=request.form.get('venta','')
        return redirect(url_for('reportes.sale_detail',oid=oid))

    return {'import_shipping':import_shipping,'import_billing':import_billing,'economic_details':economic_details}
