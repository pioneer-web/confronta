import sqlite3
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from administracao.models import SicarColetaAutomatica, User
from administracao.services.sicar_automation import enqueue_sicar_collection
from administracao.services.sicar_tracking import fingerprint_layer_content


class SicarAutomationQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(email='admin@example.com', password='UmaSenhaForte123!')

    def test_manual_pe_collection_is_unique_while_active(self):
        first, created = enqueue_sicar_collection(usuario=self.user, uf='PE')
        second, created_again = enqueue_sicar_collection(usuario=self.user, uf='PE')
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, SicarColetaAutomatica.Status.AGUARDANDO_FILA)

    def test_pilot_rejects_other_state(self):
        with self.assertRaises(ValueError):
            enqueue_sicar_collection(usuario=self.user, uf='PI')


class GpkgFingerprintTests(TestCase):
    def _make_gpkg_like_sqlite(self, path, rows):
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE AREA_IMOVEL_PE (fid INTEGER PRIMARY KEY AUTOINCREMENT, cod_imovel TEXT, area REAL, geom BLOB)')
        conn.executemany('INSERT INTO AREA_IMOVEL_PE (cod_imovel, area, geom) VALUES (?, ?, ?)', rows)
        conn.commit()
        conn.close()

    def test_fingerprint_ignores_container_fid_and_row_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a.gpkg'
            b = Path(tmp) / 'b.gpkg'
            rows = [('PE-2', 20.0, b'geom2'), ('PE-1', 10.0, b'geom1')]
            self._make_gpkg_like_sqlite(a, rows)
            self._make_gpkg_like_sqlite(b, list(reversed(rows)))
            layer_a = {'dataset_path': str(a), 'layer_name': 'AREA_IMOVEL_PE'}
            layer_b = {'dataset_path': str(b), 'layer_name': 'AREA_IMOVEL_PE'}
            self.assertEqual(fingerprint_layer_content(layer_a), fingerprint_layer_content(layer_b))

class SicarSnapshotValidationTests(TestCase):
    @override_settings()
    def test_validate_snapshot_accepts_numpy_fields_array(self):
        """pyogrio.read_info() devolve fields como ndarray; não pode usar `or []`."""
        import numpy as np
        from unittest.mock import patch
        from administracao.services.sicar_automation import _validate_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'AREA_IMOVEL_PE.gpkg'
            path.write_bytes(b'0' * 5000)
            with patch('administracao.services.sicar_automation.pyogrio.list_layers', return_value=np.array([['AREA_IMOVEL_PE', 'MultiPolygon']], dtype=object)), \
                 patch('administracao.services.sicar_automation.pyogrio.read_info', return_value={
                     'features': 433655,
                     'fields': np.array(['cod_imovel', 'status_imovel', 'municipio'], dtype=object),
                     'geometry_type': 'MultiPolygon',
                     'crs': 'EPSG:4674',
                 }):
                metadata = _validate_snapshot(path, 'AREA_IMOVEL_PE')

        self.assertEqual(metadata['registros_reportados'], 433655)
        self.assertEqual(metadata['geometry_type'], 'MultiPolygon')
