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

all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE'])
all_shops = all_shops.order_by('name')
display_map, all_shops_display, _, _ = build_display_groups(all_shops, [])

# build year_data aggregated by display name (display_map keys)
year_data = {}
for year in years:
    latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
    if not latest_date:
        continue
    qs = SalesRecord.objects.filter(date=latest_date).exclude(category__code=9999)
    records = qs.values('shop__id', 'amount_sales')
    shop_totals = {}
    for d in records:
        sid = d['shop__id']; amount = d['amount_sales']
        shop_totals[sid] = shop_totals.get(sid, 0) + amount
    disp_totals = {}
    for disp, ids in display_map.items():
        total = sum(shop_totals.get(sid, 0) for sid in ids)
        if total > 0:
            disp_totals[disp] = total
    sorted_shops = sorted(disp_totals.items(), key=lambda x: x[1], reverse=True)
    year_info = {name: {'rank': rank} for rank, (name, _) in enumerate(sorted_shops, 1)}
    year_data[year] = year_info

# build table_data rows (with cells)
rows = []
for disp, ids in display_map.items():
    # build cells
    cells = []
    has_data = False
    for year in years:
        data = year_data.get(year, {}).get(disp)
        rank = data['rank'] if data else None
        # compute diff with previous year
        # simplified: only rank
        cells.append({'year': year, 'rank': rank})
        if rank: has_data = True
    if has_data:
        rows.append({'name': disp, 'cells': cells})

# determine sort_year heuristics same as view
total_groups = len(display_map)
counts = {y: len(year_data.get(y, {})) for y in years}
max_year = max(counts.items(), key=lambda kv: kv[1])[0] if counts else selected_year
sort_year = selected_year
if counts.get(selected_year, 0) < max(1, int(total_groups * 0.5)):
    sort_year = max_year

def effective_rank_for_display(name):
    r = year_data.get(sort_year, {}).get(name, {}).get('rank')
    if r:
        return r
    for y in sorted(years, reverse=True):
        r2 = year_data.get(y, {}).get(name, {}).get('rank')
        if r2:
            return r2
    return 999

def is_closed_now(row):
    if not row.get('cells'): return False
    last_cell = row['cells'][-1]
    last_rank = last_cell.get('rank')
    earlier_has = any(c.get('rank') for c in row['cells'][:-1])
    return (last_rank is None) and bool(earlier_has)

rows_sorted = sorted(rows, key=lambda x: (is_closed_now(x), effective_rank_for_display(x['name'])))
print('first 80 sorted with closed-last:')
for r in rows_sorted[:80]:
    print(r['name'], 'closed' if is_closed_now(r) else '', effective_rank_for_display(r['name']))
