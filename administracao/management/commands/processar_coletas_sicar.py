import time

from django.core.management.base import BaseCommand

from administracao.services.sicar_automation import (
    claim_next_sicar_collection,
    enqueue_due_sicar_schedule,
    process_sicar_collection,
    recover_stale_sicar_collections,
    sync_sicar_collection_jobs,
)


class Command(BaseCommand):
    help = 'Monitora a fila de coleta automática do SICAR e agenda a rotina das 01:00 quando habilitada.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Mantém o coletor aguardando novas solicitações.')
        parser.add_argument('--interval', type=int, default=15, help='Segundos entre verificações quando a fila estiver vazia.')
        parser.add_argument('--max-items', type=int, default=0, help='Limite de coletas nesta execução; 0 = sem limite.')

    def handle(self, *args, **options):
        processed = 0
        recover_stale_sicar_collections()
        while True:
            enqueue_due_sicar_schedule()
            sync_sicar_collection_jobs()
            job = claim_next_sicar_collection()
            if job:
                self.stdout.write(f'Coletando SICAR {job.uf} — solicitação #{job.pk}')
                process_sicar_collection(job)
                processed += 1
                if options['max_items'] and processed >= options['max_items']:
                    break
                continue
            if not options['loop']:
                break
            time.sleep(max(2, options['interval']))
        sync_sicar_collection_jobs()
        self.stdout.write(self.style.SUCCESS(f'Coletas processadas: {processed}'))
