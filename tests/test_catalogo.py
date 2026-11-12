import io
import re
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from werkzeug.security import generate_password_hash
from app import create_app


def image(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (200, 250), color).save(stream, 'PNG')
    stream.seek(0)
    return stream


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        with sqlite3.connect(self.data / 'practica.db') as conn:
            conn.execute('CREATE TABLE personas(id INTEGER PRIMARY KEY,nombre TEXT)')
            conn.execute("INSERT INTO personas VALUES (1,'Dato anterior')")
        self.config = dict(TESTING=True, DATA_DIR=self.temp.name, SECRET_KEY='tests', ADMIN_PASSWORD_HASH=generate_password_hash('test-password'))
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def token(self, path='/admin/login'):
        html = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self):
        token = self.token()
        response = self.client.post('/admin/login', data={'csrf_token': token, 'password': 'test-password'})
        self.assertEqual(response.status_code, 302)
        return self.token('/admin')

    def values(self, token, **extra):
        return {'csrf_token': token, 'nombre': 'Chal plateado', 'precio': '9990', 'stock': '3', 'categoria_id': '1', 'activo': 'on', **extra}

    def test_preserve_old_data_and_categories(self):
        with sqlite3.connect(self.data / 'practica.db') as conn:
            self.assertEqual(conn.execute('SELECT nombre FROM personas').fetchone()[0], 'Dato anterior')
        token = self.login()
        self.client.post('/admin/categorias', data={'csrf_token':token,'id':'1','nombre':'Prendas'})
        create_app(self.config)
        with sqlite3.connect(self.data/'practica.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM catalogo_categorias').fetchone()[0],3)

    def test_multiphoto_gallery_settings_and_links(self):
        token = self.login()
        self.client.post('/admin/configuracion', data={'csrf_token':token, 'nombre':'Mi tienda', 'descripcion':'Mi selección', 'whatsapp':'+56 9 1234 5678'})
        response = self.client.post('/admin/producto/nuevo', data=self.values(token, enlace_ml='https://www.mercadolibre.cl/test', fotos=[(image(),'1.png'),(image('blue'),'2.png')]))
        self.assertEqual(response.status_code,302)
        html = self.client.get('/catalogo/producto/1').get_data(as_text=True)
        self.assertIn('/ir/1/wa',html)
        self.assertTrue(self.client.get('/ir/1/wa').location.startswith('https://wa.me/56912345678?'))
        self.assertIn('/ir/1/ml',html)
        self.assertEqual(self.client.get('/ir/1/ml').location,'https://www.mercadolibre.cl/test')
        self.assertEqual(html.count('data-gallery-src='),2)
        with sqlite3.connect(self.data/'practica.db') as conn:
            photos=conn.execute('SELECT id,archivo FROM catalogo_fotos ORDER BY id').fetchall()
        for _,filename in photos:
            response=self.client.get('/fotos/'+filename)
            self.assertEqual(response.status_code,200)
            response.close()
        self.client.post('/admin/foto/2/portada',data={'csrf_token':token})
        html=self.client.get('/catalogo/producto/1').get_data(as_text=True)
        self.assertLess(html.index(photos[1][1]),html.index(photos[0][1]))
        self.client.post('/admin/foto/1/quitar',data={'csrf_token':token})
        self.assertFalse((self.data/'uploads'/photos[0][1]).exists())
        self.assertEqual(self.client.get('/catalogo/producto/1').get_data(as_text=True).count('data-gallery-src='),0)

    def test_private_csrf_and_xss(self):
        self.assertEqual(self.client.get('/admin').status_code,302)
        self.assertEqual(self.client.get('/admin/respaldo').status_code,302)
        self.assertEqual(self.client.post('/admin/categorias',data={'nombre':'hack'}).status_code,400)
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,nombre='<script>alert(1)</script>'))
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html)

    def test_hidden_filter_and_stock(self):
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,fotos=[(image(),'a.png')]))
        self.assertIn('Chal plateado',self.client.get('/catalogo?categoria=1').get_data(as_text=True))
        self.assertNotIn('<h3><a',self.client.get('/catalogo?categoria=2').get_data(as_text=True))
        self.assertIn('No encontramos',self.client.get('/catalogo?q=noexiste').get_data(as_text=True))
        values=self.values(token,stock='0')
        values.pop('activo')
        self.client.post('/admin/producto/1',data=values)
        self.assertEqual(self.client.get('/catalogo/producto/1').status_code,404)
        with sqlite3.connect(self.data/'practica.db') as conn:
            filename=conn.execute('SELECT archivo FROM catalogo_fotos').fetchone()[0]
        self.client.post('/admin/salir',data={'csrf_token':token})
        self.assertEqual(self.client.get('/fotos/'+filename).status_code,404)

    def test_validation_and_no_orphan_files(self):
        token=self.login()
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,enlace_ml='https://evil.com')).status_code,400)
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,precio='-1')).status_code,400)
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,categoria_id='99')).status_code,400)
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,fotos=[(image(),'ok.png'),(io.BytesIO(b'bad'),'bad.png')])).status_code,400)
        self.assertEqual(list((self.data/'uploads').iterdir()),[])
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,fotos=[(image(),str(i)+'.png') for i in range(9)])).status_code,400)

    def test_create_category_while_saving_product(self):
        token=self.login()
        response=self.client.post('/admin/producto/nuevo',data=self.values(token,categoria_id='',nueva_categoria='Chales de fiesta'))
        self.assertEqual(response.status_code,302)
        with sqlite3.connect(self.data/'practica.db') as conn:
            category_id=conn.execute("SELECT id FROM catalogo_categorias WHERE nombre='Chales de fiesta'").fetchone()[0]
            self.assertEqual(conn.execute('SELECT categoria_id FROM catalogo_productos').fetchone()[0],category_id)
        self.assertIn('Chal plateado',self.client.get(f'/catalogo?categoria={category_id}').get_data(as_text=True))
        self.client.post('/admin/producto/nuevo',data=self.values(token,categoria_id='',nueva_categoria='chales de fiesta'))
        with sqlite3.connect(self.data/'practica.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM catalogo_categorias').fetchone()[0],4)
        response=self.client.post('/admin/producto/nuevo',data=self.values(token,categoria_id='',nueva_categoria='No crear',fotos=[(io.BytesIO(b'bad'),'bad.png')]))
        self.assertEqual(response.status_code,400)
        with sqlite3.connect(self.data/'practica.db') as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalogo_categorias WHERE nombre='No crear'").fetchone()[0],0)

    def test_carousel_and_direct_purchase_markup(self):
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,enlace_ml='https://www.mercadolibre.cl/test',fotos=[(image(),'a.png'),(image(),'b.png')]))
        home=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn('Comprar en Mercado Libre',home)
        self.assertIn('data-autoplay="5000"',home)
        self.assertIn('Cómo comprar',home)
        detail=self.client.get('/catalogo/producto/1').get_data(as_text=True)
        self.assertIn('data-autoplay="5500"',detail)
        self.assertIn('Pausar cambio automático',detail)
        self.assertIn('Foto siguiente',detail)

    def test_remote_photos_and_backup(self):
        token=self.login()
        with patch('app.descargar_foto',return_value=image()):
            self.assertEqual(self.client.post('/admin/producto/nuevo',data=self.values(token,foto_urls='https://http2.mlstatic.com/D_test.webp')).status_code,302)
        response=self.client.get('/admin/respaldo')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            self.assertIn('data/practica.db',archive.namelist())
            self.assertEqual(len([name for name in archive.namelist() if name.startswith('data/uploads/')]),1)
        response.close()
        restarted=create_app(self.config).test_client()
        self.assertIn('Chal plateado',restarted.get('/catalogo').get_data(as_text=True))


if __name__=='__main__':
    unittest.main()
