import time

from django.core.management.base import BaseCommand

from administracao.services.source_sync import (
    IBAMA_COLLECTOR_VERSION,
    process_next_job,
    schedule_due_jobs,
)


class Command(BaseCommand):
    help = 'Agenda e processa sincronizações automáticas IBAMA/INCRA.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write(f'IBAMA collector: {IBAMA_COLLECTOR_VERSION} | fonte: Dados Abertos IBAMA')
        loop = bool(options['loop']) and not options['once']
        interval = max(5, int(options['interval'] or 30))
        while True:
            schedule_due_jobs()
            worked = process_next_job()
            if not loop:
                break
            time.sleep(2 if worked else interval)
