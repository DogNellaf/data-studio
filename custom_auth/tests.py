from django.contrib.auth.models import User
from django.test import TestCase, override_settings

STRONG_PASSWORD = "Backup-Studio-2026"


class LoginViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="secret123")

    def test_get_renders_form(self):
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login.html")

    def test_valid_login_redirects_to_backups(self):
        response = self.client.post("/auth/login", {"username": "alice", "password": "secret123"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_honours_safe_next(self):
        response = self.client.post(
            "/auth/login", {"username": "alice", "password": "secret123", "next": "/create"}
        )
        self.assertRedirects(response, "/create", fetch_redirect_response=False)

    def test_login_ignores_external_next(self):
        response = self.client.post(
            "/auth/login",
            {"username": "alice", "password": "secret123", "next": "https://evil.example"},
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_invalid_login_shows_error(self):
        response = self.client.post("/auth/login", {"username": "alice", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный логин или пароль")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_is_redirected(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/auth/login"), "/", fetch_redirect_response=False)

    @override_settings(DEMO_USERNAME="demo", DEMO_PASSWORD="demo-pass")
    def test_demo_credentials_hint(self):
        self.assertContains(self.client.get("/auth/login"), "demo-pass")

    def test_no_demo_hint_by_default(self):
        self.assertNotContains(self.client.get("/auth/login"), "Демо-доступ")


class RegisterViewTests(TestCase):
    def post(self, username="bob", password1=STRONG_PASSWORD, password2=STRONG_PASSWORD):
        return self.client.post(
            "/auth/register",
            {"username": username, "password1": password1, "password2": password2},
        )

    def test_get_renders_form(self):
        response = self.client.get("/auth/register")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "register.html")

    def test_successful_registration(self):
        response = self.post()
        self.assertRedirects(response, "/auth/login", fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username="bob").check_password(STRONG_PASSWORD))

    def test_empty_username(self):
        response = self.post(username="")
        self.assertIn("username", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_password_mismatch(self):
        response = self.post(password2="something-else-2026")
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_weak_password_rejected(self):
        """Регрессия: раньше пароль «1» проходил без проверок сложности."""
        response = self.post(password1="12345", password2="12345")
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_duplicate_username(self):
        User.objects.create_user("bob", password="x")
        response = self.post()
        self.assertIn("username", response.context["form"].errors)
        self.assertEqual(User.objects.filter(username="bob").count(), 1)


class ProfileViewTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user("alice", password=self.password)
        self.client.force_login(self.user)

    def post(self, username="alice", password1="", password2=""):
        return self.client.post(
            "/auth/profile",
            {"username": username, "new_password1": password1, "new_password2": password2},
        )

    def test_requires_auth(self):
        self.client.logout()
        self.assertRedirects(
            self.client.get("/auth/profile"),
            "/auth/login?next=/auth/profile",
            fetch_redirect_response=False,
        )

    def test_get_renders_form(self):
        response = self.client.get("/auth/profile")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "profile.html")

    def test_change_username_only(self):
        response = self.post(username="alice2")
        self.assertRedirects(response, "/auth/profile", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice2")
        self.assertTrue(self.user.check_password(self.password))

    def test_change_password_keeps_session(self):
        """Регрессия: после смены пароля пользователь не должен разлогиниваться."""
        self.post(password1=STRONG_PASSWORD, password2=STRONG_PASSWORD)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG_PASSWORD))
        self.assertEqual(self.client.get("/auth/profile").status_code, 200)

    def test_weak_password_rejected(self):
        response = self.post(password1="123", password2="123")
        self.assertIn("new_password1", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_password_mismatch_rejected(self):
        response = self.post(password1=STRONG_PASSWORD, password2="other-Password-1")
        self.assertIn("new_password2", response.context["form"].errors)

    def test_duplicate_username_rejected_without_leaking_into_header(self):
        User.objects.create_user("taken", password="x")
        response = self.post(username="taken")
        self.assertIn("username", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")
        # в шапке остаётся действующий логин, а не отклонённый ввод
        self.assertEqual(response.context["request"].user.username, "alice")

    def test_empty_username_rejected(self):
        response = self.post(username="")
        self.assertIn("username", response.context["form"].errors)


class LogoutViewTests(TestCase):
    def setUp(self):
        User.objects.create_user("alice", password="secret123")
        self.client.login(username="alice", password="secret123")

    def test_post_logs_out(self):
        response = self.client.post("/auth/logout")
        self.assertRedirects(response, "/auth/login", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_is_rejected(self):
        """Выход по GET позволял бы разлогинить пользователя чужой ссылкой."""
        self.assertEqual(self.client.get("/auth/logout").status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
