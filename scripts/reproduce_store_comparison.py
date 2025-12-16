from django.core.cache import cache
from change.views import get_all_dates_cached, build_display_groups, get_descendant_ids_for_category
from change.models import Shop, SalesRecord, Category
from django.db.models import Sum

cache.clear()
print('cache cleared')

all_dates = get_all_dates_cached()
years = sorted(list(set([d.year for d in all_dates])), reverse=True)
selected_year = years[0]
print('selected_year', selected_year)

all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE']).order_by('name')
display_map, all_shops_display, _, _ = build_display_groups(all_shops, [])
print('all_shops_display sample:', all_shops_display[:5])

# pick first 3 display tokens
tokens = [s['value'] for s in all_shops_display[:3]]
print('sample tokens:', tokens)

# expand tokens to ids
ids = []
for t in tokens:
    for p in t.split('|'):
        if p.isdigit(): ids.append(int(p))
print('ids sample:', ids)

qs = SalesRecord.objects.filter(date__year=selected_year, shop_id__in=ids)
print('records count for sample ids:', qs.count())

# aggregate by 10-dept via get_descendant_ids_for_category
l10s = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
cat_map = {}
for l10 in l10s:
    for d in get_descendant_ids_for_category(l10.code, 10):
        cat_map[d] = l10.name

records = qs.values('shop_id','shop__name','category_id').annotate(total_sales=Sum('amount_sales'))
print('aggregated records sample:', list(records)[:5])

# map to l10 names and sum
per_shop = {}
for r in records:
    cid = r['category_id']
    root = cat_map.get(cid)
    if not root: continue
    sid = r['shop_id']
    per_shop.setdefault(sid, {'name': r['shop__name'], 'total':0, 'depts':{}})
    per_shop[sid]['total'] += r['total_sales'] or 0
    per_shop[sid]['depts'][root] = per_shop[sid]['depts'].get(root,0) + (r['total_sales'] or 0)

print('per_shop sample:')
for k,v in per_shop.items():
    print(k, v['name'], 'total', v['total'])
