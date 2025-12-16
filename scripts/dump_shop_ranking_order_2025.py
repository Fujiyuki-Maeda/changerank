from django.core.cache import cache
from change.views import get_all_dates_cached, get_descendant_category_ids
from change.models import Shop, SalesRecord, Category

cache.clear()
print('cache cleared')

all_dates = get_all_dates_cached()
years = sorted(list(set([d.year for d in all_dates])))
desired_year = 2025
selected_year = desired_year if desired_year in years else (years[-1] if years else None)
print('years', years, 'selected_year', selected_year)

# build year_data
year_data = {}
for year in years:
    latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
    if not latest_date:
        continue
    qs = SalesRecord.objects.filter(date=latest_date).exclude(category__code=9999)
    records = qs.values('shop__name', 'amount_sales')
    shop_totals = {}
    for d in records:
        shop_name = d['shop__name']; amount = d['amount_sales']
        if shop_name == '加治': shop_name = '加治木'
        shop_totals[shop_name] = shop_totals.get(shop_name, 0) + amount
    sorted_shops = sorted(shop_totals.items(), key=lambda x: x[1], reverse=True)
    year_info = {name: {'rank': rank} for rank, (name, _) in enumerate(sorted_shops, 1)}
    year_data[year] = year_info

all_shops = Shop.objects.all().order_by('name')
rows = []
for shop in all_shops:
    if shop.name == '加治': continue
    name = shop.name
    # determine rank for selected_year
    rank = year_data.get(selected_year, {}).get(name, {}).get('rank')
    rows.append((name, rank if rank else 999))

rows_sorted = sorted(rows, key=lambda x: x[1])
print('first 40 sorted by selected_year:')
for r in rows_sorted[:40]:
    print(r)
