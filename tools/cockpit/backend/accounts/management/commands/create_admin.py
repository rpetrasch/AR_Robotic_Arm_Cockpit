import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from accounts import users


class Command(BaseCommand):
    help = (
        "Create a cockpit user (there are no roles: every user can do everything). The password "
        "comes from --password, the environment variable COCKPIT_ADMIN_PASSWORD, or a prompt."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", default=None)

    def handle(self, *args, username, password, **options):
        password = password or os.environ.get("COCKPIT_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass(f"Password for {username}: ")
            if password != getpass.getpass("Again: "):
                raise CommandError("passwords differ")
        try:
            users.create_user(username, password, actor="system", via="cli")
        except users.UserError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"user {username!r} created")
