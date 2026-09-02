from pathlib import Path

from django.test import SimpleTestCase


class SicorLargeImportPipelineTests(SimpleTestCase):
    def setUp(self):
        self.source = (
            Path(__file__).resolve().parents[1]
            / 'services' / 'sicor_import.py'
        ).read_text(encoding='utf-8')

    def test_staging_is_unlogged_for_large_sicor_files(self):
        self.assertIn('CREATE UNLOGGED TABLE', self.source)

    def test_copy_is_sequential_not_two_simultaneous_copies(self):
        self.assertIn('def _copy_batch', self.source)
        self.assertNotIn('with cursor.copy(raw_copy_sql) as raw_copy, cursor.copy(op_copy_sql) as op_copy', self.source)

    def test_heavy_geometry_work_happens_before_atomic_publication(self):
        prepare = self.source.index('def _prepare_gleba_points_aggregate')
        promote = self.source.index('def _promote_gleba_points')
        self.assertLess(prepare, promote)
        self.assertIn("_progress(progress_callback, 82, 'Publicando snapshot SICOR de forma atômica')", self.source)

    def test_large_snapshot_raw_does_not_keep_useless_year_index(self):
        self.assertIn("elif spec.data_kind == 'sicor_gleba_points':", self.source)
        self.assertIn('DROP INDEX IF EXISTS', self.source)
