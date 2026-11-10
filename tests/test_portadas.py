import io
import sqlite3
import unittest
import zipfile
from pathlib import Path
import test_catalogo as original
from respaldos import apply_review, review_backup

class CoversTest(unittest.TestCase):
    setUp = original.CatalogTest.setUp
    tearDown = original.CatalogTest.tearDown
    token = original.CatalogTest.token
    login = original.CatalogTest.login
    values = original.CatalogTest.values

    def rows(self):
        with sqlite3.connect(self.data/'practica.db') as conn:
            conn.row_factory=sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM catalogo_portadas ORDER BY orden,id')]

    def test_menu_order_visibility_independent_copy_and_restart(self):
        self.assertEqual(self.client.get('/admin/portadas').status_code,302)
        token=self.login()
        self.client.post('/admin/producto/nuevo',data=self.values(token,fotos=[(original.image(),'product.png')]))
        self.assertIn('Fotos de portada',self.client.get('/admin/portadas').get_data(as_text=True))
        self.client.post('/admin/portadas',data={'csrf_token':token,'foto_producto':'1','titulo':'Elegido'})
        self.client.post('/admin/portadas',data={'csrf_token':token,'fotos':[(original.image('blue'),'blue.png')],'titulo':'Segundo'})
        a,b=self.rows()
        self.client.post('/admin/portada/2/subir',data={'csrf_token':token})
        self.assertEqual([r['id'] for r in self.rows()],[2,1])
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertLess(html.index(b['archivo']),html.index(a['archivo']))
        self.client.post('/admin/foto/1/quitar',data={'csrf_token':token})
        self.assertTrue((self.data/'uploads'/a['archivo']).exists())
        self.client.post('/admin/portada/2/guardar',data={'csrf_token':token,'titulo':'Oculta'})
        public=create_app(self.config).test_client()
        html=public.get('/catalogo').get_data(as_text=True)
        self.assertIn(a['archivo'],html)
        self.assertNotIn(b['archivo'],html)
        self.assertNotIn('data-pause',html)
        self.assertEqual(public.get('/fotos/'+b['archivo']).status_code,404)
        self.client.post('/admin/portada/1/quitar',data={'csrf_token':token})
        self.assertFalse((self.data/'uploads'/a['archivo']).exists())
        self.assertIn('/static/img/chal.webp',public.get('/catalogo').get_data(as_text=True))

    def test_validation_and_backup_roundtrip_older_schema(self):
        token=self.login()
        self.client.post('/admin/portadas',data={'csrf_token':token,'fotos':[(original.image(),'a.png'),(io.BytesIO(b'bad'),'bad.png')]})
        self.assertEqual(self.rows(),[])
        self.assertEqual(list((self.data/'uploads').iterdir()),[])
        self.client.post('/admin/portadas',data={'csrf_token':token,'titulo':'Guardada','fotos':[(original.image(),'a.png')]})
        response=self.client.get('/admin/respaldo');content=response.get_data();response.close()
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            self.assertEqual(len(z.namelist()),2)
        review,summary=review_backup(io.BytesIO(content),self.data)
        self.assertEqual(summary['portadas'],1)
        apply_review(self.data/'practica.db',self.data,review)
        self.assertEqual(self.rows()[0]['titulo'],'Guardada')
        self.assertTrue((self.data/'uploads'/self.rows()[0]['archivo']).exists())
        # Un respaldo de la versión anterior no trae catalogo_portadas.
        old_db=self.data/'old.db'
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            old_db.write_bytes(z.read('data/practica.db'))
        with sqlite3.connect(old_db) as c:
            c.execute('DROP TABLE catalogo_portadas')
        older=io.BytesIO()
        with zipfile.ZipFile(older,'w') as z:
            z.write(old_db,'data/practica.db')
        older.seek(0)
        review,summary=review_backup(older,self.data)
        self.assertEqual(summary['portadas'],0)
        apply_review(self.data/'practica.db',self.data,review)
        self.assertEqual(self.rows(),[])
        self.assertIn('/static/img/chal.webp',self.client.get('/catalogo').get_data(as_text=True))

from app import create_app
