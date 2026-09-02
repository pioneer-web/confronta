import tempfile
import zipfile
from pathlib import Path
from django.test import SimpleTestCase, override_settings
from administracao.services.exceptions import SecurityValidationError
from administracao.services.zip_security import validate_zip


@override_settings(MAX_UPLOAD_SIZE_BYTES=0, MAX_ZIP_ENTRIES=100, MAX_ZIP_EXPANSION_RATIO=200, MAX_ZIP_UNCOMPRESSED_BYTES=0)
class ZipSecurityTests(SimpleTestCase):
    def _zip(self, entries):
        tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return Path(tmp.name)

    def test_bloqueia_todas_extensoes_executaveis_aprovadas(self):
        extensoes = ('.exe', '.dll', '.msi', '.bat', '.cmd', '.com', '.pif', '.scr', '.sh', '.bin', '.run')
        for ext in extensoes:
            with self.subTest(ext=ext):
                path = self._zip({'area.geojson': '{}', f'arquivo{ext}': b'conteudo'})
                with self.assertRaises(SecurityValidationError):
                    validate_zip(path)
                path.unlink(missing_ok=True)

    def test_aceita_extensoes_auxiliares_oficiais_qmd_e_fix(self):
        path = self._zip({
            'camada.shp': b'dados',
            'camada.qmd': b'metadados',
            'camada.fix': b'auxiliar',
        })
        report = validate_zip(path)
        self.assertEqual(report['entradas'], 3)
        path.unlink(missing_ok=True)

    def test_bloqueia_path_traversal(self):
        path = self._zip({'../fora.geojson': '{}'})
        with self.assertRaises(SecurityValidationError):
            validate_zip(path)
        path.unlink(missing_ok=True)

    def test_aceita_zip_com_geojson(self):
        path = self._zip({'area.geojson': '{"type":"FeatureCollection","features":[]}'})
        report = validate_zip(path)
        self.assertEqual(report['entradas'], 1)
        path.unlink(missing_ok=True)
