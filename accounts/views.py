from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect

class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated and user.is_admin_user:
            return '/portal/'
        return '/'

class CustomLogoutView(LogoutView):
    next_page = '/login/'
