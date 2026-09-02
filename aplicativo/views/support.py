from django.shortcuts import render

from aplicativo.permissions import cliente_required


@cliente_required
def ajuda_view(request):
    """Central de instruções de uso do Módulo 2."""
    return render(request, 'aplicativo/ajuda.html', {
        'acesso_aplicativo': request.acesso_aplicativo,
    })
