import io
import sqlite3
import time
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from app import create_app
from reportes import money,local_day
from respaldos import make_backup,review_backup,apply_review,read_catalog
import test_catalogo as original

class ReportesTest(unittest.TestCase):
    setUp=original.CatalogTest.setUp
    tearDown=original.CatalogTest.tearDown
    token=original.CatalogTest.token
    login=original.CatalogTest.login
    values=original.CatalogTest.values

    def setup_sales(self):
        self.ml=self.app.extensions['mercadolibre']; self.r=self.app.extensions['reportes']
        self.app.config.update(ML_CLIENT_ID='12345',ML_CLIENT_SECRET='test',ML_REDIRECT_URI='https://example.com/mercadolibre/callback')
        for k,v in {'seller':'123','access':'test','refresh':'test-r','expires':time.time()+3600}.items(): self.ml['put'](k,v)

    def order(self,**changes):
        o={'id':1001,'seller':{'id':123},'date_created':'2026-11-01T15:00:00Z','date_closed':'2026-11-01T15:01:00Z','status':'paid','currency_id':'CLP','total_amount':19980,'shipping':{'id':700},'order_items':[{'item':{'id':'MLC123456789','title':'Chal','variation_id':99},'quantity':2,'unit_price':9990,'sale_fee':1000.75}]}
        o.update(changes);return o

    def import_one(self,order=None):
        with patch('reportes.api_request',return_value=order or self.order()): self.r['import_order'](str((order or self.order())['id']))

    def result(self): return self.r['report']('2026-11-01','2026-11-30')

    def shipping(self):
        with patch('reportes.api_request',side_effect=[{'id':700,'sender_id':123,'logistic_type':'drop_off'},{'gross_amount':7000,'senders':[{'user_id':123,'cost':2500}],'receiver':{'cost':4500}}]): self.r['import_shipping']('700')

    def test_money_and_chilean_date(self):
        self.assertEqual(money('1000.755'),100076)
        self.assertEqual(local_day('2026-11-01T01:00:00Z'),'2026-10-31')
        for bad in ('NaN','Infinity','-1','1.000.000'):
            with self.assertRaises(ValueError):money(bad)

    def test_unknown_cost_not_zero_idempotence_and_historic_cost(self):
        self.setup_sales();self.import_one();self.import_one()
        r=self.result();self.assertEqual(len(r['sales']),1);self.assertEqual(r['units'],2);self.assertEqual(r['fees'],200150);self.assertIsNone(r['result']);self.assertEqual(r['missing_cost'],2)
        token=self.login()
        self.client.post('/admin/costos',data={'csrf_token':token,'producto':'MLC123456789|99','costo':'1000','aplicar':'si'})
        self.shipping()
        self.client.post('/admin/gastos',data={'csrf_token':token,'fecha':'2026-11-01','categoria':'Embalaje','descripcion':'Bolsa','monto':'7.50'})
        r=self.result();self.assertEqual(r['result'],1347100)
        self.client.post('/admin/costos',data={'csrf_token':token,'producto':'MLC123456789|99','costo':'2000','aplicar':'si'})
        self.import_one();self.assertEqual(self.result()['costs'],200000)
        self.assertIsNotNone(create_app(self.config).extensions['reportes']['report']('2026-11-01','2026-11-30')['result'])

    def test_cancelled_order_and_shared_shipping_once(self):
        self.setup_sales();self.import_one();self.import_one(self.order(id=1002));self.shipping()
        self.assertEqual(self.result()['shipping'],250000)
        self.assertEqual(self.result()['gross'],3996000)
        self.import_one(self.order(status='cancelled'))
        self.assertEqual(self.result()['gross'],1998000)
        self.assertEqual(self.result()['units'],2)
        self.assertEqual(len(self.result()['sales']),2)

    def test_shared_shipping_across_periods_is_not_duplicated(self):
        self.setup_sales();self.import_one();self.import_one(self.order(id=1002,date_created='2026-12-01T15:00:00Z',date_closed='2026-12-01T15:01:00Z'));self.shipping()
        november=self.r['report']('2026-11-01','2026-11-30')['shipping']
        december=self.r['report']('2026-12-01','2026-12-31')['shipping']
        self.assertEqual(november,125000);self.assertEqual(december,125000)
        self.assertEqual(november+december,self.r['report']('2026-11-01','2026-12-31')['shipping'])

    def test_wrong_seller_currency_and_missing_shipping(self):
        self.setup_sales()
        with self.assertRaises(ValueError):self.import_one(self.order(seller={'id':999}))
        with self.assertRaises(ValueError):self.import_one(self.order(currency_id='USD'))
        self.import_one()
        with patch('reportes.api_request',return_value={'senders':[{'user_id':999,'cost':0}]}):
            with self.assertRaises(ValueError):self.r['import_shipping']('700')
        self.assertEqual(self.result()['missing_ship'],1)
        self.ml['put']('seller','999');self.assertEqual(self.result()['gross'],0)

    def test_paging_date_windows_and_retry(self):
        self.setup_sales();self.r['start_scan']('2026-11-01','2026-11-02')
        calls=[]
        def search(path,*args,**kwargs):
            calls.append(parse_qs(urlsplit(path).query))
            return {'results':[{'id':1001}],'paging':{'total':1}}
        with patch('reportes.api_request',side_effect=search):self.r['tick']()
        self.assertEqual(self.ml['get']('ventas_dia'),'2026-11-02')
        self.assertIn('order.date_created.from',calls[0])
        with patch('reportes.api_request',side_effect=ValueError('Falta permiso')):self.r['tick']()
        with self.ml['state_db']() as c:
            r=c.execute("SELECT * FROM reportes_pendientes WHERE clave='venta:1001'").fetchone()
            self.assertEqual(r['intentos'],1);self.assertEqual(r['error'],'Falta permiso')
        with patch('reportes.api_request',return_value={'results':[],'paging':{'total':0}}):self.r['tick']()
        self.assertEqual(self.ml['get']('ventas_scan'),'0')

    def test_sales_notifications_validation_and_disconnect(self):
        self.setup_sales(); token=self.login()
        base={'application_id':12345,'user_id':123,'topic':'orders_v2','resource':'/orders/1001'}
        with patch('reportes.api_request') as api:
            self.assertEqual(self.client.post('/mercadolibre/notificaciones',json=base).status_code,200);api.assert_not_called()
        for key,value,code in [('user_id',999,403),('application_id',99,403),('resource','https://evil.com/orders/1',400)]:
            self.assertEqual(self.client.post('/mercadolibre/notificaciones',json={**base,key:value}).status_code,code)
        self.client.post('/admin/mercadolibre/desconectar',data={'csrf_token':token,'confirmar':'si'})
        with self.ml['state_db']() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM reportes_pendientes').fetchone()[0],0)

    def test_private_pages_csrf_csv_and_expense_validation(self):
        self.setup_sales()
        for path in ('/admin/reportes','/admin/costos','/admin/gastos','/admin/reportes/descargar/ventas'):
            self.assertEqual(self.client.get(path).status_code,302)
        self.assertEqual(self.client.post('/admin/gastos',data={}).status_code,400)
        token=self.login();self.import_one()
        for path in ('/admin/reportes?desde=2026-11-01&hasta=2026-11-30','/admin/costos','/admin/gastos'):
            response=self.client.get(path);self.assertEqual(response.status_code,200);self.assertIn('no-store',response.headers['Cache-Control'])
        self.client.post('/admin/gastos',data={'csrf_token':token,'fecha':'2026-11-02','categoria':'Otros','descripcion':'=HYPERLINK("evil")','monto':'15.25'})
        response=self.client.get('/admin/reportes/descargar/gastos?desde=2026-11-01&hasta=2026-11-30')
        self.assertIn("'=HYPERLINK",response.get_data(as_text=True));self.assertIn('attachment',response.headers['Content-Disposition'])
        for kind in ('ventas','productos','resumen'):
            self.assertEqual(self.client.get('/admin/reportes/descargar/'+kind+'?desde=2026-11-01&hasta=2026-11-30').status_code,200)
        self.assertEqual(self.client.get('/admin/reportes?desde=bad').status_code,400)
        self.client.post('/admin/gastos',data={'csrf_token':token,'fecha':'2026-11-02','categoria':'Otros','descripcion':'No guardar','monto':'-1'})
        self.assertEqual(self.result()['expense'],1525)

    def test_catalog_metrics_outbound_and_admin_exclusion(self):
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,enlace_ml='https://www.mercadolibre.cl/test'))
        self.client.get('/catalogo/producto/1');self.client.get('/ir/1/ml')
        self.client.post('/admin/salir',data={'csrf_token':token})
        self.client.get('/catalogo/producto/1',headers={'User-Agent':'Cliente real'})
        self.client.get('/catalogo/producto/1',headers={'User-Agent':'Cliente real'})
        response=self.client.get('/ir/1/ml',headers={'User-Agent':'Cliente real'})
        self.assertEqual(response.location,'https://www.mercadolibre.cl/test')
        self.client.get('/catalogo?q=Chal',headers={'User-Agent':'Cliente real'})
        self.client.get('/catalogo?q=NoBot',headers={'User-Agent':'Googlebot'})
        with sqlite3.connect(self.data/'practica.db') as c:
            metrics=dict(c.execute('SELECT tipo,SUM(cantidad) FROM catalogo_metricas GROUP BY tipo'))
        self.assertEqual(metrics,{'busqueda':1,'ml':1,'visita':1})
        self.assertEqual(self.client.get('/ir/999/ml').status_code,404)

    def prepare_flex(self):
        self.setup_sales();self.import_one()
        with patch('reportes.api_request',side_effect=[{'id':700,'sender_id':123,'logistic_type':'self_service'}, {'gross_amount':5000,'receiver':{'cost':3000,'discounts':[]},'senders':[{'user_id':123,'cost':0}]}]):
            self.r['import_shipping']('700')
        with sqlite3.connect(self.data/'practica.db') as c:
            c.execute('UPDATE negocio_lineas SET costo=100000')
            c.execute("UPDATE negocio_envio_detalle SET repartidor=350000 WHERE id='700'")

    def flex_movement(self,typ='BONUS',amount=3000,did=1):
        return {'charge_info':{'detail_id':did,'detail_type':typ,'detail_amount':amount,'legal_document_status':'PROCESSED','transaction_detail':'Movimiento Flex'},'shipping_info':{'shipping_id':700}}

    def billing_page(self,rows,doc='BILL'):
        with patch('reportes.api_request',return_value={'results':rows,'total':len(rows),'errors':[]}):
            self.r['import_billing']('2026-11-01|'+doc+'|0')

    def test_flex_separates_buyer_bonus_and_real_carrier(self):
        self.prepare_flex()
        self.assertIsNone(self.result()['result']);self.assertEqual(self.result()['missing_flex'],1)
        # El dinero del comprador no se transforma por inferencia en un abono Flex.
        self.billing_page([self.flex_movement()])
        self.assertIsNone(self.result()['result']) # falta revisar notas de crédito
        self.billing_page([],doc='CREDIT_NOTE')
        r=self.result();self.assertEqual(r['flex_credit'],300000);self.assertEqual(r['carrier'],350000)
        self.assertEqual(r['regular_shipping'],0)
        self.assertEqual(r['result'],1998000-200150-200000+300000-350000)
        self.billing_page([self.flex_movement()]) # idempotente
        self.assertEqual(self.result()['flex_credit'],300000)
        self.billing_page([self.flex_movement('CHARGE',500,2)],doc='CREDIT_NOTE')
        self.assertEqual(self.result()['flex_credit'],250000)
        token=self.login()
        html=self.client.get('/admin/reportes/venta/1001').get_data(as_text=True)
        self.assertIn('Tu venta, explicada',html);self.assertIn('Flex',html);self.assertIn('$3.000,00',html)
        self.client.post('/admin/reportes/flex/700/repartidor',data={'csrf_token':token,'venta':'1001','costo':'4000'})
        self.assertEqual(self.result()['carrier'],400000)
        self.assertEqual(self.client.post('/admin/reportes/flex/700/repartidor',data={'csrf_token':token,'venta':'1001','costo':'-1'}).status_code,400)
        self.ml['put']('seller','999');self.assertEqual(self.client.get('/admin/reportes/venta/1001').status_code,404)

    def test_regular_shipping_receipt_and_percent(self):
        self.setup_sales();self.import_one();self.shipping();self.login()
        r=self.result();v=r['sale_details'][0]
        self.assertEqual(v['envio_cliente'],450000);self.assertEqual(v['envio_vendedor'],250000)
        self.assertEqual(v['neto_estimado'],1998000-200150-250000)
        self.assertAlmostEqual(float(v['comision_pct']),10.0175175,places=5)
        html=self.client.get('/admin/reportes/venta/1001').get_data(as_text=True)
        self.assertIn('Red de Mercado Envíos',html);self.assertIn('10.02%',html)
        self.assertEqual(self.client.get('/admin/reportes/venta/xyz').status_code,404)

    def test_flex_billing_permission_retry_and_pagination(self):
        self.prepare_flex();token=self.login()
        response=self.client.post('/admin/reportes/flex/facturacion',data={'csrf_token':token,'mes':'2026-11'})
        self.assertEqual(response.status_code,302)
        with self.ml['state_db']() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM reportes_pendientes WHERE tipo='facturacion'").fetchone()[0],2)
        from mercadolibre import MeliError
        with patch('reportes.api_request',side_effect=MeliError('Sin permiso de facturación',403)):self.r['tick']()
        with self.ml['state_db']() as c:self.assertIn('Sin permiso',c.execute("SELECT error FROM reportes_pendientes WHERE error!='' LIMIT 1").fetchone()[0])
        rows=[self.flex_movement(did=n) for n in range(1,151)]
        with patch('reportes.api_request',return_value={'results':rows,'total':200,'last_id':150,'errors':[]}):self.r['import_billing']('2026-11-01|BILL|0')
        with self.ml['state_db']() as c:self.assertIsNotNone(c.execute("SELECT 1 FROM reportes_pendientes WHERE recurso='2026-11-01|BILL|150'").fetchone())
        self.assertIsNone(self.result()['result'])

    def test_flex_backup_roundtrip_includes_detail(self):
        self.prepare_flex();self.billing_page([self.flex_movement()]);self.billing_page([],doc='CREDIT_NOTE')
        backup=self.data/'flex.zip';make_backup(self.data/'practica.db',self.data/'uploads',backup)
        with backup.open('rb') as stream:token,summary=review_backup(stream,self.data)
        with sqlite3.connect(self.data/'practica.db') as c:
            c.execute('DELETE FROM negocio_flex_cargos');c.execute('UPDATE negocio_envio_detalle SET repartidor=NULL')
        apply_review(self.data/'practica.db',self.data,token)
        self.assertEqual(self.result()['flex_credit'],300000);self.assertEqual(self.result()['carrier'],350000)

    def test_backup_restore_finances_without_tokens_and_legacy_preserves(self):
        self.setup_sales();self.import_one();self.shipping()
        token=self.login()
        self.client.post('/admin/gastos',data={'csrf_token':token,'fecha':'2026-11-01','categoria':'Otros','descripcion':'Gasto original','monto':'10'})
        backup=self.data/'copy.zip';make_backup(self.data/'practica.db',self.data/'uploads',backup)
        with backup.open('rb') as stream:t,summary=review_backup(stream,self.data)
        self.assertTrue(summary['incluye_reportes']);self.assertEqual(summary['ventas'],1)
        with sqlite3.connect(self.data/'practica.db') as c:c.execute('DELETE FROM negocio_gastos')
        apply_review(self.data/'practica.db',self.data,t)
        self.assertEqual(self.result()['expense'],1000);self.assertEqual(self.ml['get']('refresh'),'test-r')
        with sqlite3.connect(self.data/'practica.db') as c:
            before=c.execute('SELECT COUNT(*) FROM negocio_lineas').fetchone()[0]
            c.execute('PRAGMA foreign_keys=OFF')
            for table in ('negocio_lineas','negocio_ventas','negocio_envios','negocio_costos','negocio_gastos','catalogo_metricas'):
                c.execute('DROP TABLE '+table)
        legacy=self.data/'legacy.zip';make_backup(self.data/'practica.db',self.data/'uploads',legacy)
        self.app=create_app(self.config);self.r=self.app.extensions['reportes'];self.ml=self.app.extensions['mercadolibre'];self.import_one()
        with legacy.open('rb') as stream:t,summary=review_backup(stream,self.data)
        self.assertFalse(summary['incluye_reportes']);apply_review(self.data/'practica.db',self.data,t)
        self.assertEqual(len(self.result()['sales']),1)
