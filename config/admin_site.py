from django.contrib.admin import AdminSite

class SuperUserAdminSite(AdminSite):
    """AdminSite that only allows superusers to access the Django admin UI.
    Unauthenticated users are redirected to the login page (standard Django behavior).
    Authenticated non‑superusers receive a 403 Forbidden response.
    """
    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser

# Instance used in urls
super_admin_site = SuperUserAdminSite(name='superadmin')
