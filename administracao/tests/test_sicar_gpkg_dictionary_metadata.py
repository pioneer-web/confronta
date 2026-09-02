import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from administracao.services.gis_inspector import inspect_dataset
from administracao.services.schema_drift import snapshot_layer, compare_schema


class SicarGpkgDictionaryMetadataTests(SimpleTestCase):
    def _fixture(self, path):
        con = sqlite3.connect(path)
        con.executescript('''
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT,
                description TEXT DEFAULT '', srs_id INTEGER
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT PRIMARY KEY, column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z INTEGER, m INTEGER
            );
            CREATE TABLE DICIONARIO (
                id INTEGER PRIMARY KEY, name_column TEXT, type_column TEXT,
                description TEXT, character_set TEXT, srid INTEGER,
                created_date TEXT, future_metadata TEXT
            );
            INSERT INTO gpkg_contents VALUES ('AREA_IMOVEL','features','AREA_IMOVEL','',4326);
            INSERT INTO gpkg_contents VALUES ('DICIONARIO','attributes','DICIONARIO','',NULL);
            INSERT INTO gpkg_geometry_columns VALUES ('AREA_IMOVEL','the_geom','MULTIPOLYGON',4326,0,0);
            INSERT INTO DICIONARIO VALUES
                (1,'cod_imovel','string','Número de inscrição no CAR','utf-8',4674,'2026-08-23 02:00:02','preservar'),
                (2,'num_area','double','Área em hectares','utf-8',4674,'2026-08-23 02:00:02','preservar'),
                (3,'the_geom','multipolygon','Geometria declarada','utf-8',4674,'2026-08-23 02:00:02','preservar');
        ''')
        con.commit(); con.close()

    def test_dictionary_is_preserved_and_compared_without_overriding_spatial_crs(self):
        with TemporaryDirectory() as tmp:
            gpkg = Path(tmp) / 'PE_AREA_IMOVEL.gpkg'
            self._fixture(gpkg)

            def fake_info(path, layer=None, **kwargs):
                if layer == 'DICIONARIO':
                    return {'crs': None, 'geometry_type': None,
                            'fields': ['name_column','type_column','description','character_set','srid','created_date','future_metadata'],
                            'dtypes': ['object']*7, 'features': 3, 'encoding': 'UTF-8'}
                return {'crs': 'EPSG:4326', 'geometry_type': 'MultiPolygon',
                        'fields': ['cod_imovel','num_area'], 'dtypes': ['object','float64'],
                        'features': 10, 'encoding': 'UTF-8'}

            with patch('administracao.services.gis_inspector.pyogrio.list_layers', return_value=[
                ['AREA_IMOVEL', 'MultiPolygon'], ['DICIONARIO', None]
            ]), patch('administracao.services.gis_inspector.pyogrio.read_info', side_effect=fake_info):
                layers = inspect_dataset(gpkg)

        spatial = layers[0]
        meta = spatial['sicar_dictionary']
        self.assertTrue(meta['present'])
        self.assertEqual(meta['row_count'], 3)
        self.assertIn('future_metadata', meta['unknown_dictionary_columns'])
        self.assertEqual(meta['comparison']['actual_epsg'], 4326)
        self.assertEqual(meta['comparison']['dictionary_srids'], [4674])
        self.assertTrue(meta['comparison']['crs_divergence'])
        self.assertTrue(meta['comparison']['consistent_fields'])
        self.assertEqual(meta['raw_rows'][0]['future_metadata'], 'preservar')

        snapshot = snapshot_layer(spatial)
        self.assertTrue(snapshot['sicar_dictionary']['present'])
        drift = compare_schema(None, snapshot)
        self.assertFalse(drift['changed'])
        self.assertEqual(drift['source_metadata_warnings'][0]['type'], 'DICTIONARY_CRS_DIVERGENCE')

    def test_dictionary_description_changes_are_audited_without_forcing_db_structure_change(self):
        current = {
            'layer_name': 'AREA_IMOVEL', 'dataset_name': 'a.gpkg', 'fields': [],
            'geometry_type': 'MultiPolygon', 'crs': 'EPSG:4326', 'epsg': 4326, 'signature': 'x',
            'sicar_dictionary': {'present': True, 'field_catalog': [
                {'name': 'cod_imovel', 'type': 'string', 'description': 'Nova descrição'}
            ], 'comparison': {'dictionary_srids': [4674], 'actual_epsg': 4326, 'crs_divergence': True}},
        }
        previous = {
            **current,
            'sicar_dictionary': {'present': True, 'field_catalog': [
                {'name': 'cod_imovel', 'type': 'string', 'description': 'Descrição antiga'}
            ], 'comparison': {'dictionary_srids': [4674], 'actual_epsg': 4326, 'crs_divergence': True}},
        }
        report = compare_schema(previous, current)
        self.assertFalse(report['changed'])
        self.assertEqual(report['source_metadata_changes'][0]['type'], 'DICTIONARY_DESCRIPTION_CHANGED')
