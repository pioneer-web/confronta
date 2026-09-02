import json

from django.core.management.base import BaseCommand, CommandError

from administracao.models import SicarColetaAutomatica
from administracao.services.sicar_automation import enqueue_sicar_collection
from administracao.services.sicar_sources import SICAR_SYNC_ORDER, find_inbox_snapshot

PORTAL_DATASETS = tuple(slug for slug in SICAR_SYNC_ORDER if slug != 'sicar-perimetros')


class Command(BaseCommand):
    help = 'Enfileira os arquivos SICAR PE presentes na caixa AUTO, sem baixar novamente do portal.'

    def add_arguments(self, parser):
        parser.add_argument('--datasets', default='', help='Lista separada por vírgulas; vazio = todos os 8 temas do portal.')
        parser.add_argument('--json', action='store_true', help='Emite somente um resumo JSON.')

    def handle(self, *args, **options):
        requested = [value.strip() for value in str(options['datasets'] or '').split(',') if value.strip()]
        datasets = tuple(requested) if requested else PORTAL_DATASETS
        invalid = [slug for slug in datasets if slug not in PORTAL_DATASETS]
        if invalid:
            raise CommandError('Dataset(s) inválido(s): ' + ', '.join(invalid))

        queued = []
        missing = []
        active = []
        for slug in datasets:
            snapshot = find_inbox_snapshot(slug, 'PE')
            if not snapshot:
                missing.append(slug)
                continue
            job, created = enqueue_sicar_collection(
                origem=SicarColetaAutomatica.Origem.MANUAL,
                uf='PE',
                dataset_slug=slug,
            )
            if created:
                queued.append({'dataset': slug, 'job_id': job.pk, 'arquivo': snapshot.name})
            else:
                active.append({'dataset': slug, 'job_id': job.pk})

        result = {'queued': queued, 'already_active': active, 'missing': missing}
        if options['json']:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            return
        self.stdout.write(self.style.SUCCESS(f'Enfileiradas: {len(queued)} | já ativas: {len(active)} | ausentes: {len(missing)}'))
        for row in queued:
            self.stdout.write(f"  + {row['dataset']} -> job #{row['job_id']} ({row['arquivo']})")
        for slug in missing:
            self.stdout.write(self.style.WARNING(f'  - {slug}: arquivo não encontrado na caixa AUTO'))
