import getpass
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users.models import SiteAdministrator, User


class Command(BaseCommand):
    help = "Create or update the singleton MediaCMS site administrator."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--no-input", action="store_false", dest="interactive")
        parser.add_argument("--rebind", action="store_true")

    def handle(self, *args, **options):
        password = os.environ.get("MEDIACMS_ADMIN_PASSWORD")
        if not password:
            if not options["interactive"]:
                raise CommandError(
                    "MEDIACMS_ADMIN_PASSWORD is required when --no-input is used"
                )
            password = self._prompt_password()

        with transaction.atomic():
            binding = (
                SiteAdministrator.objects.select_for_update()
                .select_related("user")
                .filter(singleton_key="default")
                .first()
            )
            rebind_required = binding is not None and binding.user.username != options["username"]
            if rebind_required and not options["rebind"]:
                raise CommandError(
                    "A different site administrator is already bound; use --rebind explicitly"
                )

            user, _ = User.objects.select_for_update().get_or_create(
                username=options["username"],
                defaults={"email": options["email"]},
            )
            user.email = options["email"]
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            user.is_approved = True
            user.set_password(password)
            user.save()

            if binding is None:
                SiteAdministrator.objects.create(user=user)
            elif binding.user_id != user.pk:
                binding.user = user
                binding.save(update_fields=("user", "updated_at"))

            User.objects.exclude(pk=user.pk).update(
                is_active=False,
                is_staff=False,
                is_superuser=False,
                is_approved=False,
            )

        self.stdout.write(self.style.SUCCESS("Site administrator initialized."))

    def _prompt_password(self):
        password = getpass.getpass("Administrator password: ")
        confirmation = getpass.getpass("Administrator password (again): ")
        if not password:
            raise CommandError("Administrator password cannot be empty")
        if password != confirmation:
            raise CommandError("Administrator passwords do not match")
        return password
