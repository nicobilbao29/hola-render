import base64
import hashlib
import io
import sqlite3
import time
import unittest
import zipfile
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import test_catalogo as original
from app import create_app
from mercadolibre import MeliError, api_request, photo_url
from respaldos import apply_review, review_backup

PHOTO='https://http2.mlstatic.com/D_NQ_NP_123456-MLC999999_112026-F.webp'

class MeliTest(unittest.TestCase):
    setUp = original.CatalogTest.setUp
    tearDown = original.CatalogTest.tearDown
    token = original.CatalogTest.token
    login = original.CatalogTest.login
    values = original.CatalogTest.values

    def setup_meli(self):
        self.app.config.update(ML_CLIENT_ID='12345',ML_CLIENT_SECRET='secret-test',ML_REDIRECT_URI='https://example.com/mercadolibre/callback')
        self.ml=self.app.extensions['mercadolibre']
        for key,value in {'seller':'123','access':'access-test','refresh':'refresh-test','expires':str(time.time()+3600)}.items():
            self.ml['put'](key,value)

    def item(self, **extra):
        return {'id':'MLC123456789','seller_id':123,'site_id':'MLC','title':'Chal de Mercado Libre','price':9990,'available_quantity':4,'currency_id':'CLP','permalink':'http://articulo.mercadolibre.cl/MLC-123456789-chal','pictures':[{'secure_url':PHOTO}],'status':'active',**extra}

    def fake_api(self,path,token=None,form=None):
        if path=='/items/MLC123456789':
            return self.item()
        if path.endswith('/description'):
            return {'plain_text':'Descripción importada'}
        raise AssertionError('Unexpected API path: '+path)

    def notify(self, **extra):
        return self.client.post('/mercadolibre/notificaciones',json={'application_id':12345,'user_id':123,'topic':'items','resource':'/items/MLC123456789',**extra})

    def test_pkce_state_and_token_callback(self):
        self.setup_meli()
        token=self.login()
        response=self.client.post('/admin/mercadolibre/conectar',data={'csrf_token':token})
        query=parse_qs(urlsplit(response.location).query)
        self.assertEqual(urlsplit(response.location).hostname,'auth.mercadolibre.cl')
        with self.client.session_transaction() as session:
            verifier=session['ml_oauth']['verifier']
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(query['code_challenge'],[challenge])
        with patch('mercadolibre.api_request') as api:
            self.assertEqual(self.client.get('/mercadolibre/callback?state=wrong&code=secret').status_code,400)
            api.assert_not_called()
        self.client.post('/admin/mercadolibre/conectar',data={'csrf_token':token})
        with self.client.session_transaction() as session:
            state=session['ml_oauth']['state']
        def authorize(path,token=None,form=None):
            if path=='/oauth/token':
                self.assertIn('code_verifier',form)
                return {'access_token':'new-access','refresh_token':'new-refresh','user_id':123,'expires_in':21600}
            return {'id':123,'site_id':'MLC'}
        with patch('mercadolibre.api_request',side_effect=authorize):
            response=self.client.get('/mercadolibre/callback?state='+state+'&code=test')
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.ml['get']('refresh'),'new-refresh')
        self.assertEqual(self.client.get('/mercadolibre/callback?state='+state+'&code=test').status_code,400)

    def test_notifications_queue_idempotence_and_validation(self):
        self.setup_meli()
        with patch('mercadolibre.api_request') as api:
            self.assertEqual(self.notify().status_code,200)
            self.assertEqual(self.notify().status_code,200)
            self.assertEqual(self.notify(application_id=999).status_code,403)
            self.assertEqual(self.notify(user_id=999).status_code,403)
            self.assertEqual(self.notify(resource='https://evil.test/items/MLC123456789').status_code,400)
            self.assertEqual(self.client.post('/mercadolibre/notificaciones',data='bad').status_code,400)
            api.assert_not_called()
        with self.ml['state_db']() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0],1)
        self.assertEqual(self.client.get('/mercadolibre/notificaciones').status_code,200)
        self.assertEqual(self.client.post('/mercadolibre/notificaciones',data='x'*17000,content_type='application/json').status_code,413)
        self.assertEqual(self.client.post('/admin/mercadolibre/sincronizar').status_code,400)
        self.assertEqual(self.client.get('/admin/mercadolibre').status_code,302)

    def test_import_upsert_remote_photos_pause_and_manual_preservation(self):
        self.setup_meli()
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,nombre='Manual'))
        with patch('mercadolibre.api_request',side_effect=self.fake_api):
            self.ml['import_item']('MLC123456789')
            self.ml['import_item']('MLC123456789')
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM catalogo_productos').fetchone()[0],2)
            product_id=c.execute('SELECT producto_id FROM catalogo_ml_items').fetchone()[0]
            c.execute('UPDATE catalogo_productos SET categoria_id=1,destacado=1 WHERE id=?',(product_id,))
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn(PHOTO,html)
        self.assertIn('Manual',html)
        self.assertIn(PHOTO,self.client.get('/catalogo/producto/'+str(product_id)).get_data(as_text=True))
        self.assertEqual(list((self.data/'uploads').iterdir()),[])
        def update(path,token=None,form=None):
            return {'plain_text':'Nueva descripción'} if path.endswith('/description') else self.item(title='Nuevo nombre',price=10990,status='paused')
        with patch('mercadolibre.api_request',side_effect=update):
            self.ml['import_item']('MLC123456789')
        with sqlite3.connect(self.data/'practica.db') as c:
            row=c.execute('SELECT nombre,precio,activo,categoria_id,destacado FROM catalogo_productos WHERE id=?',(product_id,)).fetchone()
            self.assertEqual(row,('Nuevo nombre',10990,0,1,1))
        public=create_app(self.config).test_client()
        self.assertEqual(public.get('/catalogo/producto/'+str(product_id)).status_code,404)
        self.assertIn('Manual',public.get('/catalogo').get_data(as_text=True))

    def test_api_failure_keeps_product_and_retries(self):
        self.setup_meli()
        with patch('mercadolibre.api_request',side_effect=self.fake_api):
            self.ml['import_item']('MLC123456789')
        self.ml['enqueue'](['MLC123456789'])
        with patch('mercadolibre.api_request',side_effect=MeliError('Sin acceso',403)):
            self.ml['tick']()
        with self.ml['state_db']() as c:
            job=c.execute('SELECT * FROM pendientes').fetchone()
            self.assertEqual(job['intentos'],1)
            self.assertGreater(job['proximo'],time.time())
        self.assertIn('Chal de Mercado Libre',self.client.get('/catalogo').get_data(as_text=True))
        self.ml['enqueue'](['MLC123456789'])
        with patch('mercadolibre.api_request',side_effect=MeliError('Eliminada',404)):
            self.ml['tick']()
        self.assertNotIn('Chal de Mercado Libre',self.client.get('/catalogo').get_data(as_text=True))

    def test_external_seller_not_imported(self):
        self.setup_meli()
        with patch('mercadolibre.api_request',return_value=self.item(seller_id=999)):
            with self.assertRaises(MeliError):
                self.ml['import_item']('MLC123456789')
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM catalogo_productos').fetchone()[0],0)
        self.assertIsNone(photo_url('https://http2.mlstatic.com.evil.com/D_bad.webp'))
        self.assertIsNone(photo_url('https://user@http2.mlstatic.com/D_bad.webp'))
        self.assertEqual(photo_url(PHOTO.replace('https:','http:')),PHOTO)

    def test_refresh_rotation_and_scan_pagination(self):
        self.setup_meli()
        self.ml['put']('expires','0')
        def refresh(path,token=None,form=None):
            self.assertEqual(form['refresh_token'],'refresh-test')
            return {'access_token':'renewed','refresh_token':'rotated','user_id':123,'expires_in':21600}
        with patch('mercadolibre.api_request',side_effect=refresh) as api:
            self.assertEqual(self.ml['access_token'](),'renewed')
            self.assertEqual(self.ml['access_token'](),'renewed')
            self.assertEqual(api.call_count,1)
        self.assertEqual(self.ml['get']('refresh'),'rotated')
        with patch('mercadolibre.api_request',side_effect=[{'results':['MLC123456789'],'scroll_id':'cursor'},{'results':[]}]) as api:
            self.ml['scan']()
            self.assertIn('scroll_id=cursor',api.call_args_list[1].args[0])
        with patch('mercadolibre.api_request',side_effect=self.fake_api):
            self.ml['tick']()
        with self.ml['state_db']() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0],0)

    def test_backups_links_and_no_tokens_plus_cover_copy(self):
        self.setup_meli()
        token=self.login()
        with patch('mercadolibre.api_request',side_effect=self.fake_api):
            self.ml['import_item']('MLC123456789')
        html=self.client.get('/admin/portadas').get_data(as_text=True)
        self.assertIn('value="ml:1"',html)
        self.assertIn(PHOTO,html)
        with patch('app.descargar_foto',return_value=original.image()):
            self.client.post('/admin/portadas',data={'csrf_token':token,'foto_producto':'ml:1','titulo':'Portada importada'})
        response=self.client.get('/admin/respaldo');content=response.get_data();response.close()
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            self.assertNotIn('data/mercadolibre_privado.sqlite',z.namelist())
            raw=z.read('data/practica.db')
            self.assertNotIn(b'access-test',raw)
            self.assertNotIn(b'refresh-test',raw)
        review,summary=review_backup(io.BytesIO(content),self.data)
        self.assertEqual(summary['fotos_ml'],1)
        apply_review(self.data/'practica.db',self.data,review)
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn(PHOTO,html)
        self.assertIn('Portada importada',html)
        self.assertEqual(self.ml['get']('refresh'),'refresh-test')

    def test_change_account_requires_confirmation_and_preserves_manual_products(self):
        self.setup_meli()
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,nombre='Manual conservado'))
        with patch('mercadolibre.api_request',side_effect=self.fake_api):
            self.ml['import_item']('MLC123456789')
        self.ml['enqueue'](['MLC123456789'])
        response=self.client.post('/admin/mercadolibre/desconectar',data={'csrf_token':token})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.ml['get']('seller'),'123')
        self.assertEqual(self.client.post('/admin/mercadolibre/desconectar',data={'confirmar':'si'}).status_code,400)
        self.client.post('/admin/mercadolibre/desconectar',data={'csrf_token':token,'confirmar':'si'})
        self.assertEqual(self.ml['get']('seller'),'')
        self.assertEqual(self.ml['get']('access'),'')
        self.assertEqual(self.ml['get']('refresh'),'')
        with self.ml['state_db']() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0],0)
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn('Manual conservado',html)
        self.assertNotIn('Chal de Mercado Libre',html)
        self.assertIn('Desconectar para cambiar cuenta',self.client.get('/admin/mercadolibre').get_data(as_text=True))
        # Una cuenta nueva se puede autorizar después de la desconexión explícita.
        self.client.post('/admin/mercadolibre/conectar',data={'csrf_token':token})
        with self.client.session_transaction() as session:
            state=session['ml_oauth']['state']
        with patch('mercadolibre.api_request',side_effect=[{'access_token':'new','refresh_token':'new-r','user_id':456,'expires_in':21600},{'id':456,'site_id':'MLC','nickname':'VENDEDOR_CORRECTO'}]):
            self.client.get('/mercadolibre/callback?state='+state+'&code=test')
        self.assertEqual(self.ml['get']('seller'),'456')
        self.assertIn('VENDEDOR_CORRECTO',self.client.get('/admin/mercadolibre').get_data(as_text=True))
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM catalogo_ml_items').fetchone()[0],1)
        self.assertEqual(self.notify().status_code,403)

    def test_expected_seller_and_old_authorization_are_rejected(self):
        self.setup_meli()
        token=self.login()
        self.app.config['ML_EXPECTED_SELLER_ID']='999'
        self.client.post('/admin/mercadolibre/conectar',data={'csrf_token':token})
        with self.client.session_transaction() as session:
            old_oauth=dict(session['ml_oauth'])
            state=old_oauth['state']
        with patch('mercadolibre.api_request',side_effect=[{'access_token':'wrong','refresh_token':'wrong-r','user_id':123,'expires_in':21600},{'id':123,'site_id':'MLC'}]):
            self.client.get('/mercadolibre/callback?state='+state+'&code=test')
        self.assertEqual(self.ml['get']('access'),'access-test')
        self.assertIn('ML_EXPECTED_SELLER_ID',self.client.get('/admin/mercadolibre').get_data(as_text=True))
        self.client.post('/admin/mercadolibre/desconectar',data={'csrf_token':token,'confirmar':'si'})
        with self.client.session_transaction() as session:
            session['ml_oauth']=old_oauth
        with patch('mercadolibre.api_request') as api:
            self.client.get('/mercadolibre/callback?state='+state+'&code=test')
            api.assert_not_called()
        self.assertEqual(self.ml['get']('refresh'),'')

    def test_change_account_waits_for_worker_instead_of_racing(self):
        from respaldos import StorageLock
        self.setup_meli()
        token=self.login()
        with StorageLock(self.data/'.mercadolibre_operation.lock').acquire():
            response=self.client.post('/admin/mercadolibre/desconectar',data={'csrf_token':token,'confirmar':'si'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(self.ml['get']('seller'),'123')
        self.assertIn('sincronización en curso',self.client.get('/admin/mercadolibre').get_data(as_text=True))
