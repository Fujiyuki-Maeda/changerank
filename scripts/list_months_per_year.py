from change.views import get_all_dates_cached
from change.models import SalesRecord

years = sorted(list({d.year for d in get_all_dates_cached()}))
print('years', years)
for y in years:
    months = sorted(list(set(SalesRecord.objects.filter(date__year=y).values_list('date__month', flat=True))))
    print(y, months)
