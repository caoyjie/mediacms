from django.test import TestCase

from users.models import User


class SessionVersionTest(TestCase):
    def test_password_change_increments_session_version(self) -> None:
        user = User.objects.create_user(username="alice", password="old-password")
        self.assertEqual(user.session_version, 1)

        user.set_password("new-password")
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.session_version, 2)

    def test_disabling_user_increments_session_version(self) -> None:
        user = User.objects.create_user(username="alice", password="password")

        user.is_active = False
        user.save(update_fields=["is_active"])

        user.refresh_from_db()
        self.assertEqual(user.session_version, 2)
