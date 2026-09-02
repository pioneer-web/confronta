from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from administracao.services.batch import _item_archive_path


class _FakeLote:
    def __init__(self, pk, extracted_path):
        self.pk = pk
        self.id = pk
        self.extracted_path = str(extracted_path)
        self.saved = []

    def save(self, update_fields=None):
        self.saved.append(tuple(update_fields or []))


class _FakeItem:
    def __init__(self, lote, relative, name):
        self.pk = 91
        self.lote = lote
        self.lote_id = lote.pk
        self.caminho_relativo = relative
        self.nome_arquivo = name
        self.saved = []

    def save(self, update_fields=None):
        self.saved.append(tuple(update_fields or []))


class BatchStorageRecoveryTests(SimpleTestCase):
    def test_finds_file_in_current_canonical_root_when_stored_path_is_stale(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            batch_storage = base / 'manage_batches'
            working = batch_storage / 'working'
            recovery = batch_storage / 'recovery'
            inbox = base / 'import_inbox'
            canonical = working / 'lote_23'
            target = canonical / 'item_0001' / 'adm_embargo_ibama_a.shp.zip'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'ibama')
            recovery.mkdir(parents=True)
            inbox.mkdir(parents=True)

            lote = _FakeLote(23, base / 'caminho_antigo' / 'lote_23')
            item = _FakeItem(lote, 'item_0001/adm_embargo_ibama_a.shp.zip', target.name)

            with override_settings(
                BASE_DIR=base,
                BATCH_STORAGE_DIR=batch_storage,
                BATCH_DIR=working,
                BATCH_RECOVERY_DIR=recovery,
                IMPORT_INBOX_DIR=inbox,
            ):
                found = _item_archive_path(item)

            self.assertEqual(found, target.resolve())
            self.assertEqual(Path(lote.extracted_path), canonical.resolve())

    def test_recreates_missing_working_root_from_recovery(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            batch_storage = base / 'manage_batches'
            working = batch_storage / 'working'
            recovery = batch_storage / 'recovery'
            inbox = base / 'import_inbox'
            recovery_file = recovery / 'lote_23' / 'item_0001' / 'adm_embargo_ibama_a.shp.zip'
            recovery_file.parent.mkdir(parents=True)
            recovery_file.write_bytes(b'ibama-recovery')
            working.mkdir(parents=True)
            inbox.mkdir(parents=True)

            stale_root = base / 'missing_working' / 'lote_23'
            lote = _FakeLote(23, stale_root)
            item = _FakeItem(lote, 'item_0001/adm_embargo_ibama_a.shp.zip', recovery_file.name)

            with override_settings(
                BASE_DIR=base,
                BATCH_STORAGE_DIR=batch_storage,
                BATCH_DIR=working,
                BATCH_RECOVERY_DIR=recovery,
                IMPORT_INBOX_DIR=inbox,
            ):
                found = _item_archive_path(item)

            expected = working / 'lote_23' / item.caminho_relativo
            self.assertEqual(found, expected.resolve())
            self.assertEqual(found.read_bytes(), b'ibama-recovery')
            self.assertEqual(Path(lote.extracted_path), (working / 'lote_23').resolve())

from unittest.mock import patch
from administracao.services.batch import _create_recovery_link


class BatchRecoveryCopyFallbackTests(SimpleTestCase):
    def test_copy_fallback_is_used_when_hardlink_is_not_supported(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            recovery = base / 'recovery'
            source = base / 'working' / 'lote_30' / 'item_0001' / 'sicor_glebas_wkt_2026.gz'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'\x1f\x8bteste')
            with override_settings(BATCH_RECOVERY_DIR=recovery):
                with patch('administracao.services.batch.os.link', side_effect=OSError('hardlink indisponivel')):
                    result = _create_recovery_link(source, 30, 'item_0001/sicor_glebas_wkt_2026.gz')
            self.assertIsNotNone(result)
            self.assertTrue(result.is_file())
            self.assertEqual(result.read_bytes(), source.read_bytes())


class BatchLegacyStorageRecoveryTests(SimpleTestCase):
    def test_recovers_item_from_legacy_named_volume_when_new_working_is_empty(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            storage = base / 'new_manage_batches'
            working = storage / 'working'
            inbox = base / 'import_inbox'
            recovery = inbox / '.manage_batches' / 'recovery'
            legacy_storage = base / 'legacy_manage_batches'
            legacy_working = legacy_storage / 'working'
            legacy_recovery = legacy_storage / 'recovery'
            legacy_file = legacy_working / 'lote_35' / 'item_0001' / 'tis_poligonais.zip'
            legacy_file.parent.mkdir(parents=True)
            legacy_file.write_bytes(b'funai')
            working.mkdir(parents=True)
            recovery.mkdir(parents=True)

            lote = _FakeLote(35, base / 'stale' / 'lote_35')
            item = _FakeItem(lote, 'item_0001/tis_poligonais.zip', 'tis_poligonais.zip')

            with override_settings(
                BASE_DIR=base,
                BATCH_STORAGE_DIR=storage,
                BATCH_DIR=working,
                BATCH_RECOVERY_DIR=recovery,
                BATCH_LEGACY_STORAGE_DIR=legacy_storage,
                BATCH_LEGACY_DIR=legacy_working,
                BATCH_LEGACY_RECOVERY_DIR=legacy_recovery,
                IMPORT_INBOX_DIR=inbox,
            ):
                found = _item_archive_path(item)

            self.assertEqual(found, legacy_file.resolve())
            self.assertEqual(Path(lote.extracted_path), (legacy_working / 'lote_35').resolve())

    def test_recovers_item_from_independent_recovery_when_working_disappears(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            storage = base / 'manage_batches'
            working = storage / 'working'
            inbox = base / 'import_inbox'
            recovery = inbox / '.manage_batches' / 'recovery'
            recovery_file = recovery / 'lote_32' / 'item_0001' / 'Areas de Quilombolas.zip'
            recovery_file.parent.mkdir(parents=True)
            recovery_file.write_bytes(b'incra-quilombola')
            working.mkdir(parents=True)

            lote = _FakeLote(32, working / 'lote_32')
            item = _FakeItem(lote, 'item_0001/Areas de Quilombolas.zip', 'Areas de Quilombolas.zip')

            with override_settings(
                BASE_DIR=base,
                BATCH_STORAGE_DIR=storage,
                BATCH_DIR=working,
                BATCH_RECOVERY_DIR=recovery,
                BATCH_LEGACY_STORAGE_DIR=None,
                BATCH_LEGACY_DIR=None,
                BATCH_LEGACY_RECOVERY_DIR=None,
                IMPORT_INBOX_DIR=inbox,
            ):
                found = _item_archive_path(item)

            expected = working / 'lote_32' / 'item_0001' / 'Areas de Quilombolas.zip'
            self.assertEqual(found, expected.resolve())
            self.assertEqual(found.read_bytes(), b'incra-quilombola')
