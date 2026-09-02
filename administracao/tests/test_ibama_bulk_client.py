import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from administracao.services.ibama_bulk_sync import (
    IBAMA_COLLECTOR_VERSION,
    _analyze_main_csv,
    _coordinate_fallback,
    _extract_wkt_from_payload,
    _materialize_main_csv,
)


class IbamaBulkClientTests(SimpleTestCase):
    def test_collector_is_bulk_open_data(self):
        self.assertEqual(IBAMA_COLLECTOR_VERSION, 'bulk-dados-abertos-v0.4.2')

    def test_extract_wkt_from_nested_json(self):
        payload = {'resultado': {'geometria': 'MULTIPOLYGON (((-35 -8,-34 -8,-34 -7,-35 -8)))'}}
        value = _extract_wkt_from_payload(payload)
        self.assertTrue(value.startswith('MULTIPOLYGON'))

    def test_main_resource_accepts_direct_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'download.bin'
            source.write_text(
                'SEQ_TAD;GEOM_AREA_EMBARGADA\n1;POLYGON ((-35 -8,-34 -8,-34 -7,-35 -8))\n',
                encoding='utf-8',
            )
            out = _materialize_main_csv(source, root / 'out')
            self.assertTrue(out.exists())
            self.assertIn('SEQ_TAD', out.read_text(encoding='utf-8'))

    def test_main_resource_accepts_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'download.bin'
            with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    'termo_embargo.csv',
                    'SEQ_TAD;GEOM_AREA_EMBARGADA\n1;POLYGON ((-35 -8,-34 -8,-34 -7,-35 -8))\n',
                )
            out = _materialize_main_csv(source, root / 'out')
            self.assertEqual(out.name, 'termo_embargo.csv')

    def test_main_csv_uses_seq_tad_and_detects_missing_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'termos.csv'
            path.write_text(
                'SEQ_TAD;NUM_TAD;GEOM_AREA_EMBARGADA\n'
                '1;A;POLYGON ((-35 -8,-34 -8,-34 -7,-35 -8))\n'
                '2;B;\n',
                encoding='utf-8',
            )
            result = _analyze_main_csv(path)
            self.assertEqual(result['records'], 2)
            self.assertEqual(result['keys'], {'1', '2'})
            self.assertEqual(result['missing_geometry'], {'2'})

    def test_main_csv_blocks_duplicate_seq_tad(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'termos.csv'
            path.write_text(
                'SEQ_TAD;GEOM_AREA_EMBARGADA\n'
                '1;POLYGON ((-35 -8,-34 -8,-34 -7,-35 -8))\n'
                '1;POLYGON ((-36 -8,-35 -8,-35 -7,-36 -8))\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, 'duplicado'):
                _analyze_main_csv(path)


    def test_main_csv_tolerates_isolated_missing_seq_tad(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'termos.csv'
            rows = ['SEQ_TAD;WKT']
            for i in range(1, 101):
                key = '' if i == 50 else str(i)
                rows.append(f'{key};POLYGON ((-35 -8,-34 -8,-34 -7,-35 -8))')
            path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
            result = _analyze_main_csv(path)
            self.assertEqual(result['records'], 100)
            self.assertEqual(result['valid_records'], 99)
            self.assertEqual(result['invalid_key_rows'], 1)
            self.assertNotIn('', result['keys'])

    def test_seq_tad_decimal_text_is_normalized(self):
        from administracao.services.ibama_bulk_sync import _normalize_seq_tad
        self.assertEqual(_normalize_seq_tad('1881095.0'), '1881095')
        self.assertEqual(_normalize_seq_tad(' 1881095 '), '1881095')
        self.assertEqual(_normalize_seq_tad(''), '')
        self.assertEqual(_normalize_seq_tad('ABC'), '')

    def test_coordinates_reconstruct_polygon_only_for_wanted_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'coords.csv'
            path.write_text(
                'SEQ_TAD;SEQ_POLIGONO;ORDEM;LONGITUDE;LATITUDE\n'
                '2;10;1;-35;-8\n'
                '2;10;2;-34;-8\n'
                '2;10;3;-34;-7\n'
                '2;10;4;-35;-8\n'
                '3;20;1;-40;-9\n',
                encoding='utf-8',
            )
            result = _coordinate_fallback(path, {'2'})
            self.assertIn('2', result)
            self.assertNotIn('3', result)
            self.assertFalse(result['2'].is_empty)
