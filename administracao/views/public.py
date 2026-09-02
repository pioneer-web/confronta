from django.shortcuts import render

def public_root(request):
    return render(request,'administracao/public_root.html')
