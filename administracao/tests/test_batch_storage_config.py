from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class BatchStorageConfigTests(SimpleTestCase):
    def test_working_storage_is_separate_from_external_import_inbox(self):
        inbox = Path(settings.IMPORT_INBOX_DIR).resolve()
        storage = Path(settings.BATCH_STORAGE_DIR).resolve()
        self.assertNotEqual(storage, inbox)
        self.assertNotIn(inbox, storage.parents)
        self.assertEqual(Path(settings.BATCH_DIR).parent.resolve(), storage)

    def test_recovery_is_independent_from_working_storage(self):
        working = Path(settings.BATCH_DIR).resolve()
        recovery = Path(settings.BATCH_RECOVERY_DIR).resolve()
        self.assertNotEqual(recovery, working)
        self.assertNotEqual(recovery.parent, working.parent)
