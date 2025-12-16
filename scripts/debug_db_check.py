from change.models import SalesRecord, Shop, Category
from django.db.models import Sum
from change.views import get_all_dates_cached, get_descendant_category_ids

print('SalesRecord count:', SalesRecord.objects.count())
all_dates = get_all_dates_cached()
print('Cached all_dates years:', sorted(list({d.year for d in all_dates})))

years = sorted(list(set([d.year for d in all_dates])))
print('Years list:', years)

latest_per_year = {}
for y in years:
    latest = SalesRecord.objects.filter(date__year=y).order_by('-date').values_list('date', flat=True).first()
    latest_per_year[y] = latest
print('latest_per_year:', latest_per_year)

hyuga = Shop.objects.filter(name__contains='日向').first()
print('hyuga:', hyuga and hyuga.id, hyuga and hyuga.name)

if hyuga:
    for y,d in latest_per_year.items():
        if d:
            s = SalesRecord.objects.filter(date=d, shop=hyuga).aggregate(sales=Sum('amount_sales'))['sales']
            print(f'hyuga {y} sales on {d}:', s)

# sample a 10-dept
l10 = Category.objects.filter(level=10).exclude(code=9999).order_by('code').first()
print('sample l10:', l10 and l10.code, l10 and l10.name)
if l10:
    ids = get_descendant_category_ids(l10.code)
    print('desc count for', l10.code, len(ids))
    for y,d in latest_per_year.items():
        if d:
            s = SalesRecord.objects.filter(date=d, category__id__in=ids).aggregate(sales=Sum('amount_sales'))['sales']
            print(f'dept {l10.code} total on {d}:', s)

# Show a few SalesRecord samples
print('Sample records (5):')
for r in SalesRecord.objects.all().order_by('-date')[:5]:
    print(r.date, r.shop.name, r.category.code if r.category else None, r.amount_sales)
