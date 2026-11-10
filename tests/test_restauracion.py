import io
import json
import sqlite3
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import create_app
from respaldos import StorageLock, apply_review, review_backup, stage_path
import test_catalogo as original
image = original.image


class RestoreTest(unittest.TestCase):
    setUp = original.CatalogTest.setUp
    tearDown = original.CatalogTest.tearDown
    token = original.CatalogTest.token
    login = original.CatalogTest.login
    values = original.CatalogTest.values
    def archive(self):
        response=self.client.get('/admin/respaldo')
        self.assertEqual(response.status_code,200)
        content=response.get_data()
        response.close()
        return content

    def post_archive(self, content, token):
        return self.client.post('/admin/restaurar/revisar',data={'csrf_token':token,'respaldo':(io.BytesIO(content),'respaldo.zip')})

    def add_product(self,token,name='Producto anterior',photos=True):
        values=self.values(token,nombre=name)
        if photos:
            values['fotos']=[(image(),'foto.png')]
        self.assertEqual(self.client.post('/admin/producto/nuevo',data=values).status_code,302)

    def test_full_restore_and_recover_previous(self):
        token=self.login()
        self.add_product(token,'Chal original')
        content=self.archive()
        self.add_product(token,'Blazer agregado después')
        self.assertEqual(self.post_archive(content,token).status_code,302)
        html=self.client.get('/admin/restaurar').get_data(as_text=True)
        self.assertIn('RESPALDO REVISADO',html)
        # Revisar no cambia la base.
        self.assertIn('Blazer agregado',self.client.get('/catalogo').get_data(as_text=True))
        self.client.post('/admin/restaurar/confirmar',data={'csrf_token':token,'confirmar':'si'})
        html=self.client.get('/catalogo').get_data(as_text=True)
        self.assertIn('Chal original',html)
        self.assertNotIn('Blazer agregado',html)
        with sqlite3.connect(self.data/'practica.db') as conn:
            self.assertEqual(conn.execute('SELECT nombre FROM personas').fetchone()[0],'Dato anterior')
            filename=conn.execute('SELECT archivo FROM catalogo_fotos').fetchone()[0]
        response=self.client.get('/fotos/'+filename)
        self.assertEqual(response.status_code,200)
        response.close()
        copies=list((self.data/'respaldos_automaticos').glob('*.zip'))
        self.assertEqual(len(copies),1)
        response=self.client.get('/admin/respaldos/'+copies[0].name)
        before=response.get_data();response.close()
        self.assertEqual(self.post_archive(before,token).status_code,302)
        self.client.post('/admin/restaurar/confirmar',data={'csrf_token':token,'confirmar':'si'})
        self.assertIn('Blazer agregado',self.client.get('/catalogo').get_data(as_text=True))
        self.assertIn('Chal original',create_app(self.config).test_client().get('/catalogo').get_data(as_text=True))

    def test_confirmation_is_required_and_cancel_is_safe(self):
        token=self.login()
        content=self.archive()
        self.add_product(token)
        self.post_archive(content,token)
        self.client.post('/admin/restaurar/confirmar',data={'csrf_token':token})
        self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))
        self.client.post('/admin/restaurar/cancelar',data={'csrf_token':token})
        self.assertEqual(list((self.data/'.restauraciones').iterdir()),[])
        self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))

    def test_invalid_zip_missing_photo_and_wrong_schema(self):
        token=self.login()
        self.add_product(token)
        valid=self.archive()
        with zipfile.ZipFile(io.BytesIO(valid)) as archive:
            database=archive.read('data/practica.db')
        missing=io.BytesIO()
        with zipfile.ZipFile(missing,'w') as archive:
            archive.writestr('data/practica.db',database)
        wrong=io.BytesIO()
        with zipfile.ZipFile(wrong,'w') as archive:
            archive.writestr('data/practica.db',b'no es sqlite')
        for content in (b'no es zip',missing.getvalue(),wrong.getvalue()):
            self.assertEqual(self.post_archive(content,token).status_code,400)
            self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))
        self.assertEqual(list((self.data/'.restauraciones').iterdir()),[])

    def test_zip_paths_and_duplicate_members_blocked(self):
        token=self.login()
        for name in ('../escape.txt','/tmp/escape.txt','data/uploads/../../x.webp','data\\uploads\\bad.webp','app.py'):
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:
                archive.writestr(name,b'bad')
            self.assertEqual(self.post_archive(buffer.getvalue(),token).status_code,400)
        duplicate=io.BytesIO()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(duplicate,'w') as archive:
                archive.writestr('data/practica.db',b'a')
                archive.writestr('data/practica.db',b'b')
        self.assertEqual(self.post_archive(duplicate.getvalue(),token).status_code,400)

    def test_restoration_is_private_and_csrf_protected(self):
        token=self.login()
        content=self.archive()
        self.assertEqual(self.client.post('/admin/restaurar/revisar',data={'respaldo':(io.BytesIO(content),'a.zip')}).status_code,400)
        self.post_archive(content,token)
        with self.client.session_transaction() as state:
            state.pop('admin')
        self.assertEqual(self.client.get('/admin/restaurar').status_code,302)
        self.assertEqual(self.client.post('/admin/restaurar/confirmar',data={'csrf_token':token,'confirmar':'si'}).status_code,302)
        self.assertEqual(self.client.get('/admin/respaldos/antes_20260101T000000_abcdef12.zip').status_code,302)

    def test_transaction_failure_keeps_original_data_and_photos(self):
        token=self.login()
        self.add_product(token,'Fuente')
        content=self.archive()
        self.add_product(token,'Actual')
        review_token,_=review_backup(io.BytesIO(content),self.data)
        originals=set(p.name for p in (self.data/'uploads').iterdir())
        with sqlite3.connect(self.data/'practica.db') as conn:
            conn.execute("CREATE TRIGGER test_block BEFORE INSERT ON catalogo_productos BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.Error):
            apply_review(str(self.data/'practica.db'),self.data,review_token)
        self.assertEqual(set(p.name for p in (self.data/'uploads').iterdir()),originals)
        with sqlite3.connect(self.data/'practica.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM catalogo_productos').fetchone()[0],2)
            conn.execute('DROP TRIGGER test_block')
        self.assertIn('Actual',self.client.get('/catalogo').get_data(as_text=True))

    def test_file_failure_leaves_database_unchanged(self):
        token=self.login()
        self.add_product(token)
        content=self.archive()
        review_token,_=review_backup(io.BytesIO(content),self.data)
        originals=set(p.name for p in (self.data/'uploads').iterdir())
        with patch('respaldos.shutil.copyfileobj',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):apply_review(str(self.data/'practica.db'),self.data,review_token)
        self.assertEqual(set(p.name for p in (self.data/'uploads').iterdir()),originals)
        self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))

    def test_lock_prevents_parallel_writers(self):
        first=StorageLock(self.data/'lock').acquire()
        with self.assertRaises(TimeoutError):StorageLock(self.data/'lock').acquire(timeout=0.05)
        first.release()
        second=StorageLock(self.data/'lock').acquire(timeout=0.05)
        second.release()

    def test_corrupt_image_rejected(self):
        token=self.login()
        self.add_product(token)
        source=self.archive()
        buffer=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as archive,zipfile.ZipFile(buffer,'w') as result:
            for name in archive.namelist():
                result.writestr(name,b'not an image' if name.startswith('data/uploads/') else archive.read(name))
        self.assertEqual(self.post_archive(buffer.getvalue(),token).status_code,400)
        self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))

    def test_expired_review_changes_nothing(self):
        token=self.login()
        self.add_product(token)
        content=self.archive()
        self.post_archive(content,token)
        with self.client.session_transaction() as state:
            review_token=state['restore_token']
        manifest_path=stage_path(self.data,review_token)/'revision.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['created']=time.time()-4000
        manifest_path.write_text(json.dumps(manifest))
        self.client.post('/admin/restaurar/confirmar',data={'csrf_token':token,'confirmar':'si'})
        self.assertIn('Producto anterior',self.client.get('/catalogo').get_data(as_text=True))


if __name__=='__main__':unittest.main()
