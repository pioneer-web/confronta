from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from administracao.constants import FONTE_SLUGS
from administracao.datasets import get_dataset, source_groups, datasets_for_source
from administracao.permissions import admin_required, superadmin_required
from administracao.source_catalog import catalog_sections, catalog_summary, get_source_profile
from administracao.services.data_cleanup import clear_dataset_data, clear_source_data, dataset_storage_status


@admin_required
def catalogo_bases(request):
    return render(
        request,
        'administracao/bases/catalogo.html',
        {
            'sections': catalog_sections(),
            'summary': catalog_summary(),
        },
    )


@admin_required
def base_detalhe(request, source_slug):
    source = get_source_profile(source_slug)
    if source is None:
        return redirect('administracao:catalogo_bases')

    groups = source_groups(source.import_source_slug) if source.is_importable else []
    return render(
        request,
        'administracao/bases/detalhe.html',
        {
            'source': source,
            'groups': groups,
        },
    )


@superadmin_required
def limpar_dataset(request, source_slug, dataset_slug):
    spec = get_dataset(dataset_slug)
    if spec is None or spec.fonte_slug != source_slug:
        messages.error(request, 'Dataset não reconhecido para esta fonte.')
        return redirect('administracao:catalogo_bases')

    storage = dataset_storage_status(spec)
    confirmation_token = f'EXCLUIR {spec.slug}'
    if request.method == 'POST':
        typed = str(request.POST.get('confirmacao') or '').strip()
        if typed != confirmation_token:
            messages.error(request, f'Confirmação inválida. Digite exatamente: {confirmation_token}')
        else:
            try:
                result = clear_dataset_data(spec, request.user)
            except Exception as exc:
                messages.error(
                    request,
                    f'A limpeza foi bloqueada e revertida pelo banco: {exc}',
                )
            else:
                messages.success(
                    request,
                    'Dados atuais excluídos com segurança: '
                    f'{result["registros_operacionais_removidos"]} registro(s) operacionais e '
                    f'{result["registros_raw_removidos"]} registro(s) RAW. As tabelas foram preservadas.',
                )
                return redirect('administracao:base_detalhe', source_slug=source_slug)

    return render(
        request,
        'administracao/bases/limpar_dataset.html',
        {
            'spec': spec,
            'source_slug': source_slug,
            'storage': storage,
            'confirmation_token': confirmation_token,
        },
    )


@superadmin_required
def excluir_dados_fonte(request):
    if request.method != 'POST':
        return redirect('administracao:novo_lote_importacao')

    source_slug = str(request.POST.get('source_slug') or '').strip().lower()
    fonte = FONTE_SLUGS.get(source_slug)
    if not fonte or not datasets_for_source(source_slug):
        messages.error(request, 'Esta fonte ainda não possui tabelas técnicas implementadas para exclusão.')
        return redirect('administracao:novo_lote_importacao')

    if request.POST.get('confirmar_exclusao') != 'on':
        messages.error(request, 'A exclusão não foi executada porque a confirmação não foi marcada.')
        return redirect(f"{reverse('administracao:novo_lote_importacao')}?fonte={source_slug}")

    try:
        result = clear_source_data(source_slug, request.user)
    except Exception as exc:
        messages.error(request, f'A exclusão foi bloqueada e revertida pelo banco: {exc}')
    else:
        total = len(result['tabelas_operacionais']) + len(result['tabelas_raw'])
        messages.success(
            request,
            f'Dados atuais de {fonte.label} excluídos. {total} tabela(s) foram esvaziadas; '
            'estrutura, índices e histórico foram preservados.'
        )
    return redirect(f"{reverse('administracao:novo_lote_importacao')}?fonte={source_slug}")
