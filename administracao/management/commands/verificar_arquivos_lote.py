from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from administracao.models import LoteImportacao
from administracao.services.batch import _batch_root_candidates, _batch_recovery_roots


class Command(BaseCommand):
    help = 'Verifica, sem alterar nada, os arquivos de trabalho e as referências de recuperação de um lote.'

    def add_arguments(self, parser):
        parser.add_argument('lote_id', type=int)

    def handle(self, *args, **options):
        try:
            lote = LoteImportacao.objects.prefetch_related('itens').get(pk=options['lote_id'])
        except LoteImportacao.DoesNotExist as exc:
            raise CommandError('Lote não encontrado.') from exc

        roots = _batch_root_candidates(lote)
        recovery_roots = _batch_recovery_roots(lote.pk)

        self.stdout.write(f'Lote #{lote.pk}')
        for index, root in enumerate(roots, 1):
            self.stdout.write(f'Área de trabalho candidata {index}: {root} | existe: {root.is_dir()}')
        for index, recovery_root in enumerate(recovery_roots, 1):
            self.stdout.write(f'Recuperação candidata {index}: {recovery_root} | existe: {recovery_root.exists()}')

        unavailable = 0
        for item in lote.itens.all():
            working = None
            for root in roots:
                candidate = (root / item.caminho_relativo).resolve()
                if candidate.is_file():
                    working = candidate
                    break
            recovery = None
            for recovery_root in recovery_roots:
                candidate = (recovery_root / item.caminho_relativo).resolve()
                if candidate.is_file():
                    recovery = candidate
                    break
                if recovery_root.is_dir():
                    matches = [value.resolve() for value in recovery_root.rglob(item.nome_arquivo) if value.is_file()]
                    if len(matches) == 1:
                        recovery = matches[0]
                        break

            working_ok = working is not None
            recovery_ok = recovery is not None and recovery.is_file()
            if not working_ok and not recovery_ok:
                unavailable += 1
            marker = 'OK' if working_ok else ('RECUPERÁVEL' if recovery_ok else 'AUSENTE')
            self.stdout.write(
                f'[{marker}] item #{item.pk}: {item.caminho_relativo} '
                f'| trabalho={working_ok} | recuperação={recovery_ok}'
            )

        if unavailable:
            raise CommandError(
                f'{unavailable} arquivo(s) não existem nem em working nem na recuperação. '
                'Somente esses arquivos precisam ser reenviados antes do reprocessamento.'
            )
        self.stdout.write(self.style.SUCCESS('Todos os itens estão disponíveis ou recuperáveis.'))
