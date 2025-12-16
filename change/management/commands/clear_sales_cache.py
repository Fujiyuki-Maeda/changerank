from django.core.management.base import BaseCommand
from django.core.cache import cache


class Command(BaseCommand):
    help = 'Clear sales-related caches (salesrecord_all_dates and dashboard caches)'

    def handle(self, *args, **options):
        self.stdout.write('Clearing sales-related caches...')
        try:
            cache.delete('salesrecord_all_dates')
            # If you use more specific keys, delete them here.
            cache.clear()
            self.stdout.write(self.style.SUCCESS('Caches cleared.'))
        except Exception as e:
            self.stderr.write(f'Error clearing caches: {e}')