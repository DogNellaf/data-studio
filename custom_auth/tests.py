from django.contrib.auth.models import User
from django.test import TestCase


class LoginViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="secret123")

    def test_get_renders_form(self):
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login.html")

    def test_valid_login_redirects_to_profile(self):
        response = self.client.post(
            "/auth/login", {"login": "alice", "password": "secret123"}
        )
        self.assertRedirects(response, "/auth/profile", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_invalid_login_shows_error(self):
        response = self.client.post(
            "/auth/login", {"login": "alice", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.context["messages"]]
        self.assertIn("Неверный логин или пароль", msgs)
        self.assertNotIn("_auth_user_id", self.client.session)


class RegisterViewTests(TestCase):
    def test_get_renders_form(self):
        response = self.client.get("/auth/register")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "register.html")

    def test_successful_registration(self):
        response = self.client.post(
            "/auth/register",
            {"username": "bob", "password": "pw12345", "confirm_password": "pw12345"},
        )
        self.assertRedirects(response, "/auth/login", fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username="bob").exists())

    def test_empty_username(self):
        response = self.client.post(
            "/auth/register",
            {"username": "", "password": "pw12345", "confirm_password": "pw12345"},
        )
        self.assertIn(
            "Не указано имя пользователя",
            [str(m) for m in response.context["messages"]],
        )
        self.assertFalse(User.objects.exists())

    def test_empty_password(self):
        response = self.client.post(
            "/auth/register",
            {"username": "bob", "password": "", "confirm_password": ""},
        )
        self.assertIn("Не указан пароль", [str(m) for m in response.context["messages"]])
        self.assertFalse(User.objects.filter(username="bob").exists())

    def test_password_mismatch(self):
        response = self.client.post(
            "/auth/register",
            {"username": "bob", "password": "pw12345", "confirm_password": "other"},
        )
        self.assertIn("Пароли не совпадают", [str(m) for m in response.context["messages"]])
        self.assertFalse(User.objects.filter(username="bob").exists())

    def test_duplicate_username(self):
        User.objects.create_user("bob", password="pw12345")
        response = self.client.post(
            "/auth/register",
            {"username": "bob", "password": "pw12345", "confirm_password": "pw12345"},
        )
        self.assertIn(
            "Пользователь с таким логином уже существует",
            [str(m) for m in response.context["messages"]],
        )
        self.assertEqual(User.objects.filter(username="bob").count(), 1)


class ProfileViewTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user("alice", password=self.password)

    def login(self):
        self.assertTrue(self.client.login(username="alice", password=self.password))

    def test_requires_auth(self):
        self.assertRedirects(
            self.client.get("/auth/profile"), "/auth/login", fetch_redirect_response=False
        )

    def test_get_renders_form(self):
        self.login()
        response = self.client.get("/auth/profile")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "profile.html")

    def test_change_username_only(self):
        self.login()
        response = self.client.post(
            "/auth/profile",
            {"username": "alice2", "password": "", "confirm_password": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice2")
        # пароль не изменился
        self.assertTrue(self.user.check_password(self.password))

    def test_change_password_keeps_session(self):
        """Регрессия: после смены пароля пользователь не должен разлогиниваться."""
        self.login()
        response = self.client.post(
            "/auth/profile",
            {"username": "alice", "password": "newpass99", "confirm_password": "newpass99"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass99"))
        # последующий запрос всё ещё авторизован
        follow_up = self.client.get("/auth/profile")
        self.assertEqual(follow_up.status_code, 200)

    def test_duplicate_username_rejected(self):
        User.objects.create_user("taken", password="x")
        self.login()
        response = self.client.post(
            "/auth/profile",
            {"username": "taken", "password": "", "confirm_password": ""},
        )
        self.assertIn(
            "Пользователь с таким логином уже существует",
            [str(m) for m in response.context["messages"]],
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "alice")

    def test_password_mismatch_rejected(self):
        self.login()
        response = self.client.post(
            "/auth/profile",
            {"username": "alice", "password": "aaa11111", "confirm_password": "bbb22222"},
        )
        self.assertIn("Пароли не совпадают", [str(m) for m in response.context["messages"]])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_empty_username_rejected(self):
        self.login()
        response = self.client.post(
            "/auth/profile",
            {"username": "", "password": "", "confirm_password": ""},
        )
        self.assertIn(
            "Не указано имя пользователя",
            [str(m) for m in response.context["messages"]],
        )


class LogoutViewTests(TestCase):
    def test_logout_redirects_and_clears_session(self):
        User.objects.create_user("alice", password="secret123")
        self.client.login(username="alice", password="secret123")
        response = self.client.get("/auth/logout")
        self.assertRedirects(response, "/auth/login", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
