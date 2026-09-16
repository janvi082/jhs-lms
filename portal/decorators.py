from functools import wraps
from django.http import HttpResponseForbidden
from django.contrib import messages

def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_admin_user:
            messages.error(request, "Access restricted. You do not have permission to access the JHS Admin Portal.")
            return HttpResponseForbidden('Forbidden')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
