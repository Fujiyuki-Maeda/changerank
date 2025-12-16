import datetime
from django.core.cache import cache
from change.models import Shop, Category, SalesRecord
from django.core.cache import cache

print('Starting test import script')

# Create or get test shop and category
shop, _ = Shop.objects.get_or_create(name='IMPORT_TEST_STORE')
category, _ = Category.objects.get_or_create(code=9999, level=10, defaults={'name':'客数'})

report_date = datetime.date(2026, 1, 15)

# Create a SalesRecord for 2026
sr = SalesRecord.objects.create(
    shop=shop,
    category=category,
    date=report_date,
    amount_sales=12345,
    amount_profit=1000,
    amount_purchase=2000,
    amount_supply=0,
    amount_net=12345,
)

print(f'Inserted SalesRecord id={sr.id} date={sr.date} shop={shop.name}')

# Clear cache directly (pre-automation behavior)
cache.clear()
print('Cleared cache')

# Check cache key removal (simple check)
if cache.get('salesrecord_all_dates') is None:
    print('salesrecord_all_dates cache is cleared or not set')
else:
    print('salesrecord_all_dates cache still present')

print('Test script finished')
