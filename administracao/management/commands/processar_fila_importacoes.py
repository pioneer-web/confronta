import time

from django.core.management.base import BaseCommand

from administracao.services.batch import claim_next_item, process_batch_item, recover_stale_items, scan_inbox_once


class Command(BaseCommand):
    help = 'Processa sequencialmente a fila de importações manuais em lote do Manage CONFRONTA.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Mantém o worker aguardando novos itens.')
        parser.add_argument('--interval', type=int, default=5, help='Segundos entre verificações quando a fila estiver vazia.')
        parser.add_argument('--max-items', type=int, default=0, help='Limite de itens nesta execução; 0 = sem limite.')
        parser.add_argument(
            '--scan-inbox', action='store_true',
            help='Habilita explicitamente o scanner legado de import_inbox. Desativado por padrão no fluxo manual.',
        )

    def handle(self, *args, **options):
        recover_stale_items()
        processed = 0
        while True:
            # A arquitetura atual usa upload manual pelo painel. O scanner de
            # pasta permanece disponível apenas para diagnóstico/futuro uso e
            # nunca é executado silenciosamente pelo worker de produção.
            if options['scan_inbox']:
                scan_inbox_once()
            item = claim_next_item()
            if item:
                self.stdout.write(f'Processando lote #{item.lote_id}: {item.caminho_relativo}')
                process_batch_item(item)
                processed += 1
                if options['max_items'] and processed >= options['max_items']:
                    break
                continue
            if not options['loop']:
                break
            time.sleep(max(1, options['interval']))
        self.stdout.write(self.style.SUCCESS(f'Itens processados: {processed}'))
