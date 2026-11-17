import io
import sqlite3
import unittest
from unittest.mock import patch
import test_catalogo as original
import test_mercadolibre as meli
from categorias_ml import classify
from respaldos import make_backup, review_backup, apply_review


class CategoriesMLTest(unittest.TestCase):
    setUp = original.CatalogTest.setUp
    tearDown = original.CatalogTest.tearDown
    token = original.CatalogTest.token
    login = original.CatalogTest.login
    values = original.CatalogTest.values
    setup_meli = meli.MeliTest.setup_meli
    item = meli.MeliTest.item

    def import_title(self, title):
        with patch('mercadolibre.api_request', side_effect=[self.item(title=title), {'plain_text':'Descripción'}]):
            self.ml['import_item']('MLC123456789')

    def category(self):
        with sqlite3.connect(self.data/'practica.db') as c:
            return c.execute('SELECT c.nombre FROM catalogo_productos p JOIN catalogo_categorias c ON c.id=p.categoria_id ORDER BY p.id LIMIT 1').fetchone()[0]

    def test_specific_families_and_word_boundaries(self):
        cases = {'Disfraz vestido bruja':'Disfraces','Vestido de fiesta':'Vestidos','Vestido niña':'Vestidos de niña',
            'Gorro invierno':'Gorros','Polerón unisex':'Polerones unisex','Polerones unisex':'Polerones unisex',
            'Polera unisex':'Poleras unisex','Rompecabezas 1000 piezas':'Rompecabezas','Juego mesa':'Juegos',
            'Blusa elegante':'Blusas','Bolero elegante':'Boleros elegantes','Blazer negro':'Blazers','Chal fiesta':'Chales de fiesta',
            'Capa medieval':'Capas medievales','Conjunto niño':'Conjuntos de niño','Sombrero texano':'Sombreros texanos',
            'Poncho lana':'Ponchos','Traje de huasa':'Trajes de huaso y huasa','Polera manga larga con escote':'Poleras manga larga',
            'Chaleco':'Por clasificar','Polera estampada rompecabezas':'Poleras'}
        for title, expected in cases.items():
            with self.subTest(title=title):self.assertEqual(classify(title),expected)
        self.assertEqual(classify('Modelo ABC','Juguetes > Rompecabezas'),'Rompecabezas')

    def test_import_and_manual_choice_survive_resync(self):
        self.setup_meli();token=self.login();self.import_title('Chal fiesta')
        self.assertEqual(self.category(),'Chales de fiesta')
        response=self.client.post('/admin/producto/1',data=self.values(token,categoria_id='1'))
        self.assertEqual(response.status_code,302)
        self.import_title('Chal fiesta nuevo')
        self.assertEqual(self.category(),'Vestuario')
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT manual FROM catalogo_ml_clasificacion').fetchone()[0],1)

    def test_bulk_existing_products_only_and_csrf(self):
        self.setup_meli();token=self.login();self.import_title('Rompecabezas infantil')
        with sqlite3.connect(self.data/'practica.db') as c:
            c.execute("INSERT OR IGNORE INTO catalogo_categorias(nombre) VALUES ('Mercado Libre')")
            generic=c.execute("SELECT id FROM catalogo_categorias WHERE nombre='Mercado Libre'").fetchone()[0]
            c.execute('DELETE FROM catalogo_ml_clasificacion')
            c.execute('UPDATE catalogo_productos SET categoria_id=?',(generic,))
        self.client.post('/admin/producto/nuevo',data=self.values(token,nombre='Blusa manual',categoria_id=str(generic)))
        self.assertEqual(self.client.get('/admin/categorias/automaticas').status_code,200)
        self.assertEqual(self.category(),'Mercado Libre')
        self.assertEqual(self.client.post('/admin/categorias/automaticas').status_code,400)
        self.assertEqual(self.client.post('/admin/categorias/automaticas',data={'csrf_token':token}).status_code,302)
        self.assertEqual(self.category(),'Rompecabezas')
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT categoria_id FROM catalogo_productos WHERE id=2').fetchone()[0],generic)
        self.assertEqual(self.app.test_client().get('/admin/categorias/automaticas').status_code,302)

    def test_taxonomy_fallback_and_cache(self):
        self.setup_meli();prepare=self.app.extensions['clasificacion']['prepare']
        with patch('categorias_ml.api_request',return_value={'id':'MLC1234','path_from_root':[{'name':'Juguetes'},{'name':'Rompecabezas'}]}) as api:
            for _ in range(2):self.assertEqual(prepare({'title':'Modelo ABC','category_id':'MLC1234'},'token')['label'],'Rompecabezas')
            api.assert_called_once_with('/categories/MLC1234','token')

    def test_rename_preserves_manual_category(self):
        self.setup_meli();token=self.login();self.import_title('Chal fiesta')
        with sqlite3.connect(self.data/'practica.db') as c:
            category_id=c.execute('SELECT categoria_id FROM catalogo_productos WHERE id=1').fetchone()[0]
        self.client.post('/admin/categorias',data={'csrf_token':token,'id':str(category_id),'nombre':'Mis chales'})
        self.import_title('Chal fiesta')
        self.assertEqual(self.category(),'Mis chales')
        archive=self.data/'clasificacion.zip'
        make_backup(self.data/'practica.db',self.data/'uploads',archive)
        review,_=review_backup(io.BytesIO(archive.read_bytes()),self.data)
        with sqlite3.connect(self.data/'practica.db') as c:
            c.execute('DELETE FROM catalogo_ml_clasificacion')
        apply_review(self.data/'practica.db',self.data,review)
        self.import_title('Chal fiesta')
        self.assertEqual(self.category(),'Mis chales')
        with sqlite3.connect(self.data/'practica.db') as c:
            self.assertEqual(c.execute('SELECT manual FROM catalogo_ml_clasificacion').fetchone()[0],1)
