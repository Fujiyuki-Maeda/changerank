from django.core.cache import cache
from change.views import get_all_dates_cached, build_display_groups
from change.models import Shop, SalesRecord

cache.clear()
print('cache cleared')

all_dates = get_all_dates_cached()
years = sorted(list(set([d.year for d in all_dates])))
desired_year = 2026
selected_year = desired_year if desired_year in years else (years[-1] if years else None)
print('years', years, 'selected_year', selected_year)

all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE']).order_by('name')
# use the same grouping helper as the view
display_map, all_shops_display, _, _ = build_display_groups(all_shops, [])

# build year_data aggregated by display name (display_map keys)
year_data = {}
for year in years:
    latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
    if not latest_date:
        continue
    qs = SalesRecord.objects.filter(date=latest_date).exclude(category__code=9999)
    records = qs.values('shop__id', 'amount_sales')
    # map shop_id -> amount
    shop_totals = {}
    for d in records:
        sid = d['shop__id']; amount = d['amount_sales']
        shop_totals[sid] = shop_totals.get(sid, 0) + amount
    # aggregate per display name
    disp_totals = {}
    for disp, ids in display_map.items():
        total = sum(shop_totals.get(sid, 0) for sid in ids)
        if total > 0:
            disp_totals[disp] = total
    sorted_shops = sorted(disp_totals.items(), key=lambda x: x[1], reverse=True)
    year_info = {name: {'rank': rank} for rank, (name, _) in enumerate(sorted_shops, 1)}
    year_data[year] = year_info

# now build rows per display_map key
rows = []
for disp, ids in display_map.items():
    rank = year_data.get(selected_year, {}).get(disp, {}).get('rank')
    rows.append((disp, rank if rank else 999))

rows_sorted = sorted(rows, key=lambda x: x[1])
print('first 80 sorted by selected_year:')
for r in rows_sorted[:80]:
    print(r)
