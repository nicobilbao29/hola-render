import io
import sqlite3
import unittest
from datetime import datetime,timedelta,timezone
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch
import test_catalogo as original
import test_mercadolibre as meli
import test_reportes as reports
from respaldos import make_backup,review_backup,apply_review,read_catalog
from publicaciones import validate
PHOTO=meli.PHOTO


class PublicationsTest(unittest.TestCase):
    setUp=original.CatalogTest.setUp
    tearDown=original.CatalogTest.tearDown
    token=original.CatalogTest.token
    login=original.CatalogTest.login
    values=original.CatalogTest.values
    setup_meli=meli.MeliTest.setup_meli
    item=meli.MeliTest.item
    order=reports.ReportesTest.order

    def publish(self,**extra):
        self.created=datetime.now(timezone.utc)-timedelta(days=10)
        item=self.item(date_created=self.created.isoformat(),last_updated=datetime.now(timezone.utc).isoformat(),sold_quantity=0,**extra)
        with patch('mercadolibre.api_request',side_effect=[item,{'plain_text':'Detalle'}]):self.ml['import_item'](item['id'])
        return item

    def test_dates_photos_daily_average_idempotence_and_seller_scope(self):
        self.setup_meli();item=self.publish();self.publish()
        ext=self.app.extensions['publicaciones'];r=ext['records']('2020-01-01','2030-01-01')
        self.assertEqual(r['total'],1);self.assertEqual(r['rows'][0]['fotos'],[PHOTO])
        day=r['rows'][0]['fecha'];self.assertEqual(ext['records'](day,day)['average'],1)
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM negocio_publicacion_fotos').fetchone()[0],1)
        self.ml['put']('seller','999');self.assertEqual(ext['records']('2020-01-01','2030-01-01')['total'],0)

    def test_photo_change_is_observed_not_last_updated_and_backup_roundtrip(self):
        self.setup_meli();self.publish();self.publish(pictures=[{'secure_url':PHOTO.replace('123456','654321')}])
        self.login()
        html=self.client.get('/admin/publicaciones/MLC123456789/fotos').get_data(as_text=True)
        self.assertIn(PHOTO,html);self.assertIn(PHOTO.replace('123456','654321'),html)
        self.assertIn('no cuándo se subió',html)
        archive=self.data/'publicaciones.zip';make_backup(self.data/'practica.db',self.data/'uploads',archive)
        review,_=review_backup(io.BytesIO(archive.read_bytes()),self.data)
        with sqlite3.connect(self.data/'practica.db') as c:c.execute('DELETE FROM negocio_publicacion_fotos');c.execute('DELETE FROM negocio_publicaciones')
        apply_review(self.data/'practica.db',self.data,review)
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM negocio_publicacion_fotos').fetchone()[0],2)
        rows=read_catalog(self.data/'practica.db');rows['negocio_publicacion_fotos'][0]['url']='https://evil.test/image.jpg'
        with self.assertRaises(ValueError):validate(rows)

    def test_first_sale_query_validates_item_and_measures_real_elapsed_time(self):
        self.setup_meli();self.publish();ext=self.app.extensions['publicaciones']
        closed=self.created+timedelta(days=3,hours=6)
        order=self.order(date_closed=closed.isoformat())
        with patch('reportes.api_request',return_value={'results':[order],'paging':{'total':1}}) as api:
            ext['first_sale']('MLC123456789')
        q=parse_qs(urlsplit(api.call_args.args[0]).query)
        self.assertEqual(q['item'],['MLC123456789']);self.assertEqual(q['sort'],['date_asc'])
        result=ext['records']('2020-01-01','2030-01-01')
        self.assertEqual(result['rows'][0]['demora'],3.25);self.assertEqual(result['delay'],3.25)
        order['seller']['id']=999
        with patch('reportes.api_request',return_value={'results':[order],'paging':{'total':1}}):
            with self.assertRaises(ValueError):ext['first_sale']('MLC123456789')
        with self.assertRaises(ValueError):ext['first_sale']('MLC999999999')

    def test_absent_history_and_old_or_republished_are_not_used_for_mean(self):
        self.setup_meli();self.publish(parent_item_id='MLC987654321');ext=self.app.extensions['publicaciones']
        with patch('reportes.api_request',return_value={'results':[],'paging':{'total':0}}):ext['first_sale']('MLC123456789')
        r=ext['records']('2020-01-01','2030-01-01')
        self.assertIsNone(r['delay']);self.assertTrue(r['rows'][0]['historial_limitado'])
        with sqlite3.connect(self.data/'practica.db') as c:c.execute('UPDATE negocio_publicaciones SET creada=NULL')
        r=ext['records']('2020-01-01','2030-01-01');self.assertEqual(r['unknown'],1);self.assertEqual(r['total'],0)

    def test_refresh_is_private_csrf_and_background_only(self):
        self.setup_meli();self.publish();token=self.login()
        with patch('reportes.api_request') as api:
            self.assertEqual(self.client.post('/admin/publicaciones/actualizar').status_code,400)
            response=self.client.post('/admin/publicaciones/actualizar?desde=2020-01-01&hasta=2030-01-01',data={'csrf_token':token,'modo':'ventas'})
            self.assertEqual(response.status_code,302);api.assert_not_called()
        with self.ml['state_db']() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM reportes_pendientes WHERE tipo='primera_venta'").fetchone()[0],1)
        self.assertEqual(self.app.test_client().get('/admin/publicaciones').status_code,302)
        self.ml['put']('seller','999');self.assertEqual(self.client.get('/admin/publicaciones/MLC123456789/fotos').status_code,404)

    def test_paid_orders_update_first_found_and_cancellation_invalidates(self):
        self.setup_meli();self.publish();reports=self.app.extensions['reportes'];ext=self.app.extensions['publicaciones']
        stamp=(self.created+timedelta(days=4)).isoformat()
        with patch('reportes.api_request',return_value=self.order(date_closed=stamp)):reports['import_order']('1001')
        r=ext['records']('2020-01-01','2030-01-01');self.assertEqual(r['rows'][0]['demora'],4);self.assertIsNone(r['delay'])
        with patch('reportes.api_request',return_value=self.order(date_closed=stamp,status='cancelled')):reports['import_order']('1001')
        self.assertIsNone(ext['records']('2020-01-01','2030-01-01')['rows'][0]['primera_fecha'])
